"""Side-effect-free structural validation for a Cortex 8 record Bundle."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import PROFILE_FILENAMES, RECORD_FIELDS, RECORD_SCHEMA
from .errors import CortexError, issue
from .jsonio import json_bytes, loads_object
from .knowledge_unit import validate_complete_directory
from .naming import require_naming_runtime, tag_title_date_name
from .native import component_problem, is_reparse_metadata, native_path
from .profiles import registered_tags, tag_groups, validate_layout_profile, validate_record, validate_record_schema, validate_tags_profile


@dataclass
class ValidationReport:
    issues: list[dict[str, Any]]
    count: int
    tags: dict[str, Any] | None
    layout: dict[str, Any] | None

    @property
    def valid(self) -> bool:
        return not self.issues


def _scan(path: Path, label: str, issues: list[dict[str, Any]]) -> list[os.DirEntry[str]]:
    try:
        return sorted(os.scandir(native_path(path)), key=lambda e: e.name.encode("utf-8", "strict"))
    except (OSError, UnicodeEncodeError) as exc:
        issues.append(issue("directory_unreadable", "Directory could not be read as strict UTF-8 names", path=label, os_error=str(exc)))
        return []


def _meta(path: Path, label: str, issues: list[dict[str, Any]]) -> os.stat_result | None:
    try:
        return os.lstat(native_path(path))
    except OSError as exc:
        issues.append(issue("path_unreadable", "Filesystem entry could not be inspected", path=label, os_error=str(exc)))
        return None


def _is_dir(path: Path, label: str, issues: list[dict[str, Any]]) -> bool:
    meta = _meta(path, label, issues)
    if meta is None:
        return False
    if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode):
        issues.append(issue("real_directory_required", "Entry must be a real directory", path=label))
        return False
    return True


def _is_file(path: Path, label: str, issues: list[dict[str, Any]]) -> bool:
    meta = _meta(path, label, issues)
    if meta is None:
        return False
    if is_reparse_metadata(meta) or not stat.S_ISREG(meta.st_mode):
        issues.append(issue("ordinary_file_required", "Entry must be an ordinary file", path=label))
        return False
    return True


def _read_json(path: Path, label: str, issues: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bytes | None]:
    if not _is_file(path, label, issues):
        return None, None
    try:
        payload = path.read_bytes()
        return loads_object(payload, label=label), payload
    except CortexError as exc:
        issues.append(exc.as_issue())
    except OSError as exc:
        issues.append(issue("file_unreadable", "File could not be read", path=label, os_error=str(exc)))
    return None, None


def _component(name: str, label: str, issues: list[dict[str, Any]]) -> None:
    problem = component_problem(name)
    if problem:
        issues.append(issue(problem[0], problem[1], path=label))


def _opaque_assets(root: Path, label: str, issues: list[dict[str, Any]]) -> None:
    folded: dict[str, str] = {}
    def walk(directory: Path, prefix: str = "") -> None:
        for entry in _scan(directory, label + ("/" + prefix if prefix else ""), issues):
            rel = f"{prefix}/{entry.name}" if prefix else entry.name
            _component(entry.name, f"{label}/{rel}", issues)
            key = rel.casefold()
            if key in folded and folded[key] != rel:
                issues.append(issue("conversion_casefold_collision", "Payload paths collide under case folding", path=label, paths=[folded[key], rel]))
            folded[key] = rel
            try:
                meta = entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(issue("path_unreadable", "Payload entry could not be inspected", path=f"{label}/{rel}", os_error=str(exc)))
                continue
            if is_reparse_metadata(meta):
                issues.append(issue("reparse_path", "Links and reparse points are forbidden", path=f"{label}/{rel}"))
            elif stat.S_ISDIR(meta.st_mode):
                walk(Path(entry.path), rel)
            elif not stat.S_ISREG(meta.st_mode):
                issues.append(issue("nonregular_entry", "Only regular files and real directories are allowed", path=f"{label}/{rel}"))
    walk(root)


def _selected_tag(record: dict[str, Any], tags: dict[str, Any], layout: dict[str, Any], label: str, issues: list[dict[str, Any]]) -> str | None:
    group = layout["partition_tag_group"]
    if group is None:
        issues.append(issue("bundle_not_operational", "A nonempty partition_tag_group is required for records", path="profiles/layout.json#/partition_tag_group"))
        return None
    groups = tag_groups(tags)
    if group not in groups:
        issues.append(issue("unknown_partition_tag_group", "partition_tag_group must name an existing Tag 2 group", path="profiles/layout.json#/partition_tag_group", group=group))
        return None
    choices = {item["tag"] for item in groups[group]}
    selected = [tag for tag in record.get("tags", []) if tag in choices]
    if len(selected) != 1:
        issues.append(issue("partition_tag_count", "Record must select exactly one partition tag", path=f"{label}/record.json#/tags", tags=selected))
        return None
    return selected[0]


def validate_record_directory(record_dir: Path, folder: str, *, registered: set[str], maximum: int, partition: str | None = None, label_root: str = "", tags: dict[str, Any] | None = None, layout: dict[str, Any] | None = None, check_folder: bool = True, **_legacy: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    label = f"{label_root}/{folder}".strip("/")
    if check_folder:
        _component(folder, label, issues)
        try:
            if len(folder.encode("utf-8", "strict")) > maximum:
                issues.append(issue("record_folder_too_long", "Record folder exceeds max_component_length", path=label))
        except UnicodeEncodeError:
            issues.append(issue("unsafe_component", "Record folder is not strict UTF-8", path=label))
    if not _is_dir(record_dir, label, issues):
        return issues
    entries = _scan(record_dir, label, issues)
    names = {e.name for e in entries}
    for forbidden in ("original", "representations"):
        if forbidden in names:
            issues.append(issue("legacy_record_wrapper", "Legacy record wrappers are invalid in Layout 5", path=f"{label}/{forbidden}"))
    if "record.json" not in names:
        issues.append(issue("missing_record_entry", "record.json is required", path=f"{label}/record.json"))
    record, payload = (None, None)
    if "record.json" in names:
        record, payload = _read_json(record_dir / "record.json", f"{label}/record.json", issues)
    selected: str | None = None
    if record is not None:
        issues.extend(validate_record(record, registered, label=f"{label}/record.json"))
        canonical = {field: record.get(field) for field in RECORD_FIELDS}
        try:
            if payload != json_bytes(canonical):
                issues.append(issue("noncanonical_record_json", "record.json must be canonical Cortex JSON", path=f"{label}/record.json"))
        except UnicodeEncodeError:
            issues.append(issue("invalid_utf8_text", "record.json is not strict UTF-8", path=f"{label}/record.json"))
        if tags is not None and layout is not None:
            selected = _selected_tag(record, tags, layout, label, issues)
            if selected is not None:
                if partition is not None and selected != partition:
                    issues.append(issue("partition_tag_mismatch", "Record partition tag must exactly equal its partition", path=label, expected=partition, actual=selected))
                try:
                    expected = tag_title_date_name(selected, record["title"], record["timestamp"], maximum)
                    if folder != expected:
                        issues.append(issue("record_name_mismatch", "Record folder does not match Layout 5 naming", path=label, expected=expected, actual=folder))
                except CortexError as exc:
                    issues.append(exc.as_issue())

    try:
        validate_complete_directory(record_dir, cortex_record=True)
    except CortexError as exc:
        nested = exc.details.get("issues")
        if isinstance(nested, list):
            issues.extend(item for item in nested if isinstance(item, dict))
        else:
            problem = exc.as_issue()
            if problem.get("path"):
                problem["path"] = f"{label}/{problem['path']}".rstrip("/")
            else:
                problem["path"] = label
            issues.append(problem)
    return issues


def validate_workspace(workspace: Path, *, locked_record_schema: bytes | None = None, tags_override: dict[str, Any] | None = None, layout_override: dict[str, Any] | None = None) -> ValidationReport:
    workspace = Path(os.path.abspath(workspace))
    issues: list[dict[str, Any]] = []
    if not _is_dir(workspace, ".", issues):
        return ValidationReport([issue("workspace_not_initialized", "Workspace does not exist or is not a real directory", path=".")], 0, None, None)
    roots = _scan(workspace, ".", issues)
    names = {e.name for e in roots}
    tags_value: dict[str, Any] | None = None
    layout_value: dict[str, Any] | None = None
    profiles = workspace / "profiles"
    if "profiles" not in names or not _is_dir(profiles, "profiles", issues):
        issues.append(issue("missing_profiles", "profiles directory is missing", path="profiles"))
    else:
        profile_entries = _scan(profiles, "profiles", issues)
        pnames = {e.name for e in profile_entries}
        for missing in sorted(set(PROFILE_FILENAMES) - pnames): issues.append(issue("missing_profile", "Required profile is missing", path=f"profiles/{missing}"))
        for extra in sorted(pnames - set(PROFILE_FILENAMES)): issues.append(issue("unexpected_profile", "Unexpected profile entry", path=f"profiles/{extra}"))
        if "record-schema.json" in pnames:
            if locked_record_schema is None:
                value, raw = _read_json(profiles / "record-schema.json", "profiles/record-schema.json", issues)
            else:
                value, raw = loads_object(locked_record_schema, label="profiles/record-schema.json"), locked_record_schema
            if value is not None:
                issues.extend(validate_record_schema(value))
                if raw != json_bytes(RECORD_SCHEMA): issues.append(issue("noncanonical_record_schema", "record-schema.json is not canonical", path="profiles/record-schema.json"))
        if "tags.json" in pnames:
            disk, raw = _read_json(profiles / "tags.json", "profiles/tags.json", issues)
            tags_value = tags_override if tags_override is not None else disk
            if tags_value is not None:
                issues.extend(validate_tags_profile(tags_value))
                if tags_override is None and raw != json_bytes(tags_value): issues.append(issue("noncanonical_profile_json", "tags.json is not canonical", path="profiles/tags.json"))
        if "layout.json" in pnames:
            disk, raw = _read_json(profiles / "layout.json", "profiles/layout.json", issues)
            layout_value = layout_override if layout_override is not None else disk
            if layout_value is not None:
                issues.extend(validate_layout_profile(layout_value))
                if layout_override is None and raw != json_bytes(layout_value): issues.append(issue("noncanonical_profile_json", "layout.json is not canonical", path="profiles/layout.json"))
    count = 0
    if tags_value is None or layout_value is None or validate_tags_profile(tags_value) or validate_layout_profile(layout_value):
        return ValidationReport(issues, count, tags_value, layout_value)
    group = layout_value["partition_tag_group"]
    groups = tag_groups(tags_value)
    if group is not None:
        try:
            require_naming_runtime()
        except CortexError as exc:
            issues.append(exc.as_issue())
            return ValidationReport(issues, count, tags_value, layout_value)
    if group is not None and group not in groups:
        issues.append(issue("unknown_partition_tag_group", "partition_tag_group must name an existing Tag 2 group", path="profiles/layout.json#/partition_tag_group", group=group))
    if group is not None and group in groups:
        folded_tags: dict[str, str] = {}
        maximum = layout_value["max_component_length"]
        for item in groups[group]:
            tag = item["tag"]
            problem = component_problem(tag, allow_profiles=False)
            if problem:
                issues.append(issue(problem[0], problem[1], path="profiles/tags.json", tag=tag))
            try:
                if len(tag.encode("utf-8", "strict")) + 11 > maximum:
                    issues.append(issue("insufficient_unit_name_capacity", "Naming tag and date leave no title capacity", path="profiles/layout.json#/max_component_length", tag=tag))
            except UnicodeEncodeError:
                issues.append(issue("unsafe_component", "Naming tag is not strict UTF-8", path="profiles/tags.json", tag=tag))
            key = tag.casefold()
            if key in folded_tags and folded_tags[key] != tag:
                issues.append(issue("naming_tag_casefold_collision", "Naming tags collide under case folding", path="profiles/tags.json", tags=[folded_tags[key], tag]))
            folded_tags[key] = tag
    registered = registered_tags(tags_value)
    collisions: dict[str, str] = {}
    partition_tags = {item["tag"] for item in groups[group]} if group is not None and group in groups else set()
    for entry in roots:
        if entry.name == "profiles":
            continue
        key = entry.name.casefold()
        if key in collisions and collisions[key] != entry.name:
            issues.append(issue("partition_casefold_collision", "Partition names collide under case folding", path=".", names=[collisions[key], entry.name]))
        collisions[key] = entry.name
        _component(entry.name, entry.name, issues)
        partition_path = Path(entry.path)
        if not _is_dir(partition_path, entry.name, issues):
            continue
        if partition_tags and entry.name not in partition_tags:
            issues.append(issue("unknown_partition", "Partition must be an exact configured tag value", path=entry.name))
        units = _scan(partition_path, entry.name, issues)
        if not units:
            issues.append(issue("empty_partition", "Layout 5 partitions must be nonempty", path=entry.name))
        unit_collisions: dict[str, str] = {}
        for unit in units:
            count += 1
            unit_key = unit.name.casefold()
            if unit_key in unit_collisions and unit_collisions[unit_key] != unit.name:
                issues.append(issue("record_casefold_collision", "Record unit names collide under case folding", path=entry.name, names=[unit_collisions[unit_key], unit.name]))
            unit_collisions[unit_key] = unit.name
            issues.extend(validate_record_directory(Path(unit.path), unit.name, partition=entry.name, label_root=entry.name, registered=registered, maximum=layout_value["max_component_length"], tags=tags_value, layout=layout_value))
    if count and group is None:
        issues.append(issue("bundle_not_operational", "A nonempty partition group is required when records exist", path="profiles/layout.json#/partition_tag_group"))
    return ValidationReport(issues, count, tags_value, layout_value)


__all__ = ["ValidationReport", "validate_record_directory", "validate_workspace"]
