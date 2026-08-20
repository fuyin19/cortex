"""Side-effect-free structural validation for a Cortex 5 record KB."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import PROFILE_FILENAMES, RECORD_FIELDS, RECORD_SCHEMA
from .errors import CortexError, issue
from .jsonio import json_bytes, loads_object
from .native import component_problem, is_reparse_metadata, native_path
from .profiles import (
    registered_tags,
    tag_groups,
    validate_layout_profile,
    validate_record,
    validate_record_schema,
    validate_tags_profile,
)


@dataclass
class ValidationReport:
    issues: list[dict[str, Any]]
    count: int
    tags: dict[str, Any] | None
    layout: dict[str, Any] | None

    @property
    def valid(self) -> bool:
        return not self.issues


def _scan(directory: Path, label: str, issues: list[dict[str, Any]]) -> list[os.DirEntry[str]]:
    try:
        return sorted(os.scandir(native_path(directory)), key=lambda entry: entry.name.encode("utf-8", errors="surrogatepass"))
    except OSError as exc:
        issues.append(issue("directory_unreadable", "Directory could not be read", path=label, os_error=str(exc)))
        return []


def _lstat(path: Path, label: str, issues: list[dict[str, Any]]) -> os.stat_result | None:
    try:
        return os.lstat(native_path(path))
    except OSError as exc:
        issues.append(issue("path_unreadable", "Filesystem entry could not be inspected", path=label, os_error=str(exc)))
        return None


def _real_directory(path: Path, label: str, issues: list[dict[str, Any]]) -> bool:
    metadata = _lstat(path, label, issues)
    if metadata is None:
        return False
    if is_reparse_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
        issues.append(issue("real_directory_required", "Entry must be a real directory", path=label))
        return False
    return True


def _ordinary_file(path: Path, label: str, issues: list[dict[str, Any]]) -> bool:
    metadata = _lstat(path, label, issues)
    if metadata is None:
        return False
    if is_reparse_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
        issues.append(issue("ordinary_file_required", "Entry must be an ordinary file", path=label))
        return False
    return True


def _read_owned_json(path: Path, label: str, issues: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bytes | None]:
    if not _ordinary_file(path, label, issues):
        return None, None
    try:
        payload = path.read_bytes()
        return loads_object(payload, label=label), payload
    except CortexError as exc:
        issues.append(exc.as_issue())
    except OSError as exc:
        issues.append(issue("file_unreadable", "File could not be read", path=label, os_error=str(exc)))
    return None, None


def _canonical_json_matches(value: dict[str, Any], payload: bytes | None, label: str, issues: list[dict[str, Any]]) -> bool:
    try:
        return payload == json_bytes(value)
    except UnicodeEncodeError:
        issues.append(issue("invalid_utf8_text", "JSON contains text that is not valid UTF-8", path=label))
        return False


def _component_issues(name: str, label: str, issues: list[dict[str, Any]]) -> None:
    problem = component_problem(name)
    if problem is not None:
        issues.append(issue(problem[0], problem[1], path=label))


def _validate_opaque_tree(root: Path, label: str, issues: list[dict[str, Any]]) -> None:
    collisions: dict[str, str] = {}

    def visit(directory: Path, prefix: str = "") -> None:
        for entry in _scan(directory, f"{label}/{prefix}".rstrip("/"), issues):
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            path_label = f"{label}/{relative}"
            _component_issues(entry.name, path_label, issues)
            key = relative.casefold()
            previous = collisions.get(key)
            if previous is not None and previous != relative:
                issues.append(
                    issue(
                        "conversion_casefold_collision",
                        "Conversion paths collide under case folding",
                        path=label,
                        paths=[previous, relative],
                    )
                )
            collisions[key] = relative
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(issue("path_unreadable", "Entry could not be inspected", path=path_label, os_error=str(exc)))
                continue
            if is_reparse_metadata(metadata):
                issues.append(issue("reparse_path", "Symlinks and reparse points are forbidden", path=path_label))
            elif stat.S_ISDIR(metadata.st_mode):
                visit(Path(entry.path), relative)
            elif not stat.S_ISREG(metadata.st_mode):
                issues.append(issue("nonregular_entry", "Only ordinary files and real directories are allowed", path=path_label))

    visit(root)


def validate_record_directory(
    record_dir: Path,
    folder: str,
    *,
    registered: set[str],
    maximum: int,
    label_root: str,
    partition_tags: set[str] | None = None,
    expected_partition: str | None = None,
    check_folder: bool = True,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    label = f"{label_root}/{folder}"
    if check_folder:
        _component_issues(folder, label, issues)
        try:
            too_long = len(folder.encode("utf-8")) > maximum
        except UnicodeEncodeError:
            too_long = True
        if too_long:
            issues.append(issue("record_folder_too_long", "Record folder exceeds max_component_length", path=label))
    if not _real_directory(record_dir, label, issues):
        return issues

    children = _scan(record_dir, label, issues)
    names = {entry.name for entry in children}
    expected = {"record.json", "original"}
    for missing in sorted(expected - names):
        issues.append(issue("missing_record_entry", f"Record entry is missing: {missing}", path=f"{label}/{missing}"))
    for extra in sorted(names - expected - {"representations"}):
        issues.append(issue("unexpected_record_entry", "Record folder contains an unexpected entry", path=f"{label}/{extra}"))

    record_value: dict[str, Any] | None = None
    record_payload: bytes | None = None
    if "record.json" in names:
        record_value, record_payload = _read_owned_json(record_dir / "record.json", f"{label}/record.json", issues)
    if record_value is not None:
        issues.extend(validate_record(record_value, registered, label=f"{label}/record.json"))
        if partition_tags is not None and expected_partition is not None and isinstance(record_value.get("tags"), list):
            selected = [tag for tag in record_value["tags"] if tag in partition_tags]
            if len(selected) != 1:
                issues.append(
                    issue(
                        "partition_tag_count",
                        "Record must contain exactly one tag from the configured partition group",
                        path=f"{label}/record.json#/tags",
                        tags=selected,
                    )
                )
            elif selected[0] != expected_partition:
                issues.append(
                    issue(
                        "partition_path_mismatch",
                        "Record partition tag must equal its parent directory",
                        path=f"{label}/record.json#/tags",
                        expected=expected_partition,
                        actual=selected[0],
                    )
                )
        canonical_record = {field: record_value.get(field) for field in RECORD_FIELDS}
        if not _canonical_json_matches(canonical_record, record_payload, f"{label}/record.json", issues):
            issues.append(issue("noncanonical_record_json", "record.json must use two spaces, LF, fixed field order, and a trailing newline", path=f"{label}/record.json"))

    original = record_dir / "original"
    if "original" in names and _real_directory(original, f"{label}/original", issues):
        original_entries = _scan(original, f"{label}/original", issues)
        if len(original_entries) != 1:
            issues.append(issue("invalid_original", "original must contain exactly one source file", path=f"{label}/original"))
        for entry in original_entries:
            _component_issues(entry.name, f"{label}/original/{entry.name}", issues)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(issue("path_unreadable", "Original entry could not be inspected", path=f"{label}/original/{entry.name}", os_error=str(exc)))
                continue
            if is_reparse_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
                issues.append(issue("ordinary_file_required", "Original entry must be an ordinary file", path=f"{label}/original/{entry.name}"))

    representations = record_dir / "representations"
    if "representations" in names and _real_directory(representations, f"{label}/representations", issues):
        representation_entries = _scan(representations, f"{label}/representations", issues)
        representation_names = {entry.name for entry in representation_entries}
        if representation_names != {"markdown-conversion"}:
            for missing in sorted({"markdown-conversion"} - representation_names):
                issues.append(issue("missing_conversion_root", "markdown-conversion directory is missing", path=f"{label}/representations/{missing}"))
            for extra in sorted(representation_names - {"markdown-conversion"}):
                issues.append(issue("unexpected_representation", "Unexpected representation namespace", path=f"{label}/representations/{extra}"))
        conversion = representations / "markdown-conversion"
        if "markdown-conversion" in representation_names and _real_directory(conversion, f"{label}/representations/markdown-conversion", issues):
            _validate_opaque_tree(conversion, f"{label}/representations/markdown-conversion", issues)
    return issues


def validate_workspace(
    workspace: Path,
    *,
    locked_record_schema: bytes | None = None,
    tags_override: dict[str, Any] | None = None,
    layout_override: dict[str, Any] | None = None,
) -> ValidationReport:
    workspace = Path(os.path.abspath(workspace))
    issues: list[dict[str, Any]] = []
    metadata = _lstat(workspace, ".", issues)
    if metadata is None:
        return ValidationReport(issues=[issue("workspace_not_initialized", "Workspace does not exist", path=".")], count=0, tags=None, layout=None)
    if is_reparse_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
        return ValidationReport(issues=[issue("workspace_not_directory", "Workspace must be a real directory", path=".")], count=0, tags=None, layout=None)

    root_entries = _scan(workspace, ".", issues)
    root_names = {entry.name for entry in root_entries}
    profiles = workspace / "profiles"
    tags_value: dict[str, Any] | None = None
    layout_value: dict[str, Any] | None = None

    if "profiles" not in root_names:
        issues.append(issue("missing_profiles", "profiles directory is missing", path="profiles"))
    elif _real_directory(profiles, "profiles", issues):
        profile_entries = _scan(profiles, "profiles", issues)
        profile_names = {entry.name for entry in profile_entries}
        for missing in sorted(set(PROFILE_FILENAMES) - profile_names):
            issues.append(issue("missing_profile", "Required profile is missing", path=f"profiles/{missing}"))
        for extra in sorted(profile_names - set(PROFILE_FILENAMES)):
            issues.append(issue("unexpected_profile", "Unexpected profile entry", path=f"profiles/{extra}"))

        if "record-schema.json" in profile_names:
            if locked_record_schema is None:
                value, payload = _read_owned_json(profiles / "record-schema.json", "profiles/record-schema.json", issues)
            else:
                if _ordinary_file(profiles / "record-schema.json", "profiles/record-schema.json", issues):
                    payload = locked_record_schema
                    try:
                        value = loads_object(payload, label="profiles/record-schema.json")
                    except CortexError as exc:
                        issues.append(exc.as_issue())
                        value = None
                else:
                    value, payload = None, None
            if value is not None:
                issues.extend(validate_record_schema(value))
                if payload != json_bytes(RECORD_SCHEMA):
                    issues.append(issue("noncanonical_record_schema", "record-schema.json bytes do not match the fixed profile", path="profiles/record-schema.json"))
        if "tags.json" in profile_names:
            disk_tags, tags_payload = _read_owned_json(profiles / "tags.json", "profiles/tags.json", issues)
            tags_value = tags_override if tags_override is not None else disk_tags
            if tags_value is not None:
                issues.extend(validate_tags_profile(tags_value))
                if tags_override is None and not _canonical_json_matches(tags_value, tags_payload, "profiles/tags.json", issues):
                    issues.append(issue("noncanonical_profile_json", "tags.json must use canonical Cortex JSON", path="profiles/tags.json"))
        if "layout.json" in profile_names:
            disk_layout, layout_payload = _read_owned_json(profiles / "layout.json", "profiles/layout.json", issues)
            layout_value = layout_override if layout_override is not None else disk_layout
            if layout_value is not None:
                issues.extend(validate_layout_profile(layout_value))
                if layout_override is None and not _canonical_json_matches(layout_value, layout_payload, "profiles/layout.json", issues):
                    issues.append(issue("noncanonical_profile_json", "layout.json must use canonical Cortex JSON", path="profiles/layout.json"))

    tags_valid = tags_value is not None and not validate_tags_profile(tags_value)
    layout_valid = layout_value is not None and not validate_layout_profile(layout_value)
    count = 0
    root_children = sorted(root_names - {"profiles"})
    if not tags_valid or not layout_valid:
        for extra in root_children:
            issues.append(issue("unexpected_workspace_entry", "Workspace contains an unexpected root entry", path=extra))
        return ValidationReport(issues=issues, count=count, tags=tags_value, layout=layout_value)

    assert tags_value is not None and layout_value is not None
    groups = tag_groups(tags_value)
    all_registered = registered_tags(tags_value)
    partition_by = layout_value["partition_by"]
    maximum = layout_value["max_component_length"]
    if partition_by is None:
        for extra in root_children:
            issues.append(issue("unconfigured_bundle_content", "An unconfigured bundle may contain only profiles", path=extra))
        return ValidationReport(issues=issues, count=count, tags=tags_value, layout=layout_value)
    if partition_by not in groups:
        issues.append(
            issue(
                "unknown_partition_group",
                "partition_by must name an existing tag group",
                path="profiles/layout.json#/partition_by",
                group=partition_by,
            )
        )
        for extra in root_children:
            issues.append(issue("unexpected_workspace_entry", "Workspace contains an unexpected root entry", path=extra))
        return ValidationReport(issues=issues, count=count, tags=tags_value, layout=layout_value)

    partition_names = [item["tag"] for item in groups[partition_by]]
    partition_tags = set(partition_names)
    folded_partitions: dict[str, str] = {}
    for tag in partition_names:
        problem = component_problem(tag, allow_profiles=False)
        if problem is not None:
            issues.append(issue(problem[0], problem[1], path="profiles/tags.json", tag=tag))
        try:
            too_long = len(tag.encode("utf-8")) > maximum
        except UnicodeEncodeError:
            too_long = True
        if too_long:
            issues.append(issue("partition_name_too_long", "Partition tag exceeds max_component_length", path="profiles/tags.json", tag=tag))
        if layout_value["unit_name_strategy"] == "partition-title-date":
            try:
                insufficient_capacity = len(tag.encode("utf-8")) + 2 + 8 + 1 > maximum
            except UnicodeEncodeError:
                insufficient_capacity = True
            if insufficient_capacity:
                issues.append(
                    issue(
                        "insufficient_unit_name_capacity",
                        "Partition tag and date leave no room for a semantic title",
                        path="profiles/layout.json#/max_component_length",
                        tag=tag,
                    )
                )
        key = tag.casefold()
        previous = folded_partitions.get(key)
        if previous is not None and previous != tag:
            issues.append(issue("partition_casefold_collision", "Partition tags collide under case folding", path="profiles/tags.json", tags=[previous, tag]))
        folded_partitions[key] = tag

    for partition_entry in root_entries:
        if partition_entry.name == "profiles":
            continue
        partition_label = partition_entry.name
        try:
            partition_metadata = partition_entry.stat(follow_symlinks=False)
        except OSError as exc:
            issues.append(issue("path_unreadable", "Partition entry could not be inspected", path=partition_label, os_error=str(exc)))
            continue
        if is_reparse_metadata(partition_metadata) or not stat.S_ISDIR(partition_metadata.st_mode):
            issues.append(issue("partition_directory_required", "Bundle root may contain only real partition directories", path=partition_label))
            continue
        if partition_entry.name not in partition_tags:
            issues.append(issue("unregistered_partition", "Partition directory must equal a tag in the configured group", path=partition_label))
            continue
        unit_entries = _scan(Path(partition_entry.path), partition_label, issues)
        if not unit_entries:
            issues.append(issue("empty_partition", "Partition directories must not be empty", path=partition_label))
            continue
        unit_collisions: dict[str, str] = {}
        for unit_entry in unit_entries:
            unit_label = f"{partition_label}/{unit_entry.name}"
            try:
                unit_metadata = unit_entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(issue("path_unreadable", "Knowledge-unit entry could not be inspected", path=unit_label, os_error=str(exc)))
                continue
            if is_reparse_metadata(unit_metadata) or not stat.S_ISDIR(unit_metadata.st_mode):
                issues.append(issue("unit_directory_required", "Partitions may contain only real knowledge-unit directories", path=unit_label))
                continue
            count += 1
            key = unit_entry.name.casefold()
            previous = unit_collisions.get(key)
            if previous is not None and previous != unit_entry.name:
                issues.append(issue("record_casefold_collision", "Knowledge-unit folders collide under case folding", path=partition_label, names=[previous, unit_entry.name]))
            unit_collisions[key] = unit_entry.name
            issues.extend(
                validate_record_directory(
                    Path(unit_entry.path),
                    unit_entry.name,
                    registered=all_registered,
                    maximum=maximum,
                    label_root=partition_label,
                    partition_tags=partition_tags,
                    expected_partition=partition_entry.name,
                )
            )
    return ValidationReport(issues=issues, count=count, tags=tags_value, layout=layout_value)


__all__ = ["ValidationReport", "validate_record_directory", "validate_workspace"]
