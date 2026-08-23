"""Strict, dependency-free filesystem engine for Cortex Notes."""

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


VERSION = "1.0.0"
DEFAULT_ROOT = Path(r"C:\Users\fuyin\Desktop\anti-entropy\notes")
DEFAULT_TOOLS_ROOT = Path(r"C:\Users\fuyin\Desktop\anti-entropy\tools")
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
NOTE_KEYS = {"version", "id", "title", "created_at", "bundle_id", "partition", "tags", "body"}
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+08:00$")
DATE_RE = re.compile(r"^\d{8}$")
SAFE_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class NotesError(Exception):
    def __init__(self, status: str, code: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.data = data or {}


def result(command: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "ok", "exit_code": 0, "command": command, "data": data or {}, "issues": []}


def failure(command: str, exc: NotesError) -> dict[str, Any]:
    return {
        "status": exc.status, "exit_code": RESULT_CODES[exc.status], "command": command,
        "data": exc.data, "issues": [{"code": exc.code}],
    }


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
    exists = target.exists() or target.is_symlink()
    if exists:
        try:
            info = target.lstat()
        except OSError as exc:
            raise NotesError("io_error", "path_unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise NotesError("validation_error", "linked_or_reparse_path")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise NotesError("validation_error", "nonordinary_path")
    if not exists and not may_be_missing:
        raise NotesError("validation_error", "path_missing")
    current = target.parent
    while current.parent != current:
        _ordinary(current, directory=True)
        current = current.parent
    _ordinary(current, directory=True)
    return target


def _safe_component(value: str) -> str:
    if not isinstance(value, str) or value == "" or len(value) > 120 or value[-1:] in (" ", "."):
        raise NotesError("validation_error", "unsafe_component")
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value or not SAFE_RE.fullmatch(value) or value in (".", ".."):
        raise NotesError("validation_error", "unsafe_component")
    if value.split(".", 1)[0].upper() in RESERVED:
        raise NotesError("validation_error", "reserved_component")
    return value


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
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise NotesError("validation_error", "linked_or_reparse_path")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise NotesError("validation_error", "nonordinary_path")
    return values


def _json(path: Path) -> dict[str, Any]:
    _ordinary(path, directory=False)
    try:
        return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object)
    except NotesError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NotesError("validation_error", "invalid_json") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NotesError("validation_error", "duplicate_json_key")
        value[key] = item
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))


def registry_value() -> dict[str, Any]:
    return {"version": 1, "bundles": [
        {"id": bundle, "path": bundle, "description": description} for bundle, description in REGISTRY_ORDER
    ]}


def bundle_value(bundle: str, values: tuple[str, ...] | None = None) -> dict[str, Any]:
    descriptions = dict(REGISTRY_ORDER)
    if bundle == "daily-notes":
        partition: dict[str, Any] = {"strategy": "date", "format": "YYYYMMDD", "timezone": "Asia/Hong_Kong"}
    elif bundle == "tools-feedback":
        partition = {"strategy": "allowlist", "values": list(values or TOOL_PARTITIONS)}
    elif bundle == "ideas":
        partition = {"strategy": "allowlist", "values": list(IDEA_PARTITIONS)}
    else:
        raise NotesError("usage_error", "unknown_bundle")
    return {
        "version": 1, "id": bundle, "description": descriptions[bundle],
        "tag_strategy": "partition", "partition": partition,
    }


def _validate_registry(value: object) -> None:
    if value != registry_value():
        raise NotesError("validation_error", "registry_schema_invalid")


def _validate_bundle(value: object, bundle: str) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) != {"version", "id", "description", "tag_strategy", "partition"}:
        raise NotesError("validation_error", "bundle_schema_invalid")
    partition = value.get("partition")
    if isinstance(partition, dict) and isinstance(partition.get("values"), list):
        raw_values = partition["values"]
        if all(isinstance(item, str) for item in raw_values) and len(raw_values) != len({item.casefold() for item in raw_values}):
            raise NotesError("validation_error", "casefold_collision")
    if bundle == "tools-feedback":
        if not isinstance(partition, dict) or set(partition) != {"strategy", "values"} or partition["strategy"] != "allowlist":
            raise NotesError("validation_error", "bundle_schema_invalid")
        values = partition["values"]
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise NotesError("validation_error", "bundle_schema_invalid")
        if len(values) != len(set(item.casefold() for item in values)) or tuple(values[:len(TOOL_PARTITIONS)]) != TOOL_PARTITIONS:
            raise NotesError("validation_error", "tools_allowlist_not_monotonic")
        expected = bundle_value(bundle, tuple(values))
    else:
        expected = bundle_value(bundle)
        values = partition.get("values", []) if isinstance(partition, dict) else []
    if value != expected:
        raise NotesError("validation_error", "bundle_schema_invalid")
    return tuple(values)


def _canonical_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="microseconds")
    if not TIMESTAMP_RE.fullmatch(value):
        raise NotesError("validation_error", "timestamp_invalid")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise NotesError("validation_error", "timestamp_invalid") from exc
    return value


def _slug(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    pieces: list[str] = []
    dash = False
    for char in normalized:
        if char.isalnum() or char in ("_", "-") or (ord(char) > 127 and not char.isspace()):
            pieces.append(char)
            dash = False
        elif not dash and pieces:
            pieces.append("-")
            dash = True
    value = "".join(pieces).strip("-.") or "note"
    return value[:80].rstrip("-.") or "note"


def _partition_values(root: Path, bundle: str) -> tuple[str, ...]:
    value = _json(root / bundle / "bundle.json")
    return _validate_bundle(value, bundle)


def _check_tool(tools_root: Path, partition: str) -> None:
    _safe_component(partition)
    tools = _absolute(tools_root)
    candidates = {entry.name.casefold(): entry for entry in _entries(tools) if entry.is_dir()}
    candidate = candidates.get(partition.casefold())
    if candidate is None or candidate.name != partition:
        raise NotesError("validation_error", "tool_repository_missing")
    try:
        _ordinary(candidate, directory=True)
        _ordinary(candidate / ".git", directory=True)
    except NotesError as exc:
        raise NotesError("validation_error", "tool_repository_missing") from exc


def _partition_allowed(root: Path, bundle: str, partition: str, tools_root: Path, *, add: bool) -> None:
    _safe_component(partition)
    values = _partition_values(root, bundle)
    if bundle == "daily-notes":
        if not DATE_RE.fullmatch(partition):
            raise NotesError("validation_error", "daily_partition_invalid")
        try:
            datetime.strptime(partition, "%Y%m%d")
        except ValueError as exc:
            raise NotesError("validation_error", "daily_partition_invalid") from exc
        return
    if partition not in values:
        raise NotesError("validation_error", "partition_not_configured")
    if bundle == "tools-feedback" and add:
        _check_tool(tools_root, partition)


def _preflight(root: Path, bundle: str) -> tuple[str, ...]:
    """Validate the Registry and selected Bundle's required partition skeleton without mutation."""
    _validate_registry(_json(root / "registry.json"))
    values = _partition_values(root, bundle)
    observed: set[str] = set()
    for entry in _entries(root / bundle):
        if entry.name == "bundle.json":
            _ordinary(entry, directory=False)
            continue
        _ordinary(entry, directory=True)
        _partition_allowed(root, bundle, entry.name, DEFAULT_TOOLS_ROOT, add=False)
        if bundle != "daily-notes" and entry.name not in values:
            raise NotesError("validation_error", "partition_not_configured")
        try:
            _ordinary(entry / "archive", directory=True)
        except NotesError as exc:
            if exc.code == "path_missing":
                raise NotesError("validation_error", "archive_missing") from exc
            raise
        observed.add(entry.name)
    if bundle != "daily-notes" and not set(values).issubset(observed):
        raise NotesError("validation_error", "configured_partition_missing")
    return values


def _note_metadata(path: Path, bundle: str, partition: str, note: str) -> dict[str, Any]:
    value = _json(path / "note.json")
    if not isinstance(value, dict) or set(value) != NOTE_KEYS:
        raise NotesError("validation_error", "note_schema_invalid")
    timestamp = value.get("created_at")
    expected = {
        "version": 1, "id": note, "title": value.get("title"), "created_at": timestamp,
        "bundle_id": bundle, "partition": partition, "tags": [partition], "body": "note.md",
    }
    if value != expected or not isinstance(value.get("title"), str) or value["title"] == "":
        raise NotesError("validation_error", "note_schema_invalid")
    _canonical_timestamp(timestamp if isinstance(timestamp, str) else "")
    if not note.startswith(timestamp[:10].replace("-", "") + "-" + timestamp[11:19].replace(":", "") + timestamp[20:26] + "-"):
        raise NotesError("validation_error", "note_identity_invalid")
    if bundle == "daily-notes" and partition != timestamp[:10].replace("-", ""):
        raise NotesError("validation_error", "note_identity_invalid")
    return value


def _unit(path: Path, bundle: str, partition: str, note: str) -> tuple[dict[str, Any], bytes]:
    _safe_component(note)
    entries = _entries(path)
    if [item.name for item in entries] != ["note.json", "note.md"]:
        raise NotesError("validation_error", "note_unit_shape_invalid")
    metadata = _note_metadata(path, bundle, partition, note)
    _ordinary(path / "note.md", directory=False)
    try:
        body = (path / "note.md").read_bytes()
        body.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise NotesError("validation_error", "note_body_invalid") from exc
    return metadata, body


def _digest(root: Path, bundle: str, partition: str, note: str, archived: bool, path: Path) -> str:
    _metadata, body = _unit(path, bundle, partition, note)
    try:
        metadata_raw = (path / "note.json").read_bytes()
    except OSError as exc:
        raise NotesError("io_error", "note_changed_during_scan") from exc
    _metadata_second, body_second = _unit(path, bundle, partition, note)
    try:
        metadata_second = (path / "note.json").read_bytes()
    except OSError as exc:
        raise NotesError("io_error", "note_changed_during_scan") from exc
    if metadata_raw != metadata_second or body != body_second:
        raise NotesError("validation_error", "note_changed_during_scan")
    info = _ordinary(root, directory=True)
    assert info is not None
    payload = [
        b"CORTEX_NOTE_TREE_V1\0", os.path.normcase(str(root)).encode("utf-8"), b"\0",
        str(info.st_dev).encode(), b":", str(info.st_ino).encode(), b"\0",
        bundle.encode(), b"\0", partition.encode(), b"\0", note.encode(), b"\0",
        (b"archive" if archived else b"active"), b"\0",
        metadata_raw, b"\0", body,
    ]
    return hashlib.sha256(b"".join(payload)).hexdigest()


def _note_path(root: Path, bundle: str, partition: str, note: str, archived: bool) -> Path:
    bundle_path = root / bundle
    partition_path = bundle_path / partition
    _ordinary(bundle_path, directory=True)
    _ordinary(partition_path, directory=True)
    parent = partition_path / "archive" if archived else partition_path
    _ordinary(parent, directory=True)
    path = parent / note
    _ordinary(path, directory=True)
    return path


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    _ordinary(root, directory=True)
    lock = root / ".notes.lock"
    info = _ordinary(lock, directory=False)
    assert info is not None
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
        root.mkdir()
        (root / ".notes.lock").write_bytes(b"0")
        _write_json(root / "registry.json", registry_value())
    except OSError as exc:
        raise NotesError("io_error", "registry_init_failed") from exc
    return result("notes.registry.init", {"root": str(root)})


def registry_show(root: Path) -> dict[str, Any]:
    root = _absolute(root)
    value = _json(root / "registry.json"); _validate_registry(value)
    return result("notes.registry.show", {"registry": value})


def registry_resolve(root: Path, bundle: str) -> dict[str, Any]:
    root = _absolute(root)
    _preflight(root, bundle)
    return result("notes.registry.resolve", {"bundle_id": bundle, "path": str(root / bundle)})


def bundle_init(root: Path, bundle: str, tools_root: Path) -> dict[str, Any]:
    root = _absolute(root); _validate_registry(_json(root / "registry.json"))
    value = bundle_value(bundle)
    target = root / bundle
    with _lock(root):
        _validate_registry(_json(root / "registry.json"))
        if bundle == "tools-feedback":
            for partition in TOOL_PARTITIONS: _check_tool(tools_root, partition)
        if target.exists(): raise NotesError("validation_error", "bundle_exists")
        try:
            target.mkdir(); _write_json(target / "bundle.json", value)
            for partition in value["partition"].get("values", []):
                archive = target / partition / "archive"; archive.mkdir(parents=True); (archive / ".gitkeep").write_bytes(b"")
        except OSError as exc:
            raise NotesError("io_error", "bundle_init_failed") from exc
    return result("notes.bundle.init", {"bundle_id": bundle})


def bundle_show(root: Path, bundle: str) -> dict[str, Any]:
    root = _absolute(root); values = _preflight(root, bundle)
    return result("notes.bundle.show", {"bundle": _json(root / bundle / "bundle.json"), "partitions": list(values)})


def bundle_resolve(root: Path, bundle: str) -> dict[str, Any]:
    shown = bundle_show(root, bundle)
    shown["command"] = "notes.bundle.resolve"
    shown["data"]["path"] = str(_absolute(root) / bundle)
    return shown


def partition_add(root: Path, bundle: str, partition: str, tools_root: Path) -> dict[str, Any]:
    if bundle != "tools-feedback": raise NotesError("usage_error", "partition_expand_tools_only")
    root = _absolute(root)
    with _lock(root):
        values = _preflight(root, bundle)
        _check_tool(tools_root, partition)
        if partition in values: raise NotesError("validation_error", "partition_exists")
        if partition.casefold() in {item.casefold() for item in values}: raise NotesError("validation_error", "casefold_collision")
        path = root / bundle / partition
        try:
            archive = path / "archive"; archive.mkdir(parents=True); (archive / ".gitkeep").write_bytes(b"")
            candidate = bundle_value(bundle, (*values, partition))
            temp = root / bundle / f".bundle-{uuid.uuid4().hex}.tmp"
            _write_json(temp, candidate); os.replace(temp, root / bundle / "bundle.json")
        except OSError as exc: raise NotesError("io_error", "partition_add_failed") from exc
    return result("notes.bundle.partition.add", {"bundle_id": bundle, "partition": partition})


def validate(root: Path, bundle: str | None = None) -> dict[str, Any]:
    root = _absolute(root); _validate_registry(_json(root / "registry.json"))
    expected_root = {".notes.lock", "registry.json", *(item[0] for item in REGISTRY_ORDER)}
    for entry in _entries(root):
        if entry.name == ".git":
            _ordinary(entry, directory=True)
        elif entry.name not in expected_root:
            raise NotesError("validation_error", "notes_root_shape_invalid")
        elif entry.name in {".notes.lock", "registry.json"}:
            _ordinary(entry, directory=False)
        else:
            _ordinary(entry, directory=True)
    if not expected_root.issubset({entry.name for entry in _entries(root)}):
        raise NotesError("validation_error", "notes_root_shape_invalid")
    bundles = [bundle] if bundle else [item[0] for item in REGISTRY_ORDER]
    for bundle_id in bundles:
        bundle_path = root / bundle_id; values = _preflight(root, bundle_id)
        observed_partitions: set[str] = set()
        for entry in _entries(bundle_path):
            if entry.name == "bundle.json": _ordinary(entry, directory=False); continue
            _ordinary(entry, directory=True); partition = entry.name
            _partition_allowed(root, bundle_id, partition, DEFAULT_TOOLS_ROOT, add=False)
            if bundle_id != "daily-notes" and partition not in values: raise NotesError("validation_error", "partition_not_configured")
            children = _entries(entry)
            if "archive" not in {child.name for child in children}:
                raise NotesError("validation_error", "archive_missing")
            for child in children:
                if child.name == "archive":
                    _ordinary(child, directory=True)
                    for archived in _entries(child):
                        if archived.name == ".gitkeep":
                            _ordinary(archived, directory=False)
                            if archived.read_bytes() != b"": raise NotesError("validation_error", "archive_marker_invalid")
                        else: _unit(archived, bundle_id, partition, archived.name)
                else: _unit(child, bundle_id, partition, child.name)
            observed_partitions.add(partition)
        if bundle_id != "daily-notes" and not set(values).issubset(observed_partitions):
            raise NotesError("validation_error", "configured_partition_missing")
    return result("notes.bundle.validate" if bundle else "notes.registry.validate", {"valid": True})


def note_add(root: Path, tools_root: Path, bundle: str, partition: str | None, title: str, body_file: Path, timestamp: str | None) -> dict[str, Any]:
    root = _absolute(root); created = _canonical_timestamp(timestamp)
    if not isinstance(title, str) or title.strip() == "": raise NotesError("validation_error", "title_invalid")
    title = title.strip(); body_file = _absolute(body_file); _ordinary(body_file, directory=False)
    try: body = body_file.read_bytes(); body.decode("utf-8")
    except (OSError, UnicodeError) as exc: raise NotesError("validation_error", "note_body_invalid") from exc
    if bundle == "daily-notes":
        derived = created[:10].replace("-", "")
        if partition not in (None, derived): raise NotesError("validation_error", "daily_partition_mismatch")
        partition = derived
    if partition is None: raise NotesError("usage_error", "partition_required")
    note = created[:10].replace("-", "") + "-" + created[11:19].replace(":", "") + created[20:26] + "-" + _slug(title)
    _safe_component(note)
    metadata = {"version": 1, "id": note, "title": title, "created_at": created, "bundle_id": bundle, "partition": partition, "tags": [partition], "body": "note.md"}
    with _lock(root):
        _preflight(root, bundle)
        _partition_allowed(root, bundle, partition, tools_root, add=True)
        base = root / bundle / partition
        if not base.exists():
            if bundle != "daily-notes": raise NotesError("validation_error", "configured_partition_missing")
            try:
                archive = base / "archive"; archive.mkdir(parents=True); (archive / ".gitkeep").write_bytes(b"")
            except OSError as exc: raise NotesError("io_error", "partition_create_failed") from exc
        else:
            _ordinary(base, directory=True)
            _ordinary(base / "archive", directory=True)
        destination = base / note
        names = {entry.name.casefold() for entry in _entries(base)}
        if note.casefold() in names: raise NotesError("validation_error", "note_exists")
        staging = base / (".staging-" + uuid.uuid4().hex)
        try:
            staging.mkdir(); (staging / "note.md").write_bytes(body); _write_json(staging / "note.json", metadata)
            _unit(staging, bundle, partition, note)
            os.rename(staging, destination)
        except OSError as exc:
            try:
                for child in staging.iterdir(): child.unlink()
                staging.rmdir()
            except OSError: pass
            raise NotesError("io_error", "note_add_failed") from exc
    digest = _digest(root, bundle, partition, note, False, destination)
    return result("notes.note.add", {"bundle_id": bundle, "partition": partition, "note": note, "tree_sha256": digest})


def note_list(root: Path, bundle: str, partition: str, archived: bool | None) -> dict[str, Any]:
    root = _absolute(root); _preflight(root, bundle); _partition_allowed(root, bundle, partition, DEFAULT_TOOLS_ROOT, add=False)
    base = root / bundle / partition; notes: list[dict[str, Any]] = []
    if not base.exists():
        if bundle != "daily-notes": raise NotesError("validation_error", "configured_partition_missing")
        return result("notes.note.list", {"notes": []})
    _ordinary(base, directory=True)
    _ordinary(base / "archive", directory=True)
    locations = [(False, base), (True, base / "archive")] if archived is None else [(archived, base / "archive" if archived else base)]
    for is_archived, location in locations:
        for entry in _entries(location):
            if entry.name in ("archive", ".gitkeep"): continue
            metadata, _body = _unit(entry, bundle, partition, entry.name)
            notes.append({"id": entry.name, "title": metadata["title"], "archived": is_archived, "tree_sha256": _digest(root, bundle, partition, entry.name, is_archived, entry)})
    return result("notes.note.list", {"notes": notes})


def note_show(root: Path, bundle: str, partition: str, note: str, archived: bool) -> dict[str, Any]:
    root = _absolute(root); _preflight(root, bundle); _partition_allowed(root, bundle, partition, DEFAULT_TOOLS_ROOT, add=False)
    path = _note_path(root, bundle, partition, note, archived); metadata, body = _unit(path, bundle, partition, note)
    return result("notes.note.show", {"metadata": metadata, "body": body.decode("utf-8"), "archived": archived, "tree_sha256": _digest(root, bundle, partition, note, archived, path)})


def _authorized(root: Path, bundle: str, partition: str, note: str, archived: bool, expected: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", expected): raise NotesError("usage_error", "expected_tree_sha256_invalid")
    path = _note_path(root, bundle, partition, note, archived)
    if _digest(root, bundle, partition, note, archived, path) != expected: raise NotesError("validation_error", "stale_note_state")
    return path


def note_edit(root: Path, bundle: str, partition: str, note: str, archived: bool, body_file: Path, expected: str) -> dict[str, Any]:
    root = _absolute(root); body_file = _absolute(body_file); _ordinary(body_file, directory=False)
    try: body = body_file.read_bytes(); body.decode("utf-8")
    except (OSError, UnicodeError) as exc: raise NotesError("validation_error", "note_body_invalid") from exc
    with _lock(root):
        _preflight(root, bundle)
        path = _authorized(root, bundle, partition, note, archived, expected); metadata_bytes = (path / "note.json").read_bytes()
        temp = path / (".body-" + uuid.uuid4().hex + ".tmp")
        try: temp.write_bytes(body); os.replace(temp, path / "note.md")
        except OSError as exc: raise NotesError("io_error", "note_edit_failed") from exc
        if (path / "note.json").read_bytes() != metadata_bytes: raise NotesError("io_error", "metadata_changed")
    return result("notes.note.edit", {"tree_sha256": _digest(root, bundle, partition, note, archived, path)})


def note_archive(root: Path, bundle: str, partition: str, note: str, expected: str) -> dict[str, Any]:
    root = _absolute(root)
    with _lock(root):
        _preflight(root, bundle)
        source = _authorized(root, bundle, partition, note, False, expected); destination = root / bundle / partition / "archive" / note
        if note.casefold() in {entry.name.casefold() for entry in _entries(destination.parent)}:
            raise NotesError("validation_error", "archive_destination_exists")
        try: os.rename(source, destination)
        except OSError as exc: raise NotesError("io_error", "note_archive_failed") from exc
    return result("notes.note.archive", {"archived": True, "tree_sha256": _digest(root, bundle, partition, note, True, destination)})


def note_delete(root: Path, bundle: str, partition: str, note: str, archived: bool, expected: str, confirmed: str) -> dict[str, Any]:
    if confirmed != "yes": raise NotesError("usage_error", "delete_confirmation_required")
    root = _absolute(root)
    with _lock(root):
        _preflight(root, bundle)
        path = _authorized(root, bundle, partition, note, archived, expected); started = False
        try:
            for name in ("note.md", "note.json"):
                (path / name).unlink(); started = True
            path.rmdir()
        except OSError as exc:
            residue = []
            if path.exists():
                try: residue = [item.name for item in _entries(path)]
                except NotesError: residue = ["<unreadable>"]
            raise NotesError("io_error", "note_delete_partial" if started else "note_delete_failed", data={"residue": residue}) from exc
    return result("notes.note.delete", {"deleted": True})
