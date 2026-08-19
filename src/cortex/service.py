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
from .jsonio import json_bytes, read_json_operand
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
    rename_no_replace,
    require_real_directory,
    require_regular_file,
    require_safe_component,
)
from .profiles import registered_tags, require_valid_profile, tag_groups, validate_record
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
    try:
        payload = json_bytes(value)
    except UnicodeEncodeError as exc:
        raise validation_error("Owned JSON contains text that is not valid UTF-8", "invalid_utf8_text", path=str(path)) from exc
    temporary = path.parent / f".cortex-{purpose}-{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        with temporary.open("xb") as stream:
            temporary_created = True
            stream.write(payload)
            stream.flush()
        os.replace(native_path(temporary), native_path(path))
    except OSError as exc:
        if temporary_created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise io_error(
                    "Owned temporary file could not be cleaned",
                    "cleanup_failed",
                    path=str(temporary),
                    os_error=str(cleanup_exc),
                ) from cleanup_exc
        raise io_error("Owned JSON file could not be replaced", "replace_failed", path=str(path), os_error=str(exc)) from exc


def _cleanup_staged_directory(path: Path) -> None:
    if not exists(path):
        return
    import shutil

    try:
        shutil.rmtree(native_path(path))
    except OSError as exc:
        raise io_error("Owned staged directory could not be cleaned", "cleanup_failed", path=str(path), os_error=str(exc)) from exc


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


def _partition_for_record(record: dict[str, Any], tags: dict[str, Any], layout: dict[str, Any]) -> tuple[str, set[str]]:
    partition_by = layout["partition_by"]
    if partition_by is None:
        raise validation_error("Bundle must be configured before records can be added", "bundle_not_operational")
    groups = tag_groups(tags)
    if partition_by not in groups:
        raise validation_error("partition_by does not name an existing tag group", "unknown_partition_group", group=partition_by)
    partition_tags = {item["tag"] for item in groups[partition_by]}
    selected = [tag for tag in record["tags"] if tag in partition_tags]
    if len(selected) != 1:
        raise validation_error(
            "Record must contain exactly one tag from the configured partition group",
            "partition_tag_count",
            tags=selected,
        )
    return selected[0], partition_tags


def _record_operand(value: str) -> tuple[str, str]:
    if "\\" in value or value.startswith("/"):
        raise validation_error("Record operand must be a relative two-component POSIX path", "invalid_record_operand", path=value)
    parts = value.split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        raise validation_error("Record operand must be a relative two-component POSIX path", "invalid_record_operand", path=value)
    for part in parts:
        require_safe_component(part, label=value)
    return parts[0], parts[1]


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
            profiles.mkdir(exist_ok=False)
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
        return Outcome(data={"version": VERSION, "partition_by": None})

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
            locked_schema = lock_stream.read()
            report = validate_workspace(self.workspace, locked_record_schema=locked_schema)
            _raise_invalid_report(report)
            value = _read_operand_again(operand, value)
            require_valid_profile(profile, value)
            candidate_report = validate_workspace(
                self.workspace,
                locked_record_schema=locked_schema,
                tags_override=value if profile == "tags" else None,
                layout_override=value if profile == "layout" else None,
            )
            _raise_invalid_report(candidate_report)
            return self._set_profile(profile, value)

    def _set_profile(self, profile: str, value: dict[str, Any]) -> Outcome:
        _atomic_replace_json(self.workspace / "profiles" / f"{profile}.json", value, profile)
        return Outcome(data={"profile": profile, "value": value})

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
            registered = registered_tags(report.tags)
            record = _complete_metadata(metadata, registered)
            partition, partition_tags = _partition_for_record(record, report.tags, report.layout)
            source = _safe_source(source_operand)
            _, conversion_entries = _safe_conversion(conversion_operand)

            layout = report.layout
            base = title_slug(record["title"], layout["max_component_length"])
            partition_root = self.workspace / partition
            partition_exists = exists(partition_root)
            existing = {entry.name.casefold() for entry in checked_scandir(partition_root)} if partition_exists else set()
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

            if partition_exists:
                temporary = partition_root / f".cortex-add-{uuid.uuid4().hex}"
                destination = partition_root / folder
                staged_unit = temporary
            else:
                temporary = self.workspace / f".cortex-add-{uuid.uuid4().hex}"
                destination = partition_root
                staged_unit = temporary / folder
            temporary_created = False
            try:
                temporary.mkdir(exist_ok=False)
                temporary_created = True
                if not partition_exists:
                    staged_unit.mkdir(exist_ok=False)
                with (staged_unit / "record.json").open("xb") as stream:
                    stream.write(json_bytes(record))
                    stream.flush()
                original = staged_unit / "original"
                original.mkdir(exist_ok=False)
                copy_regular(source, original / source.name)
                if conversion_entries is not None:
                    representations = staged_unit / "representations"
                    representations.mkdir(exist_ok=False)
                    copy_conversion(conversion_entries, representations / "markdown-conversion")
                problems = validate_record_directory(
                    staged_unit,
                    folder,
                    registered=registered,
                    maximum=layout["max_component_length"],
                    label_root=partition,
                    partition_tags=partition_tags,
                    expected_partition=partition,
                    check_folder=False,
                )
                if problems:
                    first = problems[0]
                    raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
                rename_no_replace(temporary, destination)
            except CortexError:
                if temporary_created:
                    _cleanup_staged_directory(temporary)
                raise
            except OSError as exc:
                if temporary_created:
                    _cleanup_staged_directory(temporary)
                raise io_error("Record could not be staged", "record_stage_failed", path=str(destination), os_error=str(exc)) from exc
            operand = f"{partition}/{folder}"
            return Outcome(data={"record": operand, "path": operand})

    def record_edit(self, folder: str, metadata_operand: str) -> Outcome:
        partition, unit = _record_operand(folder)
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
            unit_path = self.workspace / partition / unit
            if not exists(unit_path):
                raise validation_error("Record folder does not exist", "record_not_found", path=folder)
            record_path = unit_path / "record.json"
            registered = registered_tags(report.tags)
            record = _complete_metadata(metadata, registered)
            selected_partition, _ = _partition_for_record(record, report.tags, report.layout)
            if selected_partition != partition:
                raise validation_error("Record edits cannot change the partition tag", "partition_change_forbidden", path=folder)
            _atomic_replace_json(record_path, record, "edit")
            return Outcome(data={"record": folder, "path": folder})


__all__ = ["CortexService", "Outcome"]
