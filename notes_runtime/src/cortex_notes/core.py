"""Strict, dependency-free filesystem engine for Cortex Notes 2."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid
from typing import Any, Iterator


VERSION = "2.0.0"
RESULT_CODES = {"ok": 0, "usage_error": 2, "validation_error": 3, "busy": 5, "io_error": 6}
REGISTRY_ORDER = (
    ("daily-notes", "Daily notes"),
    ("tools-feedback", "Tool feedback and update ideas"),
    ("ideas", "New tool and concept ideas"),
)
TOOL_PARTITIONS = (
    "agent-dev-utilities", "cortex", "default-workflow-framework", "file-processing", "ibd-utilities",
)
IDEA_PARTITIONS = ("new-tools-and-functions", "preliminary-concepts-and-proofs")
NOTE_KEYS = ("version", "id", "title", "created_at", "bundle_id", "partition", "tags", "body")
NOTE_PROFILE = {
    "version": 1,
    "fields": {
        "version": "integer", "id": "string", "title": "string", "created_at": "string",
        "bundle_id": "string", "partition": "string", "tags": "string-list", "body": "string",
    },
    "required": list(NOTE_KEYS),
}
PROFILE_FILES = {"note": "note-schema.json", "tags": "tags.json", "layout": "layout.json"}
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+08:00$")
DATE_RE = re.compile(r"^\d{8}$")
SAFE_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}
PARTITION_RESERVED = {"profiles", "archive", ".git", ".notes.lock", "registry.json", "bundle.json"}
RUNTIME_TEMP_PREFIXES = (".partition-", ".staging-", ".body-", ".tags-", ".notes-")


def _tags(group: str, values: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {"version": 2, "groups": [{"name": group, "tags": [
        {"tag": tag, "description": description} for tag, description in values
    ]}]}


DEFAULT_PROFILE_TABLE: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {
    "daily-notes": (
        NOTE_PROFILE,
        {"version": 2, "groups": []},
        {"version": 1, "partition": {"strategy": "date", "format": "YYYYMMDD", "timezone": "Asia/Hong_Kong"},
         "unit_name_strategy": "timestamp-title", "duplicate_name_strategy": "reject", "archive_directory": "archive"},
    ),
    "tools-feedback": (
        NOTE_PROFILE,
        _tags("tool", tuple((tag, f"Feedback for {tag}") for tag in TOOL_PARTITIONS)),
        {"version": 1, "partition": {"strategy": "tag-group", "group": "tool", "expansion": "keyed-monotonic",
                                       "admission": "git-repository-under-tools-root"},
         "unit_name_strategy": "timestamp-title", "duplicate_name_strategy": "reject", "archive_directory": "archive"},
    ),
    "ideas": (
        NOTE_PROFILE,
        _tags("idea", ((IDEA_PARTITIONS[0], "New tools and functions"),
                       (IDEA_PARTITIONS[1], "Preliminary concepts and proofs"))),
        {"version": 1, "partition": {"strategy": "tag-group", "group": "idea", "expansion": "keyed-monotonic",
                                       "admission": "safe-component"},
         "unit_name_strategy": "timestamp-title", "duplicate_name_strategy": "reject", "archive_directory": "archive"},
    ),
}


class NotesError(Exception):
    def __init__(self, status: str, code: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.data = data or {}


def result(command: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "ok", "exit_code": 0, "command": command, "data": data or {}, "issues": []}


def failure(command: str, exc: NotesError) -> dict[str, Any]:
    return {"status": exc.status, "exit_code": RESULT_CODES[exc.status], "command": command,
            "data": exc.data, "issues": [{"code": exc.code}]}


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _ordinary(path: Path, *, directory: bool, missing: bool = False) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise NotesError("validation_error", "path_missing")
    except OSError as exc:
        raise NotesError("io_error", "path_unreadable") from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise NotesError("validation_error", "linked_or_reparse_path")
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected:
        raise NotesError("validation_error", "nonordinary_path")
    return info


def _absolute(path: Path, *, may_be_missing: bool = False) -> Path:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise NotesError("usage_error", "absolute_path_required")
    target = Path(os.path.abspath(path))
    try:
        info = target.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise NotesError("io_error", "path_unreadable") from exc
    if info is None and not may_be_missing:
        raise NotesError("validation_error", "path_missing")
    if info is not None and (stat.S_ISLNK(info.st_mode) or _is_reparse(info)):
        raise NotesError("validation_error", "linked_or_reparse_path")
    if info is not None and not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise NotesError("validation_error", "nonordinary_path")
    current = target.parent
    while current.parent != current:
        _ordinary(current, directory=True)
        current = current.parent
    _ordinary(current, directory=True)
    return target


def _strict_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True


def _safe_component(value: str) -> str:
    if not isinstance(value, str) or value == "" or len(value) > 120 or value[-1:] in (" ", "."):
        raise NotesError("validation_error", "unsafe_component")
    if unicodedata.normalize("NFKC", value) != value or value in (".", "..") or not SAFE_RE.fullmatch(value):
        raise NotesError("validation_error", "unsafe_component")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise NotesError("validation_error", "unsafe_component")
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        raise NotesError("validation_error", "reserved_component")
    return value


def _partition_component(value: str) -> str:
    _safe_component(value)
    folded = value.casefold()
    if folded in PARTITION_RESERVED or any(folded.startswith(prefix) for prefix in RUNTIME_TEMP_PREFIXES):
        raise NotesError("validation_error", "reserved_partition")
    return value


def _child(parent: Path, component: str, *, partition: bool = False) -> Path:
    (_partition_component if partition else _safe_component)(component)
    candidate = Path(os.path.abspath(parent / component))
    expected_parent = Path(os.path.abspath(parent))
    try:
        contained = os.path.commonpath((str(candidate), str(expected_parent))) == str(expected_parent)
    except ValueError:
        contained = False
    if not contained or candidate.parent != expected_parent:
        raise NotesError("validation_error", "path_escape")
    return candidate


def _entries(path: Path) -> list[Path]:
    _ordinary(path, directory=True)
    try:
        values = sorted(path.iterdir(), key=lambda item: item.name.encode("utf-8"))
    except OSError as exc:
        raise NotesError("io_error", "directory_unreadable") from exc
    seen: set[str] = set()
    for item in values:
        _safe_component(item.name)
        folded = item.name.casefold()
        if folded in seen:
            raise NotesError("validation_error", "casefold_collision")
        seen.add(folded)
        try:
            info = item.lstat()
        except OSError as exc:
            raise NotesError("io_error", "path_unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise NotesError("validation_error", "linked_or_reparse_path")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise NotesError("validation_error", "nonordinary_path")
    return values


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NotesError("validation_error", "duplicate_json_key")
        value[key] = item
    return value


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    _ordinary(path, directory=False)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except NotesError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NotesError("validation_error", "invalid_json") from exc
    if not isinstance(value, dict):
        raise NotesError("validation_error", "json_object_required")
    return value, raw


def _json(path: Path) -> dict[str, Any]:
    return _read_json(path)[0]


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_json_bytes(value))


def registry_value() -> dict[str, Any]:
    return {"version": 1, "bundles": [
        {"id": bundle, "path": bundle, "description": description} for bundle, description in REGISTRY_ORDER
    ]}


def _validate_registry(value: object) -> None:
    if value != registry_value():
        raise NotesError("validation_error", "registry_schema_invalid")


def _exact_keys(value: dict[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise NotesError("validation_error", code)


def _validate_note_profile(value: object) -> None:
    if value != NOTE_PROFILE:
        raise NotesError("validation_error", "note_profile_invalid")


def _validate_tags_profile(value: object) -> None:
    if not isinstance(value, dict):
        raise NotesError("validation_error", "tags_profile_invalid")
    _exact_keys(value, {"version", "groups"}, "tags_profile_invalid")
    if type(value["version"]) is not int or value["version"] != 2 or not isinstance(value["groups"], list):
        raise NotesError("validation_error", "tags_profile_invalid")
    group_names: set[str] = set()
    tag_names: set[str] = set()
    for group in value["groups"]:
        if not isinstance(group, dict):
            raise NotesError("validation_error", "tags_profile_invalid")
        _exact_keys(group, {"name", "tags"}, "tags_profile_invalid")
        name = group["name"]
        if not _strict_text(name) or not name:
            raise NotesError("validation_error", "tags_profile_invalid")
        _safe_component(name)
        folded_group = name.casefold()
        if folded_group in group_names:
            raise NotesError("validation_error", "casefold_collision")
        group_names.add(folded_group)
        if not isinstance(group["tags"], list) or not group["tags"]:
            raise NotesError("validation_error", "tags_profile_invalid")
        for item in group["tags"]:
            if not isinstance(item, dict):
                raise NotesError("validation_error", "tags_profile_invalid")
            _exact_keys(item, {"tag", "description"}, "tags_profile_invalid")
            tag, description = item["tag"], item["description"]
            if not _strict_text(tag) or not tag or not _strict_text(description):
                raise NotesError("validation_error", "tags_profile_invalid")
            _partition_component(tag)
            folded_tag = tag.casefold()
            if folded_tag in tag_names:
                raise NotesError("validation_error", "casefold_collision")
            tag_names.add(folded_tag)


def _validate_layout_profile(value: object) -> None:
    if not isinstance(value, dict):
        raise NotesError("validation_error", "layout_profile_invalid")
    _exact_keys(value, {"version", "partition", "unit_name_strategy", "duplicate_name_strategy", "archive_directory"},
                "layout_profile_invalid")
    if type(value["version"]) is not int or value["version"] != 1:
        raise NotesError("validation_error", "layout_profile_invalid")
    if value["unit_name_strategy"] != "timestamp-title" or value["duplicate_name_strategy"] != "reject":
        raise NotesError("validation_error", "layout_profile_invalid")
    if value["archive_directory"] != "archive":
        raise NotesError("validation_error", "layout_profile_invalid")
    partition = value["partition"]
    if not isinstance(partition, dict):
        raise NotesError("validation_error", "layout_profile_invalid")
    strategy = partition.get("strategy")
    if strategy == "date":
        if partition != {"strategy": "date", "format": "YYYYMMDD", "timezone": "Asia/Hong_Kong"}:
            raise NotesError("validation_error", "layout_profile_invalid")
    elif strategy == "tag-group":
        _exact_keys(partition, {"strategy", "group", "expansion", "admission"}, "layout_profile_invalid")
        if not _strict_text(partition["group"]) or not partition["group"]:
            raise NotesError("validation_error", "layout_profile_invalid")
        _safe_component(partition["group"])
        if partition["expansion"] != "keyed-monotonic":
            raise NotesError("validation_error", "layout_profile_invalid")
        if partition["admission"] not in {"git-repository-under-tools-root", "safe-component"}:
            raise NotesError("validation_error", "layout_profile_invalid")
    else:
        raise NotesError("validation_error", "layout_profile_invalid")


def _validate_profile_relationship(tags: dict[str, Any], layout: dict[str, Any]) -> None:
    partition = layout["partition"]
    if partition["strategy"] == "date":
        if tags != {"version": 2, "groups": []}:
            raise NotesError("validation_error", "irrelevant_tag_group")
        return
    groups = tags["groups"]
    if len(groups) != 1 or groups[0]["name"] != partition["group"]:
        raise NotesError("validation_error", "irrelevant_tag_group")


def _registered_bundle(root: Path, bundle: str) -> Path:
    registry = _json(root / "registry.json")
    _validate_registry(registry)
    if not isinstance(bundle, str) or bundle not in {item["id"] for item in registry["bundles"]}:
        raise NotesError("usage_error", "unknown_bundle")
    return _child(root, bundle, partition=True)


def _read_profiles(root: Path, bundle: str) -> tuple[Path, dict[str, dict[str, Any]], dict[str, bytes]]:
    bundle_path = _registered_bundle(root, bundle)
    matches = [entry for entry in _entries(root) if entry.name.casefold() == bundle.casefold()]
    if len(matches) != 1 or matches[0].name != bundle:
        raise NotesError("validation_error", "casefold_collision" if matches else "path_missing")
    _ordinary(bundle_path, directory=True)
    bundle_entries = _entries(bundle_path)
    if any(entry.name.casefold() == "bundle.json" for entry in bundle_entries):
        raise NotesError("validation_error", "legacy_bundle_json_rejected")
    profiles_path = _child(bundle_path, "profiles")
    _ordinary(profiles_path, directory=True)
    profile_entries = _entries(profiles_path)
    if {entry.name for entry in profile_entries} != set(PROFILE_FILES.values()):
        raise NotesError("validation_error", "profile_set_invalid")
    for entry in profile_entries:
        _ordinary(entry, directory=False)
    values: dict[str, dict[str, Any]] = {}
    raws: dict[str, bytes] = {}
    for profile, filename in PROFILE_FILES.items():
        values[profile], raws[profile] = _read_json(_child(profiles_path, filename))
    _validate_note_profile(values["note"])
    _validate_tags_profile(values["tags"])
    _validate_layout_profile(values["layout"])
    _validate_profile_relationship(values["tags"], values["layout"])
    for profile in ("note", "tags", "layout"):
        if raws[profile] != _json_bytes(values[profile]):
            raise NotesError("validation_error", "noncanonical_profile")
    return bundle_path, values, raws


def _canonical_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="microseconds")
    if not TIMESTAMP_RE.fullmatch(value):
        raise NotesError("validation_error", "timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise NotesError("validation_error", "timestamp_invalid") from exc
    if parsed.utcoffset() != timedelta(hours=8):
        raise NotesError("validation_error", "timestamp_invalid")
    return value


def _valid_date_partition(partition: str) -> None:
    _partition_component(partition)
    if not DATE_RE.fullmatch(partition):
        raise NotesError("validation_error", "daily_partition_invalid")
    try:
        datetime.strptime(partition, "%Y%m%d")
    except ValueError as exc:
        raise NotesError("validation_error", "daily_partition_invalid") from exc


def _tag_items(profiles: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    if profiles["layout"]["partition"]["strategy"] == "date":
        return []
    return profiles["tags"]["groups"][0]["tags"]


def _configured_partitions(profiles: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    return tuple(item["tag"] for item in _tag_items(profiles))


def _marker(archive: Path) -> None:
    marker = _child(archive, ".gitkeep")
    _ordinary(marker, directory=False)
    try:
        if marker.read_bytes() != b"":
            raise NotesError("validation_error", "archive_marker_invalid")
    except OSError as exc:
        raise NotesError("io_error", "path_unreadable") from exc


def _partition_shape(path: Path, bundle: str, partition: str, profiles: dict[str, dict[str, Any]], *, deep: bool) -> None:
    _ordinary(path, directory=True)
    children = _entries(path)
    named = {child.name: child for child in children}
    archive_name = profiles["layout"]["archive_directory"]
    if archive_name not in named:
        raise NotesError("validation_error", "archive_missing")
    _ordinary(named[archive_name], directory=True)
    archive_children = _entries(named[archive_name])
    if ".gitkeep" not in {child.name for child in archive_children}:
        raise NotesError("validation_error", "archive_marker_missing")
    _marker(named[archive_name])
    for child in children:
        if child.name == archive_name:
            continue
        _ordinary(child, directory=True)
        if deep:
            _unit(child, bundle, partition, child.name, profiles)
    for child in archive_children:
        if child.name == ".gitkeep":
            continue
        _ordinary(child, directory=True)
        if deep:
            _unit(child, bundle, partition, child.name, profiles)


def _canonical_empty_skeleton(path: Path, profiles: dict[str, dict[str, Any]]) -> bool:
    try:
        children = _entries(path)
        if [child.name for child in children] != [profiles["layout"]["archive_directory"]]:
            return False
        archive = children[0]
        _ordinary(archive, directory=True)
        archived = _entries(archive)
        if [child.name for child in archived] != [".gitkeep"]:
            return False
        _marker(archive)
        return True
    except NotesError:
        return False


def _inventory(bundle_path: Path, bundle: str, profiles: dict[str, dict[str, Any]], *, deep: bool,
               tolerate_residue: bool = False) -> tuple[tuple[str, ...], tuple[str, ...]]:
    observed: list[str] = []
    residue: list[str] = []
    configured = _configured_partitions(profiles)
    configured_set = set(configured)
    configured_fold = {item.casefold(): item for item in configured}
    strategy = profiles["layout"]["partition"]["strategy"]
    for entry in _entries(bundle_path):
        if entry.name == "profiles":
            _ordinary(entry, directory=True)
            continue
        if entry.name.casefold() == "bundle.json":
            raise NotesError("validation_error", "legacy_bundle_json_rejected")
        _partition_component(entry.name)
        _ordinary(entry, directory=True)
        if strategy == "date":
            _valid_date_partition(entry.name)
            _partition_shape(entry, bundle, entry.name, profiles, deep=deep)
        elif entry.name not in configured_set:
            if entry.name.casefold() in configured_fold:
                raise NotesError("validation_error", "casefold_collision")
            if tolerate_residue and _canonical_empty_skeleton(entry, profiles):
                residue.append(entry.name)
            elif tolerate_residue:
                raise NotesError("validation_error", "unregistered_partition_not_resumable")
            else:
                residue.append(entry.name)
        else:
            _partition_shape(entry, bundle, entry.name, profiles, deep=deep)
        observed.append(entry.name)
    if strategy == "tag-group":
        missing = [item for item in configured if item not in observed]
        if missing:
            raise NotesError("validation_error", "configured_partition_missing", data={"missing_partitions": missing})
    if residue and not tolerate_residue:
        raise NotesError("validation_error", "unregistered_partition_residue",
                         data={"residual_partitions": sorted(residue, key=str.casefold)})
    ordered = tuple(sorted(observed, key=lambda item: item.encode("utf-8"))) if strategy == "date" else configured
    return ordered, tuple(sorted(residue, key=lambda item: item.encode("utf-8")))


def _preflight(root: Path, bundle: str, *, deep: bool = False) -> tuple[Path, dict[str, dict[str, Any]], dict[str, bytes], tuple[str, ...]]:
    bundle_path, profiles, raws = _read_profiles(root, bundle)
    partitions, _residue = _inventory(bundle_path, bundle, profiles, deep=deep)
    return bundle_path, profiles, raws, partitions


def _check_tool(tools_root: Path | None, partition: str) -> None:
    _partition_component(partition)
    if tools_root is None:
        raise NotesError("usage_error", "tools_root_required")
    tools = _absolute(tools_root)
    _ordinary(tools, directory=True)
    matches = [entry for entry in _entries(tools) if entry.name.casefold() == partition.casefold()]
    if len(matches) != 1 or matches[0].name != partition:
        raise NotesError("validation_error", "tool_repository_missing")
    candidate = matches[0]
    try:
        _ordinary(candidate, directory=True)
        _ordinary(_child(candidate, ".git"), directory=True)
    except NotesError as exc:
        raise NotesError("validation_error", "tool_repository_missing") from exc


def _partition_allowed(profiles: dict[str, dict[str, Any]], partition: str, tools_root: Path | None, *, add: bool) -> None:
    policy = profiles["layout"]["partition"]
    if policy["strategy"] == "date":
        _valid_date_partition(partition)
        return
    _partition_component(partition)
    if partition not in _configured_partitions(profiles):
        raise NotesError("validation_error", "partition_not_configured")
    if add and policy["admission"] == "git-repository-under-tools-root":
        _check_tool(tools_root, partition)


def _publish_skeleton(bundle_path: Path, partition: str, profiles: dict[str, dict[str, Any]]) -> bool:
    destination = _child(bundle_path, partition, partition=True)
    matches = [entry for entry in _entries(bundle_path) if entry.name.casefold() == partition.casefold()]
    if matches:
        if len(matches) == 1 and matches[0].name == partition and _canonical_empty_skeleton(destination, profiles):
            return False
        if any(entry.name != partition for entry in matches):
            raise NotesError("validation_error", "casefold_collision")
        raise NotesError("validation_error", "partition_exists")
    stage = _child(bundle_path, ".partition-" + uuid.uuid4().hex)
    try:
        stage.mkdir()
        archive = _child(stage, profiles["layout"]["archive_directory"])
        archive.mkdir()
        _child(archive, ".gitkeep").write_bytes(b"")
        os.rename(stage, destination)
    except OSError as exc:
        try:
            marker = stage / profiles["layout"]["archive_directory"] / ".gitkeep"
            if marker.exists(): marker.unlink()
            archive = stage / profiles["layout"]["archive_directory"]
            if archive.exists(): archive.rmdir()
            if stage.exists(): stage.rmdir()
        except OSError:
            pass
        raise NotesError("io_error", "partition_publish_failed") from exc
    return True


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    _ordinary(root, directory=True)
    lock = _child(root, ".notes.lock")
    _ordinary(lock, directory=False)
    handle = open(lock, "r+b", buffering=0)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise NotesError("busy", "notes_root_busy") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def registry_init(root: Path) -> dict[str, Any]:
    root = _absolute(root, may_be_missing=True)
    if root.exists():
        raise NotesError("validation_error", "notes_root_exists")
    try:
        root.mkdir(); _child(root, ".notes.lock").write_bytes(b"0")
        _write_json(_child(root, "registry.json"), registry_value())
    except OSError as exc:
        raise NotesError("io_error", "registry_init_failed") from exc
    return result("notes.registry.init", {"root": str(root)})


def registry_show(root: Path) -> dict[str, Any]:
    root = _absolute(root); value = _json(_child(root, "registry.json")); _validate_registry(value)
    return result("notes.registry.show", {"registry": value})


def registry_resolve(root: Path, bundle: str) -> dict[str, Any]:
    root = _absolute(root); bundle_path, _profiles, _raws, _partitions = _preflight(root, bundle)
    return result("notes.registry.resolve", {"bundle_id": bundle, "path": str(bundle_path)})


def bundle_init(root: Path, bundle: str, tools_root: Path | None = None) -> dict[str, Any]:
    root = _absolute(root); target = _registered_bundle(root, bundle); defaults = DEFAULT_PROFILE_TABLE[bundle]
    with _lock(root):
        _validate_registry(_json(_child(root, "registry.json")))
        if target.exists(): raise NotesError("validation_error", "bundle_exists")
        if bundle.casefold() in {entry.name.casefold() for entry in _entries(root)}:
            raise NotesError("validation_error", "casefold_collision")
        profiles = {"note": defaults[0], "tags": defaults[1], "layout": defaults[2]}
        policy = profiles["layout"]["partition"]
        if policy["strategy"] == "tag-group" and policy["admission"] == "git-repository-under-tools-root":
            for partition in _configured_partitions(profiles): _check_tool(tools_root, partition)
        try:
            target.mkdir(); profile_path = _child(target, "profiles"); profile_path.mkdir()
            for profile in ("note", "tags", "layout"):
                _write_json(_child(profile_path, PROFILE_FILES[profile]), profiles[profile])
            for partition in _configured_partitions(profiles): _publish_skeleton(target, partition, profiles)
        except NotesError:
            raise
        except OSError as exc:
            raise NotesError("io_error", "bundle_init_failed") from exc
    return result("notes.bundle.init", {"bundle_id": bundle})


def bundle_show(root: Path, bundle: str) -> dict[str, Any]:
    root = _absolute(root); _path, profiles, _raws, partitions = _preflight(root, bundle)
    return result("notes.bundle.show", {"bundle_id": bundle,
        "profiles": {profile: profiles[profile] for profile in ("note", "tags", "layout")},
        "partitions": list(partitions)})


def bundle_resolve(root: Path, bundle: str) -> dict[str, Any]:
    shown = bundle_show(root, bundle); shown["command"] = "notes.bundle.resolve"
    shown["data"]["path"] = str(_child(_absolute(root), bundle, partition=True)); return shown


def bundle_config_show(root: Path, bundle: str, profile: str) -> dict[str, Any]:
    if profile not in PROFILE_FILES: raise NotesError("usage_error", "unknown_profile")
    root = _absolute(root); _path, profiles, _raws, _partitions = _preflight(root, bundle)
    return result("notes.bundle.config.show", {"profile": profile, "value": profiles[profile]})


def _workflow_data(completed: list[str], failed: str | None, residue: list[str], updated: bool) -> dict[str, Any]:
    return {"completed_steps": list(completed), "failed_step": failed,
            "residual_partitions": list(residue), "profile_updated": updated}


def bundle_config_set(root: Path, bundle: str, profile: str, candidate_file: Path,
                      tools_root: Path | None = None) -> dict[str, Any]:
    if profile not in PROFILE_FILES: raise NotesError("usage_error", "unknown_profile")
    if profile != "tags": raise NotesError("usage_error", "profile_immutable")
    completed: list[str] = []; residue: list[str] = []; failed_step: str | None = "validate_request"; updated = False
    try:
        root = _absolute(root)
        failed_step = "acquire_lock"
        with _lock(root):
            failed_step = "reload_profiles"
            bundle_path, profiles, raws = _read_profiles(root, bundle)
            _partitions, observed_residue = _inventory(bundle_path, bundle, profiles, deep=False, tolerate_residue=True)
            residue = list(observed_residue); failed_step = "validate_candidate"
            candidate_file = _absolute(candidate_file); _ordinary(candidate_file, directory=False)
            candidate, _candidate_raw = _read_json(candidate_file)
            _validate_tags_profile(candidate); _validate_profile_relationship(candidate, profiles["layout"])
            current_items = _tag_items(profiles); candidate_profiles = {**profiles, "tags": candidate}
            candidate_items = _tag_items(candidate_profiles)
            current_keys = [item["tag"] for item in current_items]; candidate_keys = [item["tag"] for item in candidate_items]
            if candidate_keys[:len(current_keys)] != current_keys or len(candidate_keys) < len(current_keys):
                raise NotesError("validation_error", "tags_not_keyed_monotonic")
            new_keys = [item["tag"] for item in candidate_items[len(current_items):]]
            if any(item not in new_keys for item in residue):
                raise NotesError("validation_error", "unregistered_partition_residue",
                                 data={"residual_partitions": list(residue)})
            failed_step = "validate_admission"; policy = profiles["layout"]["partition"]
            if policy["strategy"] == "tag-group" and policy["admission"] == "git-repository-under-tools-root":
                for item in new_keys: _check_tool(tools_root, item)
            for item in new_keys:
                failed_step = f"partition:{item}"; path = _child(bundle_path, item, partition=True)
                if path.exists():
                    if item not in residue or not _canonical_empty_skeleton(path, profiles):
                        raise NotesError("validation_error", "partition_exists")
                else:
                    _publish_skeleton(bundle_path, item, profiles)
                completed.append(f"partition:{item}")
                if item not in residue: residue.append(item)
            candidate_bytes = _json_bytes(candidate)
            if candidate_bytes != raws["tags"]:
                failed_step = "profiles/tags.json"; profile_path = _child(bundle_path, "profiles")
                temp = _child(profile_path, ".tags-" + uuid.uuid4().hex + ".tmp")
                try:
                    temp.write_bytes(candidate_bytes); os.replace(temp, _child(profile_path, PROFILE_FILES["tags"]))
                except OSError as exc:
                    try:
                        if temp.exists(): temp.unlink()
                    except OSError: pass
                    raise NotesError("io_error", "tags_profile_replace_failed") from exc
                completed.append("profiles/tags.json"); updated = True; residue = []
            failed_step = None
    except NotesError as exc:
        exc.data = {**exc.data, **_workflow_data(completed, failed_step, residue, updated)}; raise
    return result("notes.bundle.config.set", {"profile": "tags", "value": candidate,
        **_workflow_data(completed, None, residue, updated)})


def validate(root: Path, bundle: str | None = None) -> dict[str, Any]:
    root = _absolute(root); registry = _json(_child(root, "registry.json")); _validate_registry(registry)
    if bundle is None:
        expected = {".notes.lock", "registry.json", *(item[0] for item in REGISTRY_ORDER)}; observed: set[str] = set()
        for entry in _entries(root):
            if entry.name == ".git": _ordinary(entry, directory=True); continue
            if entry.name not in expected: raise NotesError("validation_error", "notes_root_shape_invalid")
            _ordinary(entry, directory=entry.name not in {".notes.lock", "registry.json"}); observed.add(entry.name)
        if observed != expected: raise NotesError("validation_error", "notes_root_shape_invalid")
        bundles = [item[0] for item in REGISTRY_ORDER]
    else:
        bundles = [bundle]
    for bundle_id in bundles: _preflight(root, bundle_id, deep=True)
    return result("notes.bundle.validate" if bundle else "notes.registry.validate", {"valid": True})


def _slug(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower(); pieces: list[str] = []; dash = False
    for char in normalized:
        if char.isalnum() or char in ("_", "-") or (ord(char) > 127 and not char.isspace()):
            pieces.append(char); dash = False
        elif not dash and pieces:
            pieces.append("-"); dash = True
    value = "".join(pieces).strip("-.") or "note"; return value[:80].rstrip("-.") or "note"


def _note_metadata(path: Path, bundle: str, partition: str, note: str,
                   profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _validate_note_profile(profiles["note"]); value = _json(_child(path, "note.json"))
    if tuple(value) != NOTE_KEYS or set(value) != set(NOTE_KEYS): raise NotesError("validation_error", "note_schema_invalid")
    timestamp = value.get("created_at")
    expected = {"version": 1, "id": note, "title": value.get("title"), "created_at": timestamp,
                "bundle_id": bundle, "partition": partition, "tags": [partition], "body": "note.md"}
    if value != expected or type(value.get("version")) is not int or not _strict_text(value.get("title")) or not value["title"]:
        raise NotesError("validation_error", "note_schema_invalid")
    _canonical_timestamp(timestamp if isinstance(timestamp, str) else "")
    prefix = timestamp[:10].replace("-", "") + "-" + timestamp[11:19].replace(":", "") + timestamp[20:26] + "-"
    if not note.startswith(prefix): raise NotesError("validation_error", "note_identity_invalid")
    if profiles["layout"]["partition"]["strategy"] == "date" and partition != timestamp[:10].replace("-", ""):
        raise NotesError("validation_error", "note_identity_invalid")
    return value


def _unit(path: Path, bundle: str, partition: str, note: str,
          profiles: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bytes]:
    _safe_component(note); entries = _entries(path)
    if [item.name for item in entries] != ["note.json", "note.md"]: raise NotesError("validation_error", "note_unit_shape_invalid")
    metadata = _note_metadata(path, bundle, partition, note, profiles); _ordinary(_child(path, "note.md"), directory=False)
    try:
        body = _child(path, "note.md").read_bytes(); body.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise NotesError("validation_error", "note_body_invalid") from exc
    return metadata, body


def _digest(root: Path, bundle: str, partition: str, note: str, archived: bool, path: Path,
            profiles: dict[str, dict[str, Any]]) -> str:
    _metadata, body = _unit(path, bundle, partition, note, profiles)
    try: metadata_raw = _child(path, "note.json").read_bytes()
    except OSError as exc: raise NotesError("io_error", "note_changed_during_scan") from exc
    _metadata_second, body_second = _unit(path, bundle, partition, note, profiles)
    try: metadata_second = _child(path, "note.json").read_bytes()
    except OSError as exc: raise NotesError("io_error", "note_changed_during_scan") from exc
    if metadata_raw != metadata_second or body != body_second: raise NotesError("validation_error", "note_changed_during_scan")
    info = _ordinary(root, directory=True); assert info is not None
    payload = [b"CORTEX_NOTE_TREE_V1\0", os.path.normcase(str(root)).encode("utf-8"), b"\0",
        str(info.st_dev).encode(), b":", str(info.st_ino).encode(), b"\0", bundle.encode(), b"\0",
        partition.encode(), b"\0", note.encode(), b"\0", (b"archive" if archived else b"active"), b"\0",
        metadata_raw, b"\0", body]
    return hashlib.sha256(b"".join(payload)).hexdigest()


def _note_path(bundle_path: Path, partition: str, note: str, archived: bool,
               profiles: dict[str, dict[str, Any]]) -> Path:
    partition_path = _child(bundle_path, partition, partition=True); _ordinary(partition_path, directory=True)
    parent = _child(partition_path, profiles["layout"]["archive_directory"]) if archived else partition_path
    _ordinary(parent, directory=True); path = _child(parent, note); _ordinary(path, directory=True); return path


def note_add(root: Path, tools_root: Path | None, bundle: str, partition: str | None, title: str,
             body_file: Path, timestamp: str | None) -> dict[str, Any]:
    root = _absolute(root); created = _canonical_timestamp(timestamp)
    if not isinstance(title, str) or title.strip() == "": raise NotesError("validation_error", "title_invalid")
    title = title.strip(); body_file = _absolute(body_file); _ordinary(body_file, directory=False)
    try: body = body_file.read_bytes(); body.decode("utf-8")
    except (OSError, UnicodeError) as exc: raise NotesError("validation_error", "note_body_invalid") from exc
    with _lock(root):
        bundle_path, profiles, _raws, _partitions = _preflight(root, bundle)
        if profiles["layout"]["partition"]["strategy"] == "date":
            derived = created[:10].replace("-", "")
            if partition not in (None, derived): raise NotesError("validation_error", "daily_partition_mismatch")
            partition = derived
        if partition is None: raise NotesError("usage_error", "partition_required")
        _partition_allowed(profiles, partition, tools_root, add=True)
        note = created[:10].replace("-", "") + "-" + created[11:19].replace(":", "") + created[20:26] + "-" + _slug(title)
        _safe_component(note); base = _child(bundle_path, partition, partition=True)
        if not base.exists():
            if profiles["layout"]["partition"]["strategy"] != "date": raise NotesError("validation_error", "configured_partition_missing")
            _publish_skeleton(bundle_path, partition, profiles)
        else: _partition_shape(base, bundle, partition, profiles, deep=False)
        destination = _child(base, note); archive = _child(base, profiles["layout"]["archive_directory"])
        names = {entry.name.casefold() for entry in _entries(base)} | {entry.name.casefold() for entry in _entries(archive)}
        if note.casefold() in names: raise NotesError("validation_error", "note_exists")
        metadata = {"version": 1, "id": note, "title": title, "created_at": created, "bundle_id": bundle,
                    "partition": partition, "tags": [partition], "body": "note.md"}
        staging = _child(base, ".staging-" + uuid.uuid4().hex)
        try:
            staging.mkdir(); _child(staging, "note.md").write_bytes(body); _write_json(_child(staging, "note.json"), metadata)
            _unit(staging, bundle, partition, note, profiles); os.rename(staging, destination)
        except (OSError, NotesError) as exc:
            try:
                for child in (staging / "note.md", staging / "note.json"):
                    if child.exists(): child.unlink()
                if staging.exists(): staging.rmdir()
            except OSError: pass
            if isinstance(exc, NotesError): raise
            raise NotesError("io_error", "note_add_failed") from exc
    digest = _digest(root, bundle, partition, note, False, destination, profiles)
    return result("notes.note.add", {"bundle_id": bundle, "partition": partition, "note": note, "tree_sha256": digest})


def note_list(root: Path, bundle: str, partition: str, archived: bool | None) -> dict[str, Any]:
    root = _absolute(root); bundle_path, profiles, _raws, _partitions = _preflight(root, bundle)
    _partition_allowed(profiles, partition, None, add=False); base = _child(bundle_path, partition, partition=True); notes = []
    if not base.exists():
        if profiles["layout"]["partition"]["strategy"] != "date": raise NotesError("validation_error", "configured_partition_missing")
        return result("notes.note.list", {"notes": []})
    _partition_shape(base, bundle, partition, profiles, deep=False); archive = _child(base, profiles["layout"]["archive_directory"])
    locations = [(False, base), (True, archive)] if archived is None else [(archived, archive if archived else base)]
    for is_archived, location in locations:
        for entry in _entries(location):
            if entry.name in (profiles["layout"]["archive_directory"], ".gitkeep"): continue
            metadata, _body = _unit(entry, bundle, partition, entry.name, profiles)
            notes.append({"id": entry.name, "title": metadata["title"], "archived": is_archived,
                          "tree_sha256": _digest(root, bundle, partition, entry.name, is_archived, entry, profiles)})
    return result("notes.note.list", {"notes": notes})


def note_show(root: Path, bundle: str, partition: str, note: str, archived: bool) -> dict[str, Any]:
    root = _absolute(root); bundle_path, profiles, _raws, _partitions = _preflight(root, bundle)
    _partition_allowed(profiles, partition, None, add=False); path = _note_path(bundle_path, partition, note, archived, profiles)
    metadata, body = _unit(path, bundle, partition, note, profiles)
    return result("notes.note.show", {"metadata": metadata, "body": body.decode("utf-8"), "archived": archived,
        "tree_sha256": _digest(root, bundle, partition, note, archived, path, profiles)})


def _authorized(root: Path, bundle_path: Path, bundle: str, partition: str, note: str, archived: bool,
                expected: str, profiles: dict[str, dict[str, Any]]) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", expected): raise NotesError("usage_error", "expected_tree_sha256_invalid")
    path = _note_path(bundle_path, partition, note, archived, profiles)
    if _digest(root, bundle, partition, note, archived, path, profiles) != expected:
        raise NotesError("validation_error", "stale_note_state")
    return path


def note_edit(root: Path, bundle: str, partition: str, note: str, archived: bool, body_file: Path,
              expected: str) -> dict[str, Any]:
    root = _absolute(root); body_file = _absolute(body_file); _ordinary(body_file, directory=False)
    try: body = body_file.read_bytes(); body.decode("utf-8")
    except (OSError, UnicodeError) as exc: raise NotesError("validation_error", "note_body_invalid") from exc
    with _lock(root):
        bundle_path, profiles, _raws, _partitions = _preflight(root, bundle); _partition_allowed(profiles, partition, None, add=False)
        path = _authorized(root, bundle_path, bundle, partition, note, archived, expected, profiles)
        metadata_bytes = _child(path, "note.json").read_bytes(); temp = _child(path, ".body-" + uuid.uuid4().hex + ".tmp")
        try: temp.write_bytes(body); os.replace(temp, _child(path, "note.md"))
        except OSError as exc:
            try:
                if temp.exists(): temp.unlink()
            except OSError: pass
            raise NotesError("io_error", "note_edit_failed") from exc
        if _child(path, "note.json").read_bytes() != metadata_bytes: raise NotesError("io_error", "metadata_changed")
    return result("notes.note.edit", {"tree_sha256": _digest(root, bundle, partition, note, archived, path, profiles)})


def note_archive(root: Path, bundle: str, partition: str, note: str, expected: str) -> dict[str, Any]:
    root = _absolute(root)
    with _lock(root):
        bundle_path, profiles, _raws, _partitions = _preflight(root, bundle); _partition_allowed(profiles, partition, None, add=False)
        source = _authorized(root, bundle_path, bundle, partition, note, False, expected, profiles)
        destination = _child(
            _child(_child(bundle_path, partition, partition=True), profiles["layout"]["archive_directory"]), note,
        )
        if note.casefold() in {entry.name.casefold() for entry in _entries(destination.parent)}:
            raise NotesError("validation_error", "archive_destination_exists")
        try: os.rename(source, destination)
        except OSError as exc: raise NotesError("io_error", "note_archive_failed") from exc
    return result("notes.note.archive", {"archived": True,
        "tree_sha256": _digest(root, bundle, partition, note, True, destination, profiles)})


def note_delete(root: Path, bundle: str, partition: str, note: str, archived: bool, expected: str,
                confirmed: str) -> dict[str, Any]:
    if confirmed != "yes": raise NotesError("usage_error", "delete_confirmation_required")
    root = _absolute(root)
    with _lock(root):
        bundle_path, profiles, _raws, _partitions = _preflight(root, bundle); _partition_allowed(profiles, partition, None, add=False)
        path = _authorized(root, bundle_path, bundle, partition, note, archived, expected, profiles); started = False
        try:
            for name in ("note.md", "note.json"): _child(path, name).unlink(); started = True
            path.rmdir()
        except OSError as exc:
            residue = []
            if path.exists():
                try: residue = [item.name for item in _entries(path)]
                except NotesError: residue = ["<unreadable>"]
            raise NotesError("io_error", "note_delete_partial" if started else "note_delete_failed",
                             data={"residue": residue}) from exc
    return result("notes.note.delete", {"deleted": True})
