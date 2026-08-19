"""Registry v1 validation and resolution for a Cortex KB root."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import PROFILE_FILENAMES, REGISTRY_FILENAME, REGISTRY_VERSION, ROOT_LOCK_FILENAME
from .errors import CortexError, issue, validation_error
from .jsonio import json_bytes, loads_object
from .native import component_problem, exists, is_reparse_metadata, native_path
from .validation import validate_workspace

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class RegistryReport:
    issues: list[dict[str, Any]]
    value: dict[str, Any] | None

    @property
    def valid(self) -> bool:
        return not self.issues


def _strict_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for key in sorted(expected - set(value)):
        problems.append(issue("missing_field", f"Missing required field: {key}", path=label, field=key))
    for key in sorted(set(value) - expected):
        problems.append(issue("unknown_field", f"Unknown field: {key}", path=label, field=key))
    return problems


def canonical_registry(value: dict[str, Any]) -> dict[str, Any]:
    bundles = value.get("bundles")
    if not isinstance(bundles, list):
        return value
    ordered: list[dict[str, Any]] = []
    for entry in bundles:
        if not isinstance(entry, dict):
            return value
        ordered.append({"id": entry.get("id"), "path": entry.get("path"), "description": entry.get("description")})
    return {"version": REGISTRY_VERSION, "bundles": ordered}


def validate_registry_value(value: dict[str, Any], *, label: str = REGISTRY_FILENAME) -> list[dict[str, Any]]:
    problems = _exact_keys(value, {"version", "bundles"}, label)
    if type(value.get("version")) is not int or value.get("version") != REGISTRY_VERSION:
        problems.append(issue("invalid_registry_version", "Registry version must be integer 1", path=label))
    bundles = value.get("bundles")
    if not isinstance(bundles, list):
        problems.append(issue("invalid_registry_bundles", "bundles must be an ordered array", path=label))
        return problems
    exact_ids: set[str] = set()
    folded_ids: dict[str, str] = {}
    exact_paths: set[str] = set()
    folded_paths: dict[str, str] = {}
    for index, entry in enumerate(bundles):
        entry_label = f"{label}#/bundles/{index}"
        if not isinstance(entry, dict):
            problems.append(issue("invalid_registry_entry", "Each bundle entry must be an object", path=entry_label))
            continue
        problems.extend(_exact_keys(entry, {"id", "path", "description"}, entry_label))
        bundle_id = entry.get("id")
        if not _strict_text(bundle_id) or not bundle_id or not _ID.fullmatch(bundle_id) or len(bundle_id.encode("utf-8")) > 64:
            problems.append(issue("invalid_bundle_id", "Bundle id must be lowercase kebab-case of at most 64 UTF-8 bytes", path=entry_label))
        else:
            if bundle_id in exact_ids:
                problems.append(issue("duplicate_bundle_id", "Bundle ids must be exactly unique", path=entry_label, id=bundle_id))
            folded = bundle_id.casefold()
            if folded in folded_ids and folded_ids[folded] != bundle_id:
                problems.append(issue("bundle_id_casefold_collision", "Bundle ids must be unique under case folding", path=entry_label, ids=[folded_ids[folded], bundle_id]))
            exact_ids.add(bundle_id)
            folded_ids[folded] = bundle_id
        bundle_path = entry.get("path")
        path_valid = _strict_text(bundle_path) and bool(bundle_path)
        if path_valid:
            path_valid = component_problem(bundle_path) is None and Path(bundle_path).name == bundle_path
            folded_path = bundle_path.casefold()
            path_valid = path_valid and not folded_path.endswith(".migrating") and not folded_path.endswith(".retired")
        if not path_valid:
            problems.append(issue("invalid_bundle_path", "Bundle path must be one safe direct-child component and not staging or retired", path=entry_label))
        else:
            if bundle_path in exact_paths:
                problems.append(issue("duplicate_bundle_path", "Bundle paths must be exactly unique", path=entry_label, bundle_path=bundle_path))
            folded = bundle_path.casefold()
            if folded in folded_paths and folded_paths[folded] != bundle_path:
                problems.append(issue("bundle_path_casefold_collision", "Bundle paths must be unique under case folding", path=entry_label, paths=[folded_paths[folded], bundle_path]))
            exact_paths.add(bundle_path)
            folded_paths[folded] = bundle_path
        if not _strict_text(entry.get("description")):
            problems.append(issue("invalid_bundle_description", "Bundle description must be a string", path=entry_label))
    return problems


def _complete_profiles(directory: Path) -> bool:
    profiles = directory / "profiles"
    try:
        metadata = os.lstat(native_path(profiles))
        if is_reparse_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            return False
        names = {entry.name for entry in os.scandir(native_path(profiles))}
        return set(PROFILE_FILENAMES) <= names
    except OSError:
        return False


def validate_registry(root: Path, *, value_override: dict[str, Any] | None = None, payload_override: bytes | None = None) -> RegistryReport:
    root = Path(os.path.abspath(root))
    problems: list[dict[str, Any]] = []
    try:
        metadata = os.lstat(native_path(root))
    except OSError:
        return RegistryReport([issue("kb_root_not_found", "KB root does not exist", path=str(root))], None)
    if is_reparse_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
        return RegistryReport([issue("kb_root_not_directory", "KB root must be a real directory", path=str(root))], None)

    reading_durable_registry = value_override is None
    value = value_override
    payload = payload_override
    registry_path = root / REGISTRY_FILENAME
    if value is None:
        try:
            file_metadata = os.lstat(native_path(registry_path))
            if is_reparse_metadata(file_metadata) or not stat.S_ISREG(file_metadata.st_mode):
                return RegistryReport([issue("registry_not_ordinary", "registry.json must be an ordinary file", path=REGISTRY_FILENAME)], None)
            payload = registry_path.read_bytes()
            value = loads_object(payload, label=REGISTRY_FILENAME)
        except FileNotFoundError:
            return RegistryReport([issue("registry_not_initialized", "KB root has no registry.json", path=REGISTRY_FILENAME)], None)
        except CortexError as exc:
            return RegistryReport([exc.as_issue()], None)
        except OSError as exc:
            return RegistryReport([issue("registry_unreadable", "registry.json could not be read", path=REGISTRY_FILENAME, os_error=str(exc))], None)

    if reading_durable_registry:
        lock_path = root / ROOT_LOCK_FILENAME
        try:
            lock_metadata = os.lstat(native_path(lock_path))
            if is_reparse_metadata(lock_metadata) or not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_size != 0:
                problems.append(issue("invalid_root_lock", "KB-root lock must be an ordinary zero-byte file", path=ROOT_LOCK_FILENAME))
        except FileNotFoundError:
            problems.append(issue("missing_root_lock", "Registered KB root is missing its stable lock", path=ROOT_LOCK_FILENAME))
        except OSError as exc:
            problems.append(issue("root_lock_unreadable", "KB-root lock could not be inspected", path=ROOT_LOCK_FILENAME, os_error=str(exc)))

    problems.extend(validate_registry_value(value))
    if problems:
        return RegistryReport(problems, value)
    canonical = canonical_registry(value)
    if payload is not None and payload != json_bytes(canonical):
        problems.append(issue("noncanonical_registry_json", "registry.json must use canonical Cortex JSON", path=REGISTRY_FILENAME))

    registered = {entry["path"] for entry in canonical["bundles"]}
    for entry in canonical["bundles"]:
        target = root / entry["path"]
        report = validate_workspace(target)
        if not report.valid:
            problems.append(issue("invalid_registered_bundle", "Registered target must be a valid Cortex Bundle", path=entry["path"], issues=report.issues))
    try:
        for entry in os.scandir(native_path(root)):
            if entry.name in {REGISTRY_FILENAME, ROOT_LOCK_FILENAME, ".git"} or entry.name in registered:
                continue
            try:
                child_metadata = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not is_reparse_metadata(child_metadata) and stat.S_ISDIR(child_metadata.st_mode) and _complete_profiles(Path(entry.path)):
                problems.append(issue("orphan_bundle", "Direct child with complete profiles is not registered", path=entry.name))
    except OSError as exc:
        problems.append(issue("kb_root_unreadable", "KB root could not be scanned", path=str(root), os_error=str(exc)))
    return RegistryReport(problems, canonical)


def validate_transition(previous: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    old_by_id = {entry["id"]: entry for entry in canonical_registry(previous)["bundles"]}
    new_by_id = {entry["id"]: entry for entry in canonical_registry(candidate)["bundles"]}
    problems: list[dict[str, Any]] = []
    for bundle_id, old in old_by_id.items():
        new = new_by_id.get(bundle_id)
        if new is None:
            problems.append(issue("bundle_removal_forbidden", "Existing registry pairs cannot be removed", path=REGISTRY_FILENAME, id=bundle_id))
        elif new["path"] != old["path"]:
            problems.append(issue("bundle_reassignment_forbidden", "Existing bundle ids cannot be reassigned", path=REGISTRY_FILENAME, id=bundle_id, expected=old["path"], actual=new["path"]))
    return problems


def require_registry(root: Path) -> dict[str, Any]:
    report = validate_registry(root)
    if report.issues:
        first = report.issues[0]
        raise validation_error(first["message"], first["code"], path=first.get("path"), issues=report.issues)
    assert report.value is not None
    return report.value


def resolve_bundle(root: Path, bundle_id: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    value = require_registry(root) if registry is None else registry
    for entry in value["bundles"]:
        if entry["id"] == bundle_id:
            return entry
    raise validation_error("Bundle id is not registered", "unknown_bundle_id", id=bundle_id)


__all__ = ["RegistryReport", "canonical_registry", "require_registry", "resolve_bundle", "validate_registry", "validate_registry_value", "validate_transition"]
