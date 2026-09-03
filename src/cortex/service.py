"""Cortex 8 record-KB operations."""

from __future__ import annotations

import os
import hashlib
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .constants import DEFAULT_LAYOUT, DEFAULT_TAGS, RECORD_FIELDS, RECORD_SCHEMA, REGISTRY_FILENAME, ROOT_LOCK_FILENAME, VERSION
from .core_runner import CoreRunner, require_core
from .errors import CortexError, Status, io_error, usage_error, validation_error
from .jsonio import json_bytes, loads_object, read_json_operand
from .locking import writer_lock, workspace_lock_path
from .naming import tag_title_date_name
from .native import (
    checked_scandir,
    copy_conversion,
    copy_regular,
    exists,
    inspect_conversion,
    is_reparse_metadata,
    native_path,
    reject_reparse_ancestry,
    rename_no_replace,
    require_real_directory,
    require_regular_file,
    require_safe_component,
)
from .profiles import registered_tags, require_valid_profile, tag_groups, validate_record
from .tree import inventory_unit
from .registry import canonical_registry, require_registry, resolve_bundle, validate_registry, validate_registry_value, validate_transition
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
    *,
    auto_timestamp: bool = True,
) -> dict[str, Any]:
    _precheck_metadata_shape(value)
    candidate = {
        "title": value.get("title"),
        "timestamp": value.get("timestamp", _now() if auto_timestamp else None),
        "tags": value.get("tags"),
    }
    problems = validate_record(candidate, registered, label="metadata")
    if problems:
        first = problems[0]
        raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
    return candidate


def _naming_tag_for_record(record: dict[str, Any], tags: dict[str, Any], layout: dict[str, Any]) -> tuple[str, set[str]]:
    group_name = layout["partition_tag_group"]
    if group_name is None:
        raise validation_error("Bundle must be configured before records can be added", "bundle_not_operational")
    groups = tag_groups(tags)
    if group_name not in groups:
        raise validation_error("partition_tag_group does not name an existing tag group", "unknown_partition_tag_group", group=group_name)
    naming_tags = {item["tag"] for item in groups[group_name]}
    selected = [tag for tag in record["tags"] if tag in naming_tags]
    if len(selected) != 1:
        raise validation_error(
            "Record must contain exactly one tag from the configured partition group",
            "partition_tag_count",
            tags=selected,
        )
    return selected[0], naming_tags


def _record_operand(value: str) -> str:
    if "\\" in value or "/" in value or value in {"", ".", ".."}:
        raise validation_error("Record operand must be one exact safe component", "invalid_record_operand", path=value)
    require_safe_component(value, allow_profiles=False, label=value)
    return value


def _partition_operand(value: str) -> str:
    if "\\" in value or "/" in value or value in {"", ".", ".."}:
        raise validation_error("Partition operand must be one exact safe component", "invalid_partition_operand", path=value)
    require_safe_component(value, allow_profiles=False, label=value)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _residue_count(root: Path) -> int:
    count = 1
    def visit(directory: Path) -> None:
        nonlocal count
        for entry in os.scandir(native_path(directory)):
            count += 1
            meta = entry.stat(follow_symlinks=False)
            if is_reparse_metadata(meta):
                continue
            if stat.S_ISDIR(meta.st_mode):
                visit(Path(entry.path))
    visit(root)
    return count


def _safe_source(source_text: str | None) -> Path | None:
    if source_text is None:
        return None
    if source_text == "-":
        raise usage_error("--source does not support stdin", "source_stdin_unsupported")
    source = Path(os.path.abspath(source_text))
    require_regular_file(source, code="source_not_ordinary")
    require_safe_component(source.name, label=source.name)
    if source.name.casefold() in {
        "record.json",
        "agents.md",
        "agents.override.md",
        "claude.md",
        "claude.local.md",
        ".cursorrules",
        ".mcp.json",
    }:
        raise validation_error(
            "Source name collides with knowledge-unit control or Cortex metadata",
            "reserved_source_name",
            path=source.name,
        )
    return source


def _safe_conversion(
    conversion_text: str | None,
    core: CoreRunner,
) -> tuple[Path | None, list[tuple[str, Path, bool]] | None, Path | None]:
    if conversion_text is None:
        return None, None, None
    if conversion_text == "-":
        raise usage_error("--conversion does not support stdin", "conversion_stdin_unsupported")
    conversion = Path(os.path.abspath(conversion_text))
    core.inspect(conversion)
    kind, entries = inspect_conversion(conversion)
    if kind != "directory":
        raise validation_error("--conversion must name a real directory", "conversion_directory_required", path=str(conversion))
    record_matches = [
        relative
        for relative, _path, _is_directory in entries
        if relative.casefold() == "record.json"
    ]
    if record_matches:
        raise validation_error(
            "Converter payload must not contain reserved Cortex record.json",
            "reserved_record_metadata",
            path=record_matches[0],
        )
    retained = [
        path
        for relative, path, is_directory in entries
        if not is_directory
        and relative.startswith("src/")
        and relative.count("/") == 1
        and Path(relative).name != ".keep"
    ]
    retained_source = retained[0] if len(retained) == 1 else None
    return conversion, entries, retained_source


def _match_retained_source(source: Path | None, retained: Path | None) -> None:
    if source is None or retained is None:
        return
    if retained.name != source.name or _sha256(retained) != _sha256(source):
        raise validation_error(
            "--source must equal retained conversion source by basename and SHA-256",
            "conversion_source_mismatch",
            path=f"src/{retained.name}",
        )


def _integrate_source(stage: Path, source: Path | None, retained: Path | None) -> None:
    """Apply Cortex's explicit source choice before Core completes the envelope."""
    if source is None or retained is not None:
        return
    src = stage / "src"
    if not exists(src):
        src.mkdir(exist_ok=False)
    else:
        require_real_directory(src, code="invalid_source_directory")
    children = checked_scandir(src)
    if children:
        if len(children) != 1 or children[0].name != ".keep":
            raise validation_error("src/ is not empty", "invalid_source_directory", path="src")
        marker = Path(children[0].path)
        require_regular_file(marker, code="invalid_empty_marker")
        if marker.stat().st_size != 0:
            raise validation_error("src/.keep must be zero-byte", "invalid_empty_marker", path="src/.keep")
        marker.unlink()
    copy_regular(source, src / source.name)


def _require_initialized_lock_target(workspace: Path) -> None:
    target = workspace / "profiles" / "record-schema.json"
    if not exists(target):
        raise validation_error("Workspace is not initialized", "workspace_not_initialized", path=str(workspace))
    require_regular_file(target, code="invalid_lock_target")


class CortexService:
    def __init__(self, workspace: Path | str | None, *, kb_root: Path | str | None = None, bundle_id: str | None = None, core: CoreRunner | None = None) -> None:
        self.workspace = Path(os.path.abspath(workspace)) if workspace is not None else None
        self.kb_root = Path(os.path.abspath(kb_root)) if kb_root is not None else None
        self.bundle_id = bundle_id
        self.core = core
        self._active_mutation_report: ValidationReport | None = None
        if self.workspace is None and (self.kb_root is None or self.bundle_id is None):
            raise ValueError("An unresolved service requires both kb_root and bundle_id")

    def _core(self) -> CoreRunner:
        self.core = require_core(self.core)
        return self.core

    def _lock_path(self) -> Path:
        if self.kb_root is not None:
            return self.kb_root / ROOT_LOCK_FILENAME
        assert self.workspace is not None
        return workspace_lock_path(self.workspace)

    def _refresh_managed_workspace(self) -> None:
        if self.kb_root is None:
            return
        assert self.bundle_id is not None
        registry = require_registry(self.kb_root, core=self._core())
        entry = resolve_bundle(self.kb_root, self.bundle_id, registry=registry, core=self._core())
        selected = self.kb_root / entry["path"]
        if self.workspace is None:
            self.workspace = selected
        elif selected != self.workspace:
            raise validation_error("Bundle resolution changed before mutation", "bundle_resolution_changed", id=self.bundle_id)

    @contextmanager
    def _mutation_report(self) -> Iterator[ValidationReport]:
        if self._active_mutation_report is not None:
            yield self._active_mutation_report
            return
        self._core()
        lock_path = self._lock_path()
        if self.kb_root is None:
            assert self.workspace is not None
            _require_initialized_lock_target(self.workspace)
        with writer_lock(lock_path) as lock_stream:
            self._refresh_managed_workspace()
            assert self.workspace is not None
            _require_initialized_lock_target(self.workspace)
            locked_schema = None
            if lock_path == self.workspace / "profiles" / "record-schema.json":
                lock_stream.seek(0)
                locked_schema = lock_stream.read()
            report = validate_workspace(self.workspace, locked_record_schema=locked_schema, core=self._core())
            _raise_invalid_report(report)
            yield report

    @contextmanager
    def batch_add_context(self) -> Iterator[None]:
        """Private batch scope: one native lock, validation, and Core process."""
        core = self._core()
        with core.session():
            with self._mutation_report() as report:
                self._active_mutation_report = report
                try:
                    yield
                finally:
                    self._active_mutation_report = None

    def batch_revalidate(self) -> None:
        """Rebuild private batch state after a Result with uncertain effects."""
        assert self._active_mutation_report is not None and self.workspace is not None
        report = validate_workspace(self.workspace, core=self._core())
        _raise_invalid_report(report)
        self._active_mutation_report = report

    def init(self) -> Outcome:
        root_lock = self.workspace.parent / ROOT_LOCK_FILENAME
        if exists(root_lock):
            with writer_lock(workspace_lock_path(self.workspace)):
                return self._init_unlocked()
        return self._init_unlocked()

    def _init_unlocked(self) -> Outcome:
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
        return Outcome(data={"version": VERSION, "partition_tag_group": None})

    def status(self) -> Outcome:
        report = validate_workspace(self.workspace, core=self._core())
        return Outcome(data={"version": VERSION, "valid": report.valid, "count": report.count})

    def validate(self) -> Outcome:
        report = validate_workspace(self.workspace, core=self._core())
        data = {"version": VERSION, "valid": report.valid, "count": report.count}
        if report.valid:
            return Outcome(data=data)
        return Outcome(status=Status.VALIDATION_ERROR, data=data, issues=report.issues)

    def config_show(self, profile: str) -> Outcome:
        report = validate_workspace(self.workspace, core=self._core())
        _raise_invalid_report(report)
        value = RECORD_SCHEMA if profile == "record" else report.tags if profile == "tags" else report.layout
        assert value is not None
        return Outcome(data={"profile": profile, "value": value})

    def config_set(self, profile: str, operand: str) -> Outcome:
        value, _ = read_json_operand(operand)
        require_valid_profile(profile, value)
        with self._mutation_report() as report:
            value = _read_operand_again(operand, value)
            require_valid_profile(profile, value)
            if profile == "layout" and report.count and value != report.layout:
                raise validation_error("Layout is immutable after the first record", "layout_change_forbidden")
            candidate_report = validate_workspace(
                self.workspace,
                locked_record_schema=json_bytes(RECORD_SCHEMA),
                tags_override=value if profile == "tags" else None,
                layout_override=value if profile == "layout" else None,
                core=self._core(),
            )
            _raise_invalid_report(candidate_report)
            return self._set_profile(profile, value)

    def _set_profile(self, profile: str, value: dict[str, Any]) -> Outcome:
        _atomic_replace_json(self.workspace / "profiles" / f"{profile}.json", value, profile)
        return Outcome(data={"profile": profile, "value": value})

    def record_add(
        self,
        source_operand: str | None,
        conversion_operand: str | None,
        metadata_operand: str,
    ) -> Outcome:
        if source_operand is None and conversion_operand is None:
            raise usage_error(
                "record add requires --source, --conversion, or both",
                "record_payload_required",
            )
        metadata, _ = read_json_operand(metadata_operand)
        _precheck_metadata_shape(metadata)
        preflight_source = _safe_source(source_operand)
        _, _preflight_entries, preflight_retained = _safe_conversion(conversion_operand, self._core())
        _match_retained_source(preflight_source, preflight_retained)
        with self._mutation_report() as report:
            assert report.tags is not None and report.layout is not None
            if report.layout["partition_tag_group"] is None:
                raise validation_error("Bundle must be configured before records can be added", "bundle_not_operational")
            metadata = _read_operand_again(metadata_operand, metadata)
            registered = registered_tags(report.tags)
            layout = report.layout
            record = _complete_metadata(metadata, registered, auto_timestamp=False)
            naming_tag, _naming_tags = _naming_tag_for_record(record, report.tags, report.layout)
            source = _safe_source(source_operand)
            _, conversion_entries, retained_source = _safe_conversion(conversion_operand, self._core())
            _match_retained_source(source, retained_source)
            if conversion_entries is None:
                assert source is not None
                if source.suffix == "":
                    raise validation_error(
                        "Source-only representation must have an extension",
                        "representation_extension_required",
                        path=source.name,
                    )
            partition = naming_tag
            require_safe_component(partition, allow_profiles=False, label=partition)
            if len(partition.encode("utf-8")) > layout["max_component_length"]:
                raise validation_error("Partition exceeds max_component_length", "partition_name_too_long", path=partition)
            folder = tag_title_date_name(naming_tag, record["title"], record["timestamp"], layout["max_component_length"])
            partition_path = self.workspace / partition
            partition_exists = exists(partition_path)
            if partition_exists:
                require_real_directory(partition_path, code="invalid_partition")
                existing = {entry.name.casefold() for entry in checked_scandir(partition_path)}
            else:
                root_names = {entry.name.casefold() for entry in checked_scandir(self.workspace) if entry.name != "profiles"}
                if partition.casefold() in root_names:
                    raise validation_error("Partition collides under case folding", "partition_casefold_collision", path=partition)
                existing = set()
            if folder.casefold() in existing:
                raise validation_error("Record folder already exists", "duplicate_record_name", path=folder)
            temporary = (partition_path if partition_exists else self.workspace) / f".cortex-add-{uuid.uuid4().hex}"
            destination = partition_path / folder
            staged_unit = temporary if partition_exists else temporary / folder
            temporary_created = False
            try:
                temporary.mkdir(exist_ok=False)
                temporary_created = True
                if not partition_exists:
                    staged_unit.mkdir(exist_ok=False)
                with (staged_unit / "record.json").open("xb") as stream:
                    stream.write(json_bytes(record))
                    stream.flush()
                if conversion_entries is not None:
                    for relative, item_source, directory in conversion_entries:
                        target = staged_unit.joinpath(*relative.split("/"))
                        if directory:
                            target.mkdir(exist_ok=False)
                        else:
                            copy_regular(item_source, target)
                else:
                    assert source is not None
                    copy_regular(source, staged_unit / source.name)
                if conversion_entries is not None:
                    _integrate_source(staged_unit, source, retained_source)
                self._core().stage_complete(staged_unit, private_root_files=("record.json",))
                problems = validate_record_directory(
                    staged_unit,
                    folder,
                    registered=registered,
                    maximum=layout["max_component_length"],
                    partition=partition,
                    label_root=partition,
                    tags=report.tags,
                    layout=layout,
                    check_folder=False,
                    core=self._core(),
                    _validate_envelope=False,
                )
                if problems:
                    first = problems[0]
                    raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
                try:
                    rename_no_replace(temporary, destination if partition_exists else partition_path)
                except FileExistsError:
                    raise validation_error("Record folder already exists", "duplicate_record_name", path=folder)
            except CortexError:
                if temporary_created:
                    _cleanup_staged_directory(temporary)
                raise
            except OSError as exc:
                if temporary_created:
                    _cleanup_staged_directory(temporary)
                raise io_error("Record could not be staged", "record_stage_failed", path=str(destination), os_error=str(exc)) from exc
            return Outcome(data={"partition": partition, "record": folder, "path": f"{partition}/{folder}"})

    def record_edit(self, partition_value: str, folder: str, metadata_operand: str) -> Outcome:
        partition = _partition_operand(partition_value)
        unit = _record_operand(folder)
        metadata, _ = read_json_operand(metadata_operand)
        _precheck_metadata_shape(metadata)
        with self._mutation_report() as report:
            assert report.tags is not None and report.layout is not None
            metadata = _read_operand_again(metadata_operand, metadata)
            _precheck_metadata_shape(metadata)
            unit_path = self.workspace / partition / unit
            if not exists(unit_path):
                raise validation_error("Record folder does not exist", "record_not_found", path=folder)
            record_path = unit_path / "record.json"
            registered = registered_tags(report.tags)
            record = _complete_metadata(metadata, registered, auto_timestamp=False)
            current = read_json_operand(str(record_path))[0]
            old_tag, _ = _naming_tag_for_record(current, report.tags, report.layout)
            new_tag, _ = _naming_tag_for_record(record, report.tags, report.layout)
            if old_tag != partition:
                raise validation_error("Record does not belong to the requested partition", "partition_record_mismatch", path=f"{partition}/{unit}")
            if record["title"] != current["title"] or record["timestamp"] != current["timestamp"] or new_tag != old_tag:
                raise validation_error("Title, timestamp, and selected partition tag are immutable", "record_identity_change_forbidden", path=folder)
            _atomic_replace_json(record_path, record, "edit")
            return Outcome(data={"partition": partition, "record": folder, "path": f"{partition}/{folder}"})

    def record_show(self, partition_value: str, folder: str) -> Outcome:
        partition = _partition_operand(partition_value)
        unit = _record_operand(folder)
        with self._mutation_report() as report:
            unit_path = self.workspace / partition / unit
            if not exists(unit_path):
                raise validation_error("Record folder does not exist", "record_not_found", path=folder)
            inventory = inventory_unit(unit_path, partition, unit)
            record_bytes = (unit_path / "record.json").read_bytes()
            metadata = loads_object(record_bytes, label=f"{partition}/{unit}/record.json")
            selected, _ = _naming_tag_for_record(metadata, report.tags, report.layout)
            if selected != partition:
                raise validation_error("Record does not belong to the requested partition", "partition_record_mismatch", path=f"{partition}/{unit}")
            confirmation = inventory_unit(unit_path, partition, unit)
            if confirmation.sha256 != inventory.sha256 or confirmation.manifest != inventory.manifest:
                raise validation_error("Unit changed while record metadata was read", "tree_changed_during_read", path=unit)
            return Outcome(data={
                "partition": partition,
                "record": unit,
                "tree_sha256": inventory.sha256,
                "record_json_sha256": hashlib.sha256(record_bytes).hexdigest(),
                "metadata": metadata,
                "manifest": [{"path": item.relative, "type": item.kind, **({"size": item.size} if item.size is not None else {})} for item in inventory.manifest],
            })

    def record_delete(self, partition_value: str, folder: str, expected: str) -> Outcome:
        partition = _partition_operand(partition_value)
        unit = _record_operand(folder)
        if len(expected) != 64 or expected.lower() != expected or any(ch not in "0123456789abcdef" for ch in expected):
            raise validation_error("Expected tree digest must be lowercase 64-character SHA-256", "invalid_expected_tree_sha256")
        with self._mutation_report() as report:
            partition_path = self.workspace / partition
            unit_path = partition_path / unit
            if not exists(unit_path):
                raise validation_error("Record folder does not exist", "record_not_found", path=unit)
            inventory = inventory_unit(unit_path, partition, unit)
            if inventory.sha256 != expected:
                raise validation_error("Unit tree digest does not match", "tree_digest_mismatch", expected=expected, actual=inventory.sha256)
            ordered = sorted(inventory.manifest, key=lambda item: (item.relative.count("/"), item.relative.encode("utf-8")), reverse=True)
            first_failed = None
            try:
                for item in ordered:
                    path = unit_path.joinpath(*item.relative.split("/"))
                    first_failed = item.relative
                    meta = os.lstat(native_path(path))
                    if is_reparse_metadata(meta) or (item.kind == "file" and not stat.S_ISREG(meta.st_mode)) or (item.kind == "directory" and not stat.S_ISDIR(meta.st_mode)):
                        raise OSError("authorized entry type changed")
                    if item.kind == "file":
                        os.unlink(native_path(path))
                    else:
                        os.rmdir(native_path(path))
                first_failed = "."
                os.rmdir(native_path(unit_path))
                if not checked_scandir(partition_path):
                    os.rmdir(native_path(partition_path))
            except (OSError, CortexError) as exc:
                try:
                    remaining = _residue_count(unit_path) if exists(unit_path) else 0
                    issues = [{"code": "delete_incomplete", "message": "Record deletion stopped at first failure", "path": first_failed or ".", "details": {"os_error": str(exc)}}]
                except (OSError, CortexError) as scan_exc:
                    remaining = None
                    issues = [
                        {"code": "delete_incomplete", "message": "Record deletion stopped at first failure", "path": first_failed or ".", "details": {"os_error": str(exc)}},
                        {"code": "residue_unreadable", "message": "Deletion residue could not be counted", "details": {"os_error": str(scan_exc)}},
                    ]
                return Outcome(status=Status.IO_ERROR, data={"partition": partition, "record": unit, "partial": True, "first_failed_relative_path": first_failed or ".", "remaining_entry_count": remaining}, issues=issues)
            return Outcome(data={"partition": partition, "record": unit, "deleted": True, "tree_sha256": expected})


class RegistryService:
    def __init__(self, root: Path | str, *, core: CoreRunner | None = None) -> None:
        self.root = Path(os.path.abspath(root))
        self.core = core

    def _core(self) -> CoreRunner:
        self.core = require_core(self.core)
        return self.core

    def show(self) -> Outcome:
        value = require_registry(self.root, core=self._core())
        return Outcome(data={"registry": value})

    def validate(self) -> Outcome:
        report = validate_registry(self.root, core=self._core())
        count = len(report.value["bundles"]) if report.value is not None and isinstance(report.value.get("bundles"), list) else 0
        data = {"version": VERSION, "valid": report.valid, "bundles": count}
        if report.valid:
            return Outcome(data=data)
        return Outcome(status=Status.VALIDATION_ERROR, data=data, issues=report.issues)

    def resolve(self, bundle_id: str) -> Outcome:
        registry = require_registry(self.root, core=self._core())
        entry = resolve_bundle(self.root, bundle_id, registry=registry, core=self._core())
        return Outcome(data={"bundle_id": entry["id"], "path": entry["path"], "workspace": str(self.root / entry["path"]), "description": entry["description"]})

    def _current_for_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        report = validate_registry(self.root, core=self._core())
        candidate_paths = {entry["path"] for entry in candidate["bundles"]}
        blocking = [
            item
            for item in report.issues
            if not (item["code"] == "orphan_bundle" and item.get("path") in candidate_paths)
        ]
        if blocking:
            first = blocking[0]
            raise validation_error(first["message"], first["code"], path=first.get("path"), issues=blocking)
        if report.value is None:
            raise validation_error("Registry is not readable", "registry_unreadable", path=REGISTRY_FILENAME)
        return report.value

    def set(self, operand: str) -> Outcome:
        candidate_input, _ = read_json_operand(operand)
        syntax_problems = validate_registry_value(candidate_input)
        if syntax_problems:
            first = syntax_problems[0]
            raise validation_error(first["message"], first["code"], path=first.get("path"), issues=syntax_problems)
        candidate = canonical_registry(candidate_input)
        current_path = self.root / REGISTRY_FILENAME
        if exists(current_path):
            transition_problems = validate_transition(self._current_for_candidate(candidate), candidate)
            if transition_problems:
                first = transition_problems[0]
                raise validation_error(first["message"], first["code"], path=first.get("path"), issues=transition_problems)
        static = validate_registry(self.root, value_override=candidate_input, core=self._core())
        if static.issues:
            first = static.issues[0]
            raise validation_error(first["message"], first["code"], path=first.get("path"), issues=static.issues)

        assert static.value is not None
        candidate = static.value
        lock_path = self.root / ROOT_LOCK_FILENAME
        if not exists(lock_path):
            try:
                with lock_path.open("xb"):
                    pass
            except FileExistsError:
                pass
            except OSError as exc:
                raise io_error("KB-root lock could not be created", "lock_create_failed", path=str(lock_path), os_error=str(exc)) from exc
        require_regular_file(lock_path, code="invalid_root_lock")
        try:
            if lock_path.stat().st_size != 0:
                raise validation_error("KB-root lock must remain zero bytes", "invalid_root_lock", path=str(lock_path))
        except OSError as exc:
            raise io_error("KB-root lock could not be inspected", "lock_target_unreadable", path=str(lock_path), os_error=str(exc)) from exc
        with writer_lock(lock_path):
            candidate_input = _read_operand_again(operand, candidate)
            checked = validate_registry(self.root, value_override=candidate_input, core=self._core())
            if checked.issues:
                first = checked.issues[0]
                raise validation_error(first["message"], first["code"], path=first.get("path"), issues=checked.issues)
            assert checked.value is not None
            candidate = checked.value
            current_path = self.root / REGISTRY_FILENAME
            current: dict[str, Any] | None = None
            if exists(current_path):
                current = self._current_for_candidate(candidate)
            problems: list[dict[str, Any]] = []
            if current is not None:
                problems.extend(validate_transition(current, candidate))
            if problems:
                first = problems[0]
                raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)
            if current is None:
                if candidate.get("version") == 1:
                    candidate = {"contract": "cortex-kb-registry/v2", "version": 2, "bundles": candidate["bundles"]}
                try:
                    with current_path.open("xb") as stream:
                        stream.write(json_bytes(candidate))
                        stream.flush()
                except FileExistsError as exc:
                    raise CortexError("Registry was concurrently created", status=Status.BUSY, code="registry_busy", path=str(current_path)) from exc
                except OSError as exc:
                    raise io_error("Registry could not be created", "registry_create_failed", path=str(current_path), os_error=str(exc)) from exc
            else:
                _atomic_replace_json(current_path, candidate, "registry")
        return Outcome(data={"registry": candidate})


__all__ = ["CortexService", "Outcome", "RegistryService"]
