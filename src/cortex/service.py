"""Cortex 5 record-KB operations."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DEFAULT_LAYOUT, DEFAULT_TAGS, RECORD_FIELDS, RECORD_SCHEMA, VERSION
from .errors import CortexError, Status, io_error, usage_error, validation_error
from .jsonio import json_bytes, loads_object, read_json_operand
from .locking import workspace_lock
from .naming import suffixed_name, title_slug
from .native import (
    checked_scandir,
    copy_conversion,
    copy_regular,
    exists,
    inspect_conversion,
    native_path,
    reject_reparse_ancestry,
    remove_tree_best_effort,
    rename_no_replace,
    require_real_directory,
    require_regular_file,
    require_safe_component,
)
from .profiles import require_valid_profile, validate_record
from .validation import ValidationReport, validate_record_directory, validate_workspace


@dataclass
class Outcome:
    status: Status = Status.OK
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)


def _raise_invalid_report(report: ValidationReport) -> None:
    if report.issues:
        raise CortexError(
            "Workspace validation failed",
            status=Status.VALIDATION_ERROR,
            code="workspace_invalid",
            details={"issues": report.issues},
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _atomic_replace_json(path: Path, value: dict[str, Any], purpose: str) -> None:
    temporary = path.parent / f".cortex-{purpose}-{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        with temporary.open("xb") as stream:
            temporary_created = True
            stream.write(json_bytes(value))
            stream.flush()
        os.replace(native_path(temporary), native_path(path))
    except OSError as exc:
        if temporary_created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise io_error("Owned JSON file could not be replaced", "replace_failed", path=str(path), os_error=str(exc)) from exc


def _read_operand_again(operand: str, cached: dict[str, Any]) -> dict[str, Any]:
    if operand == "-":
        return cached
    return read_json_operand(operand)[0]


def _precheck_metadata_shape(value: dict[str, Any]) -> None:
    allowed = set(RECORD_FIELDS)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise validation_error("Metadata contains unknown fields", "unknown_field", fields=unknown)
    missing = sorted({"title", "tags"} - set(value))
    if missing:
        raise validation_error("Metadata is missing required fields", "missing_field", fields=missing)


def _complete_metadata(
    value: dict[str, Any],
    registered: set[str],
) -> dict[str, Any]:
    _precheck_metadata_shape(value)
    candidate = {
        "title": value.get("title"),
        "timestamp": value.get("timestamp", _now()),
        "tags": value.get("tags"),
    }
    problems = validate_record(candidate, registered, label="metadata")
    if problems:
        first = problems[0]
        raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
    return candidate


def _safe_source(source_text: str) -> Path:
    if source_text == "-":
        raise usage_error("--source does not support stdin", "source_stdin_unsupported")
    source = Path(os.path.abspath(source_text))
    require_regular_file(source, code="source_not_ordinary")
    require_safe_component(source.name, label=source.name)
    return source


def _safe_conversion(conversion_text: str | None) -> tuple[Path | None, list[tuple[str, Path, bool]] | None]:
    if conversion_text is None:
        return None, None
    if conversion_text == "-":
        raise usage_error("--conversion does not support stdin", "conversion_stdin_unsupported")
    conversion = Path(os.path.abspath(conversion_text))
    _, entries = inspect_conversion(conversion)
    return conversion, entries


def _require_initialized_lock_target(workspace: Path) -> None:
    target = workspace / "profiles" / "record-schema.json"
    if not exists(target):
        raise validation_error("Workspace is not initialized", "workspace_not_initialized", path=str(workspace))
    require_regular_file(target, code="invalid_lock_target")


class CortexService:
    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(os.path.abspath(workspace))

    def init(self) -> Outcome:
        root = self.workspace
        if exists(root):
            require_real_directory(root, code="workspace_not_directory")
            if checked_scandir(root):
                raise validation_error("Workspace must be absent or exactly empty", "workspace_not_empty", path=str(root))
        else:
            parent = root.parent
            require_real_directory(parent, code="workspace_parent_not_directory")
            reject_reparse_ancestry(parent)
            try:
                root.mkdir(exist_ok=False)
            except OSError as exc:
                raise io_error("Workspace directory could not be created", "init_failed", path=str(root), os_error=str(exc)) from exc
        try:
            profiles = root / "profiles"
            records = root / DEFAULT_LAYOUT["records_root"]
            profiles.mkdir(exist_ok=False)
            records.mkdir(exist_ok=False)
            for name, value in (
                ("record-schema.json", RECORD_SCHEMA),
                ("tags.json", DEFAULT_TAGS),
                ("layout.json", DEFAULT_LAYOUT),
            ):
                with (profiles / name).open("xb") as stream:
                    stream.write(json_bytes(value))
                    stream.flush()
        except OSError as exc:
            raise io_error("Workspace profiles could not be initialized", "init_failed", path=str(root), os_error=str(exc)) from exc
        return Outcome(data={"version": VERSION, "records_root": DEFAULT_LAYOUT["records_root"]})

    def status(self) -> Outcome:
        report = validate_workspace(self.workspace)
        return Outcome(data={"version": VERSION, "valid": report.valid, "count": report.count})

    def validate(self) -> Outcome:
        report = validate_workspace(self.workspace)
        data = {"version": VERSION, "valid": report.valid, "count": report.count}
        if report.valid:
            return Outcome(data=data)
        return Outcome(status=Status.VALIDATION_ERROR, data=data, issues=report.issues)

    def config_show(self, profile: str) -> Outcome:
        report = validate_workspace(self.workspace)
        _raise_invalid_report(report)
        value = report.tags if profile == "tags" else report.layout
        assert value is not None
        return Outcome(data={"profile": profile, "value": value})

    def config_set(self, profile: str, operand: str) -> Outcome:
        value, _ = read_json_operand(operand)
        require_valid_profile(profile, value)
        _require_initialized_lock_target(self.workspace)
        with workspace_lock(self.workspace) as lock_stream:
            lock_stream.seek(0)
            report = validate_workspace(self.workspace, locked_record_schema=lock_stream.read())
            _raise_invalid_report(report)
            value = _read_operand_again(operand, value)
            require_valid_profile(profile, value)
            if profile == "tags":
                return self._set_tags(value, report)
            return self._set_layout(value, report)

    def _set_tags(self, value: dict[str, Any], report: ValidationReport) -> Outcome:
        assert report.layout is not None
        registered = {item["tag"] for item in value["tags"]}
        orphaned: list[dict[str, Any]] = []
        records_root = self.workspace / report.layout["records_root"]
        for entry in checked_scandir(records_root):
            record = loads_object((Path(entry.path) / "record.json").read_bytes(), label=f"{report.layout['records_root']}/{entry.name}/record.json")
            missing = [tag for tag in record["tags"] if tag not in registered]
            if missing:
                orphaned.append({"record": entry.name, "tags": missing})
        if orphaned:
            raise validation_error("Tag replacement would orphan existing record references", "orphaned_tag_reference", records=orphaned)
        _atomic_replace_json(self.workspace / "profiles" / "tags.json", value, "tags")
        return Outcome(data={"profile": "tags", "value": value})

    def _set_layout(self, value: dict[str, Any], report: ValidationReport) -> Outcome:
        assert report.layout is not None
        old = report.layout
        old_name = old["records_root"]
        new_name = value["records_root"]
        old_root = self.workspace / old_name
        maximum = value["max_component_length"]
        folders = [entry.name for entry in checked_scandir(old_root)]
        too_long = [name for name in folders if len(name.encode("utf-8")) > maximum]
        if too_long:
            raise validation_error("Existing record folders exceed the new component limit", "existing_record_folder_too_long", folders=too_long)
        profile_path = self.workspace / "profiles" / "layout.json"
        if new_name == old_name:
            _atomic_replace_json(profile_path, value, "layout")
            return Outcome(data={"profile": "layout", "value": value})
        if folders:
            raise validation_error("records_root can change only while the current root is exactly empty", "records_root_not_empty", path=old_name)
        new_root = self.workspace / new_name
        if exists(new_root):
            raise validation_error("New records_root already exists", "records_root_exists", path=new_name)
        rename_no_replace(old_root, new_root)
        try:
            _atomic_replace_json(profile_path, value, "layout")
        except CortexError:
            try:
                if not exists(old_root):
                    os.rename(native_path(new_root), native_path(old_root))
            except OSError:
                pass
            raise
        return Outcome(data={"profile": "layout", "value": value})

    def record_add(self, source_operand: str, conversion_operand: str | None, metadata_operand: str) -> Outcome:
        metadata, _ = read_json_operand(metadata_operand)
        _precheck_metadata_shape(metadata)
        _safe_source(source_operand)
        _safe_conversion(conversion_operand)
        _require_initialized_lock_target(self.workspace)
        with workspace_lock(self.workspace) as lock_stream:
            lock_stream.seek(0)
            report = validate_workspace(self.workspace, locked_record_schema=lock_stream.read())
            _raise_invalid_report(report)
            assert report.tags is not None and report.layout is not None
            metadata = _read_operand_again(metadata_operand, metadata)
            registered = {item["tag"] for item in report.tags["tags"]}
            record = _complete_metadata(metadata, registered)
            source = _safe_source(source_operand)
            _, conversion_entries = _safe_conversion(conversion_operand)

            layout = report.layout
            records_root = self.workspace / layout["records_root"]
            base = title_slug(record["title"], layout["max_component_length"])
            existing = {entry.name.casefold() for entry in checked_scandir(records_root)}
            folder = base
            if folder.casefold() in existing:
                if layout["duplicate_name_strategy"] == "reject":
                    raise validation_error("Record folder already exists", "duplicate_record_name", path=folder)
                number = 2
                while True:
                    candidate = suffixed_name(base, number, layout["max_component_length"])
                    if candidate.casefold() not in existing:
                        folder = candidate
                        break
                    number += 1

            temporary = records_root / f".cortex-add-{uuid.uuid4().hex}"
            destination = records_root / folder
            temporary_created = False
            try:
                temporary.mkdir(exist_ok=False)
                temporary_created = True
                with (temporary / "record.json").open("xb") as stream:
                    stream.write(json_bytes(record))
                    stream.flush()
                original = temporary / "original"
                original.mkdir(exist_ok=False)
                copy_regular(source, original / source.name)
                if conversion_entries is not None:
                    representations = temporary / "representations"
                    representations.mkdir(exist_ok=False)
                    copy_conversion(conversion_entries, representations / "markdown-conversion")
                problems = validate_record_directory(
                    temporary,
                    folder,
                    registered=registered,
                    maximum=layout["max_component_length"],
                    label_root=layout["records_root"],
                    check_folder=False,
                )
                if problems:
                    first = problems[0]
                    raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
                rename_no_replace(temporary, destination)
            except CortexError:
                if temporary_created:
                    remove_tree_best_effort(temporary)
                raise
            except OSError as exc:
                if temporary_created:
                    remove_tree_best_effort(temporary)
                raise io_error("Record could not be staged", "record_stage_failed", path=str(destination), os_error=str(exc)) from exc
            return Outcome(data={"record": folder, "path": f"{layout['records_root']}/{folder}"})

    def record_edit(self, folder: str, metadata_operand: str) -> Outcome:
        require_safe_component(folder, label=folder)
        metadata, _ = read_json_operand(metadata_operand)
        _precheck_metadata_shape(metadata)
        _require_initialized_lock_target(self.workspace)
        with workspace_lock(self.workspace) as lock_stream:
            lock_stream.seek(0)
            report = validate_workspace(self.workspace, locked_record_schema=lock_stream.read())
            _raise_invalid_report(report)
            assert report.tags is not None and report.layout is not None
            metadata = _read_operand_again(metadata_operand, metadata)
            _precheck_metadata_shape(metadata)
            records_root = self.workspace / report.layout["records_root"]
            matches = [entry for entry in checked_scandir(records_root) if entry.name == folder]
            if not matches:
                raise validation_error("Record folder does not exist", "record_not_found", path=folder)
            record_path = Path(matches[0].path) / "record.json"
            registered = {item["tag"] for item in report.tags["tags"]}
            record = _complete_metadata(metadata, registered)
            _atomic_replace_json(record_path, record, "edit")
            return Outcome(data={"record": folder, "path": f"{report.layout['records_root']}/{folder}"})


__all__ = ["CortexService", "Outcome"]
