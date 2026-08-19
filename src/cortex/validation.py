"""Side-effect-free structural validation for a Cortex 5 record KB."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import DEFAULT_LAYOUT, PROFILE_FILENAMES, RECORD_SCHEMA
from .errors import CortexError, issue
from .jsonio import json_bytes, loads_object
from .native import component_problem, is_reparse_metadata, native_path
from .profiles import validate_layout_profile, validate_record, validate_record_schema, validate_tags_profile


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
        if record_payload != json_bytes(record_value):
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


def validate_workspace(workspace: Path, *, locked_record_schema: bytes | None = None) -> ValidationReport:
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
            tags_value, _ = _read_owned_json(profiles / "tags.json", "profiles/tags.json", issues)
            if tags_value is not None:
                issues.extend(validate_tags_profile(tags_value))
        if "layout.json" in profile_names:
            layout_value, _ = _read_owned_json(profiles / "layout.json", "profiles/layout.json", issues)
            if layout_value is not None:
                issues.extend(validate_layout_profile(layout_value))

    layout_valid = layout_value is not None and not validate_layout_profile(layout_value)
    records_root_name = layout_value["records_root"] if layout_valid else DEFAULT_LAYOUT["records_root"]
    allowed_root = {"profiles", records_root_name}
    for extra in sorted(root_names - allowed_root):
        issues.append(issue("unexpected_workspace_entry", "Workspace contains an unexpected root entry", path=extra))
    if records_root_name not in root_names:
        issues.append(issue("missing_records_root", "Configured records root is missing", path=records_root_name))

    count = 0
    registered: set[str] = set()
    if tags_value is not None and not validate_tags_profile(tags_value):
        registered = {item["tag"] for item in tags_value["tags"]}
    maximum = layout_value["max_component_length"] if layout_valid else DEFAULT_LAYOUT["max_component_length"]
    records_root = workspace / records_root_name
    if records_root_name in root_names and _real_directory(records_root, records_root_name, issues):
        record_entries = _scan(records_root, records_root_name, issues)
        collisions: dict[str, str] = {}
        for entry in record_entries:
            label = f"{records_root_name}/{entry.name}"
            try:
                entry_metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(issue("path_unreadable", "Record entry could not be inspected", path=label, os_error=str(exc)))
                continue
            if is_reparse_metadata(entry_metadata) or not stat.S_ISDIR(entry_metadata.st_mode):
                issues.append(issue("flat_record_directory_required", "Records root may contain only real record directories", path=label))
                continue
            count += 1
            key = entry.name.casefold()
            previous = collisions.get(key)
            if previous is not None and previous != entry.name:
                issues.append(issue("record_casefold_collision", "Record folders collide under case folding", path=records_root_name, names=[previous, entry.name]))
            collisions[key] = entry.name
            issues.extend(
                validate_record_directory(
                    Path(entry.path),
                    entry.name,
                    registered=registered,
                    maximum=maximum,
                    label_root=records_root_name,
                )
            )
    return ValidationReport(issues=issues, count=count, tags=tags_value, layout=layout_value)


__all__ = ["ValidationReport", "validate_record_directory", "validate_workspace"]
