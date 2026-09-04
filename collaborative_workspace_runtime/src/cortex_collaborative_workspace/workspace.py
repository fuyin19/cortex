"""Dependency-free Collaborative Workspace / Agent Workbench v1 engine."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from typing import Any, Iterator

from .core_runner import CoreFailure, CoreRunner, INNER_CONTRACT, OUTER_CONTRACT


VERSION = "1.2.0"
RESULT_CODES = {"ok": 0, "usage_error": 2, "validation_error": 3, "busy": 5, "io_error": 6}
OUTER_MANIFEST = "collaborative-workspace.json"
INNER_MANIFEST = ".agent-workbench.json"
OUTDATED = "_outdated"
OUTER_GUIDES = ("AGENTS.md", "CLAUDE.md")
OUTER_ROLES = ("ref", "agent-workbench")
INNER_ROLES = ("ref", "temp", "output")
DENY_FILES = frozenset({
    "agents.md", "agents.override.md", "claude.md", "claude.local.md", ".cursorrules", ".mcp.json",
})
DENY_DIRECTORIES = frozenset({".claude", ".cursor"})
WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
})
FILE_CONVERSION_SUFFIXES = frozenset({
    ".pdf", ".doc", ".docx", ".docm", ".ppt", ".pptx", ".pptm", ".pps", ".ppsx",
    ".xls", ".xlsx", ".xlsm", ".xlsb",
})
MARKDOWN_CONVERSION_SUFFIXES = frozenset({
    ".pot", ".ppsm", ".odt", ".ods", ".odp", ".rtf", ".epub", ".csv", ".html", ".json",
    ".jsonl", ".xml", ".md", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".wav", ".mp4",
    ".zip", ".txt",
})
PROVIDER_BINDINGS = {
    "file-conversion": ("FILE_CONVERSION_RUNNER", "FILE_CONVERSION_CONFIG"),
    "markdown-conversion": ("MARKDOWN_CONVERSION_RUNNER", "MARKDOWN_CONVERSION_CONFIG"),
}
PROVIDER_STDERR_LIMIT = 4096
PROVIDER_PREFLIGHT_LIMIT = 4096
PROVIDER_PREFLIGHT_TIMEOUT = 30
SAFE_COMPONENT = re.compile(r'^[^<>:"/\\|?*\x00-\x1f]+$')
_DEFAULT_PROVIDER_RUNNERS: dict[str, Path] = {}


class WorkspaceError(Exception):
    def __init__(
        self,
        status: str,
        code: str,
        *,
        data: dict[str, Any] | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.data = data or {}
        self.issues = issues or [{"code": code}]


class RuntimeFallback(Exception):
    """Private launcher signal: this interpreter lacks route dependencies."""


def result(command: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "exit_code": 0, "command": command, "data": data, "issues": []}


def failure(command: str, exc: WorkspaceError) -> dict[str, Any]:
    return {
        "status": exc.status,
        "exit_code": RESULT_CODES[exc.status],
        "command": command,
        "data": exc.data,
        "issues": exc.issues,
    }


@dataclass(frozen=True)
class Identity:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class SourceItem:
    relative: str
    kind: str
    digest: str
    source: Path

    def record(self) -> dict[str, str]:
        return {"path": self.relative, "kind": self.kind, "digest": self.digest}


@dataclass(frozen=True)
class Stage:
    root: Path
    identity: Identity
    candidate: Path
    snapshots: Path


@dataclass(frozen=True)
class ProviderBinding:
    route: str
    runner: Path
    config: Path | None
    suffixes: tuple[str, ...]
    runner_identity: Identity
    config_identity: Identity | None
    config_digest: str | None


@dataclass(frozen=True)
class Installed:
    path: Path
    identity: Identity
    kind: str
    digest: str | None = None


def _issue(code: str, path: str | None = None, **details: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code}
    if path is not None:
        value["path"] = path
    value.update(details)
    return value


def _merge_issues(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for group in groups:
        for item in group:
            key = _canonical_json(item)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_file_bytes(value: object) -> bytes:
    return _canonical_json(value) + b"\n"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity(path: Path, *, directory: bool | None = None, missing: bool = False) -> Identity | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise WorkspaceError("validation_error", "path_missing", data={"path": str(path)})
    except OSError as exc:
        raise WorkspaceError("io_error", "path_unreadable", data={"path": str(path)}) from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise WorkspaceError("validation_error", "linked_or_reparse_path", data={"path": str(path)})
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError("validation_error", "directory_required", data={"path": str(path)})
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise WorkspaceError("validation_error", "ordinary_file_required", data={"path": str(path)})
    if directory is None and not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise WorkspaceError("validation_error", "nonordinary_path", data={"path": str(path)})
    return Identity(int(info.st_dev), int(info.st_ino), int(info.st_mode))


def _same_identity(path: Path, expected: Identity) -> bool:
    try:
        return _identity(path) == expected
    except WorkspaceError:
        return False


def _absolute_root(path: Path, *, missing: bool = True) -> Path:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise WorkspaceError("usage_error", "absolute_root_required")
    target = Path(os.path.abspath(path))
    current = target if target.exists() else target.parent
    while True:
        _identity(current, directory=True)
        if current.parent == current:
            break
        current = current.parent
    found = _identity(target, directory=True, missing=True)
    if found is None and not missing:
        raise WorkspaceError("validation_error", "workspace_missing")
    return target


def _safe_component(value: str) -> None:
    if (
        value == "" or value in (".", "..") or unicodedata.normalize("NFC", value) != value
        or value[-1:] in (" ", ".") or not SAFE_COMPONENT.fullmatch(value)
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise WorkspaceError("validation_error", "unsafe_component")
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        raise WorkspaceError("validation_error", "reserved_component")


def _canonical_outdate_paths(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    canonical: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw or "\\" in raw or raw.startswith("/"):
            raise WorkspaceError("usage_error", "invalid_outdate_path")
        parts = raw.split("/")
        try:
            for part in parts:
                _safe_component(part)
        except WorkspaceError as exc:
            raise WorkspaceError("usage_error", "invalid_outdate_path") from exc
        normalized = "/".join(parts)
        key = _path_key(normalized)
        if _path_key(parts[0]) == _path_key(OUTDATED):
            raise WorkspaceError("usage_error", "invalid_outdate_path")
        if key in seen:
            raise WorkspaceError("usage_error", "duplicate_outdate_path")
        seen.add(key)
        canonical.append(normalized)
    return tuple(sorted(canonical, key=lambda value: value.encode("utf-8")))


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _entries(path: Path) -> list[Path]:
    _identity(path, directory=True)
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.name.encode("utf-8"))
    except OSError as exc:
        raise WorkspaceError("io_error", "directory_unreadable", data={"path": str(path)}) from exc
    seen: set[str] = set()
    for entry in entries:
        try:
            _safe_component(entry.name)
        except WorkspaceError as exc:
            raise WorkspaceError(exc.status, exc.code, data={"path": str(entry)}) from exc
        key = _path_key(entry.name)
        if key in seen:
            raise WorkspaceError("validation_error", "casefold_collision", data={"path": str(entry)})
        seen.add(key)
        _identity(entry)
    return entries


def _file_digest(path: Path) -> str:
    before = _identity(path, directory=False)
    assert before is not None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise WorkspaceError("io_error", "source_read_failed", data={"path": str(path)}) from exc
    if _identity(path, directory=False) != before:
        raise WorkspaceError("validation_error", "source_changed_during_scan", data={"path": str(path)})
    return digest.hexdigest()


def _tree_records_once(root: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    def visit(directory: Path, relative: Path) -> None:
        for entry in _entries(directory):
            rel = (relative / entry.name).as_posix()
            info = _identity(entry)
            assert info is not None
            if stat.S_ISDIR(info.mode):
                records.append({"kind": "directory", "path": rel})
                visit(entry, relative / entry.name)
            else:
                records.append({"digest": _file_digest(entry), "kind": "file", "path": rel})

    visit(root, Path())
    return sorted(records, key=lambda item: item["path"].encode("utf-8"))


def _tree_digest(root: Path) -> str:
    first = _tree_records_once(root)
    second = _tree_records_once(root)
    if first != second:
        raise WorkspaceError("validation_error", "tree_changed_during_scan", data={"path": str(root)})
    return _sha256(_canonical_json(first))


def _copy_file(source: Path, destination: Path, expected_digest: str | None = None) -> Installed:
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = _identity(source, directory=False)
    assert before is not None
    digest = hashlib.sha256()
    created_identity: Identity | None = None
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            created = os.fstat(outgoing.fileno())
            created_identity = Identity(int(created.st_dev), int(created.st_ino), int(created.st_mode))
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                outgoing.write(chunk)
    except OSError as exc:
        if created_identity is not None and _same_identity(destination, created_identity):
            try:
                destination.unlink()
            except OSError:
                pass
        raise WorkspaceError("io_error", "source_snapshot_failed", data={"path": str(source)}) from exc
    observed = digest.hexdigest()
    assert created_identity is not None
    copied = Installed(destination, created_identity, "file", observed)
    try:
        source_unchanged = _identity(source, directory=False) == before
    except WorkspaceError as exc:
        residue = _cleanup_installed([copied])
        if residue:
            exc.data = {**exc.data, "residue": residue}
        raise
    if not source_unchanged or (expected_digest is not None and observed != expected_digest):
        residue = _cleanup_installed([copied])
        data: dict[str, Any] = {"path": str(source)}
        if residue:
            data["residue"] = residue
        raise WorkspaceError("validation_error", "source_changed_during_snapshot", data=data)
    return copied


def _copy_tree(source: Path, destination: Path, expected_digest: str) -> None:
    destination.mkdir()

    def visit(src: Path, dst: Path) -> None:
        for entry in _entries(src):
            target = dst / entry.name
            info = _identity(entry)
            assert info is not None
            if stat.S_ISDIR(info.mode):
                target.mkdir()
                visit(entry, target)
            else:
                _copy_file(entry, target)

    visit(source, destination)
    if _tree_digest(destination) != expected_digest or _tree_digest(source) != expected_digest:
        raise WorkspaceError("validation_error", "source_changed_during_snapshot", data={"path": str(source)})


def _copy_tree_contents(source: Path, destination: Path, expected_digest: str) -> None:
    for entry in _entries(source):
        target = destination / entry.name
        info = _identity(entry)
        assert info is not None
        if stat.S_ISDIR(info.mode):
            _copy_tree(entry, target, _tree_digest(entry))
        else:
            _copy_file(entry, target, _file_digest(entry))
    if _tree_digest(destination) != expected_digest or _tree_digest(source) != expected_digest:
        raise WorkspaceError("validation_error", "source_changed_during_snapshot", data={"path": str(source)})


def _source_tree_digest(items: list[SourceItem]) -> str:
    return _sha256(_canonical_json([item.record() for item in items]))


def _records(items: list[SourceItem]) -> list[dict[str, str]]:
    return [item.record() for item in items]


def _scan_source(reference: Path | None, core: CoreRunner) -> tuple[list[SourceItem], list[dict[str, Any]]]:
    if reference is None or not reference.exists():
        return [], []
    try:
        _identity(reference, directory=True)
    except WorkspaceError as exc:
        return [], [_issue(exc.code, "ref")]
    items: list[SourceItem] = []
    issues: list[dict[str, Any]] = []

    def visit(directory: Path, relative: Path) -> None:
        try:
            entries = _entries(directory)
        except WorkspaceError as exc:
            issues.append(_issue(exc.code, relative.as_posix() or "ref"))
            return
        by_fold = {_path_key(entry.name): entry for entry in entries}
        has_agents = "agents.md" in by_fold
        has_claude = "claude.md" in by_fold
        if has_agents or has_claude:
            unit_path = relative.as_posix()
            if not (has_agents and has_claude and by_fold["agents.md"].name == "AGENTS.md"
                    and by_fold["claude.md"].name == "CLAUDE.md"):
                issues.append(_issue("instruction_control_source", unit_path or "ref"))
                return
            checked = core.knowledge_unit_validate(directory, allow_invalid=True)
            if checked["status"] != "ok":
                for item in checked["issues"] or [{"code": "invalid_knowledge_unit"}]:
                    issues.append(_issue(str(item["code"]), unit_path or "ref", source_kind="knowledge_unit"))
                return
            if not unit_path:
                issues.append(_issue("reference_root_cannot_be_knowledge_unit", "ref"))
                return
            try:
                items.append(SourceItem(unit_path, "knowledge_unit", _tree_digest(directory), directory))
            except WorkspaceError as exc:
                issues.append(_issue(exc.code, unit_path))
            return
        for entry in entries:
            rel = relative / entry.name
            canonical = rel.as_posix()
            folded = _path_key(entry.name)
            info = _identity(entry)
            assert info is not None
            if stat.S_ISDIR(info.mode):
                if not relative.parts and entry.name == OUTDATED:
                    continue
                if folded in DENY_DIRECTORIES:
                    issues.append(_issue("instruction_control_source", canonical))
                else:
                    visit(entry, rel)
            else:
                if folded in DENY_FILES:
                    issues.append(_issue("instruction_control_source", canonical))
                    continue
                try:
                    items.append(SourceItem(canonical, "file", _file_digest(entry), entry))
                except WorkspaceError as exc:
                    issues.append(_issue(exc.code, canonical))

    visit(reference, Path())
    items.sort(key=lambda item: item.relative.encode("utf-8"))
    seen: dict[str, str] = {}
    for item in items:
        key = _path_key(item.relative)
        if key in seen:
            issues.append(_issue("projection_path_collision", item.relative, other_path=seen[key]))
        seen[key] = item.relative
    paths = [item.relative.split("/") for item in items]
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if len(left) < len(right) and [_path_key(x) for x in left] == [_path_key(x) for x in right[:len(left)]]:
                issues.append(_issue("projection_path_prefix_collision", "/".join(right), other_path="/".join(left)))
    return items, issues


def _route(item: SourceItem) -> str | None:
    if item.kind == "knowledge_unit":
        return "knowledge-unit-copy"
    suffix = Path(item.relative).suffix.casefold()
    if suffix in FILE_CONVERSION_SUFFIXES:
        return "file-conversion"
    if suffix in MARKDOWN_CONVERSION_SUFFIXES:
        return "markdown-conversion"
    return None


def _ordinary_absolute_file(raw: str | None, missing_code: str, relative_code: str) -> Path:
    if not raw:
        raise WorkspaceError("usage_error", missing_code)
    path = Path(raw)
    if not path.is_absolute():
        raise WorkspaceError("usage_error", relative_code)
    absolute = Path(os.path.abspath(path))
    _identity(absolute, directory=False)
    current = absolute.parent
    while True:
        _identity(current, directory=True)
        if current.parent == current:
            break
        current = current.parent
    return absolute


def set_default_provider_runners(values: dict[str, Path]) -> None:
    global _DEFAULT_PROVIDER_RUNNERS
    _DEFAULT_PROVIDER_RUNNERS = dict(values)


def _provider_binding(route: str, suffixes: tuple[str, ...] = ()) -> ProviderBinding:
    runner_env, config_env = PROVIDER_BINDINGS[route]
    configured = os.environ.get(runner_env)
    if configured is None:
        default = _DEFAULT_PROVIDER_RUNNERS.get(route)
        configured = str(default) if default is not None else None
    runner = _ordinary_absolute_file(configured, runner_env.lower() + "_required",
                                     runner_env.lower() + "_not_absolute")
    config = None
    if config_env in os.environ:
        config = _ordinary_absolute_file(os.environ[config_env], config_env.lower() + "_required",
                                         config_env.lower() + "_not_absolute")
    runner_identity = _identity(runner, directory=False)
    assert runner_identity is not None
    config_identity = _identity(config, directory=False) if config is not None else None
    return ProviderBinding(
        route, runner, config, tuple(sorted(set(suffixes), key=lambda value: value.encode("utf-8"))),
        runner_identity, config_identity, _file_digest(config) if config is not None else None,
    )


def _provider_bindings(items: list[SourceItem]) -> dict[str, ProviderBinding]:
    suffixes: dict[str, set[str]] = {}
    paths: dict[str, str] = {}
    for item in items:
        route = _route(item)
        if route in PROVIDER_BINDINGS:
            suffixes.setdefault(route, set()).add(Path(item.relative).suffix.casefold())
            paths.setdefault(route, item.relative)
    bindings: dict[str, ProviderBinding] = {}
    for route, values in sorted(suffixes.items(), key=lambda item: item[0].encode("utf-8")):
        try:
            bindings[route] = _provider_binding(route, tuple(values))
        except WorkspaceError as exc:
            raise WorkspaceError(
                "validation_error", "projection_failed", data={"failed_items": 1},
                issues=[_issue(exc.code, paths[route], provider_route=route)],
            ) from exc
    return bindings


def _verify_provider_binding(binding: ProviderBinding) -> None:
    if not _same_identity(binding.runner, binding.runner_identity):
        raise WorkspaceError("validation_error", "provider_binding_changed", data={"provider_route": binding.route})
    if binding.config is not None:
        if not _same_identity(binding.config, binding.config_identity) or _file_digest(binding.config) != binding.config_digest:
            raise WorkspaceError("validation_error", "provider_binding_changed", data={"provider_route": binding.route})


def _provider_preflight(binding: ProviderBinding) -> None:
    _verify_provider_binding(binding)
    command = [sys.executable, "-I", "-B", str(binding.runner), "--runtime-preflight-json"]
    if binding.config is not None:
        command.extend(("--config", str(binding.config)))
    for suffix in binding.suffixes:
        command.extend(("--required-suffix", suffix))
    try:
        completed = subprocess.run(
            command, env=dict(os.environ), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=PROVIDER_PREFLIGHT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError("io_error", "provider_preflight_process_failed", data={"provider_route": binding.route}) from exc
    if len(completed.stdout) > PROVIDER_PREFLIGHT_LIMIT or len(completed.stderr) > PROVIDER_PREFLIGHT_LIMIT:
        raise WorkspaceError("validation_error", "provider_preflight_protocol_invalid", data={"provider_route": binding.route})
    try:
        payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("validation_error", "provider_preflight_protocol_invalid", data={"provider_route": binding.route}) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "status", "scope", "code"}
        or payload.get("schema_version") != 1
        or payload.get("status") not in {"ok", "error"}
        or not isinstance(payload.get("scope"), str)
        or not isinstance(payload.get("code"), str)
        or completed.stderr
    ):
        raise WorkspaceError("validation_error", "provider_preflight_protocol_invalid", data={"provider_route": binding.route})
    if completed.returncode == 0 and payload == {
        "schema_version": 1, "status": "ok", "scope": "ready", "code": "runtime_ready",
    }:
        return
    if (
        completed.returncode == 75 and payload.get("status") == "error"
        and payload.get("scope") == "python_environment"
    ):
        if os.environ.get("CORTEX_RUNTIME_FALLBACK") == "1":
            raise RuntimeFallback()
        raise WorkspaceError(
            "validation_error", "conversion_runtime_unavailable",
            data={"provider_route": binding.route, "runtime_scope": "python_environment"},
        )
    if completed.returncode not in {0, 1, 75}:
        raise WorkspaceError("validation_error", "provider_preflight_protocol_invalid", data={"provider_route": binding.route})
    raise WorkspaceError(
        "validation_error", str(payload.get("code") or "conversion_runtime_unavailable"),
        data={"provider_route": binding.route, "runtime_scope": str(payload.get("scope") or "protocol")},
    )


def _preflight_provider_bindings(bindings: dict[str, ProviderBinding]) -> None:
    for binding in bindings.values():
        _provider_preflight(binding)


def _provider_quality(unit: Path, basename: str) -> tuple[str, list[str]]:
    sidecar = unit / f"{basename}.json"
    _identity(sidecar, directory=False)
    try:
        value = json.loads(sidecar.read_bytes().decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("validation_error", "provider_metadata_invalid") from exc
    quality = value.get("quality") if isinstance(value, dict) else None
    if not isinstance(quality, dict) or quality.get("status") not in {
        "complete", "complete_with_warnings", "partial",
    }:
        raise WorkspaceError("validation_error", "provider_quality_invalid")
    raw_warnings = quality.get("warnings", [])
    if not isinstance(raw_warnings, list):
        raise WorkspaceError("validation_error", "provider_quality_invalid")
    warning_codes: list[str] = []
    for warning in raw_warnings:
        if (
            not isinstance(warning, dict)
            or not isinstance(warning.get("code"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", warning["code"]) is None
        ):
            raise WorkspaceError("validation_error", "provider_quality_invalid")
        warning_codes.append(warning["code"])
    if quality["status"] != "complete" and not warning_codes:
        warning_codes.append("provider_" + str(quality["status"]))
    return ("ready_with_warnings" if warning_codes else "ready", sorted(set(warning_codes)))


def _provider_stderr(raw: bytes) -> tuple[str, bool]:
    decoded = raw.decode("utf-8", errors="backslashreplace")
    visible = "".join(
        character
        if character in {"\n", "\t"} or unicodedata.category(character) != "Cc"
        else character.encode("unicode_escape").decode("ascii")
        for character in decoded
    )
    truncated = len(visible) > PROVIDER_STDERR_LIMIT
    return visible[-PROVIDER_STDERR_LIMIT:], truncated


def _run_provider(route: str, snapshot: Path, output_parent: Path, expected_unit: Path, core_runner: Path,
                  binding: ProviderBinding | None = None) -> tuple[str, list[str]]:
    selected = binding or _provider_binding(route, (snapshot.suffix.casefold(),))
    command = [sys.executable, "-I", "-B", str(selected.runner)]
    if selected.config is not None:
        command.extend(("--config", str(selected.config)))
    command.extend((
        "--input", str(snapshot), "--output-dir", str(output_parent),
        "--bundle-name-mode", "source-basename",
    ))
    child_env = dict(os.environ)
    child_env["ANTI_ENTROPY_CORE_RUNNER"] = str(core_runner)
    try:
        completed = subprocess.run(command, env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=1200, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError("io_error", "provider_process_failed", data={"provider_route": route}) from exc
    if completed.returncode != 0:
        data: dict[str, Any] = {"provider_route": route, "provider_exit_code": completed.returncode}
        if completed.stderr:
            excerpt, truncated = _provider_stderr(completed.stderr)
            data.update({
                "provider_stderr_excerpt": excerpt,
                "provider_stderr_truncated": truncated,
            })
        raise WorkspaceError(
            "validation_error", "provider_conversion_failed",
            data=data,
        )
    _identity(expected_unit, directory=True)
    return _provider_quality(expected_unit, snapshot.name)


def _manifest_item(item: SourceItem, unit: Path, route: str, quality: str, issues: list[str]) -> dict[str, Any]:
    return {
        "source_path": item.relative,
        "source_kind": item.kind,
        "source_digest": item.digest,
        "unit_path": item.relative,
        "prepared_digest": _tree_digest(unit),
        "provider_route": route,
        "quality": quality,
        "issues": issues,
    }


def _write_json_exclusive(path: Path, value: object) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(_json_file_bytes(value))
    except OSError as exc:
        raise WorkspaceError("io_error", "exclusive_create_failed", data={"path": str(path)}) from exc


def _new_stage(root: Path) -> Stage:
    parent = root.parent
    parent_identity = _identity(parent, directory=True)
    assert parent_identity is not None
    stage_root: Path | None = None
    try:
        stage_root = Path(tempfile.mkdtemp(prefix=".cortex-collaborative-workspace-", dir=parent))
        candidate = stage_root / "candidate"
        snapshots = stage_root / "snapshots"
        candidate.mkdir()
        snapshots.mkdir()
    except OSError as exc:
        if stage_root is not None:
            try:
                _delete_no_follow(stage_root)
            except OSError:
                pass
        raise WorkspaceError("io_error", "stage_create_failed") from exc
    stage_identity = _identity(stage_root, directory=True)
    assert stage_identity is not None
    if stage_identity.device != parent_identity.device:
        try:
            _delete_no_follow(stage_root)
        except OSError:
            pass
        raise WorkspaceError("io_error", "stage_not_same_volume")
    return Stage(stage_root, stage_identity, candidate, snapshots)


def _delete_no_follow(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        try:
            path.unlink()
        except OSError:
            path.rmdir()
        return
    if stat.S_ISDIR(info.st_mode):
        for entry in sorted(path.iterdir(), key=lambda item: item.name.encode("utf-8"), reverse=True):
            _delete_no_follow(entry)
        path.rmdir()
        return
    if stat.S_ISREG(info.st_mode):
        path.unlink()
        return
    raise OSError("nonordinary cleanup entry")


def _cleanup_stage(stage: Stage) -> list[str]:
    if not _same_identity(stage.root, stage.identity):
        return [str(stage.root)]
    try:
        _delete_no_follow(stage.root)
        return []
    except OSError:
        return [str(stage.root)]


def _outer_manifest(workspace_id: str) -> dict[str, Any]:
    return {
        "contract": OUTER_CONTRACT,
        "workspace_id": workspace_id,
        "roles": {"reference": "ref", "agent_workbench": "agent-workbench"},
    }


def _inner_manifest(
    workspace_id: str,
    generation: int,
    items: list[SourceItem],
    projected: list[dict[str, Any]],
) -> dict[str, Any]:
    warnings = sorted({warning for item in projected for warning in item["issues"]})
    quality = "ready_with_warnings" if warnings else "ready"
    return {
        "contract": INNER_CONTRACT,
        "workspace_id": workspace_id,
        "generation": generation,
        "quality": quality,
        "source_records": _records(items),
        "source_tree_digest": _source_tree_digest(items),
        "items": projected,
        "warnings": warnings,
    }


def _populate_candidate(
    stage: Stage,
    workspace_id: str,
    generation: int,
    items: list[SourceItem],
    core: CoreRunner,
    *,
    previous_ref: Path | None = None,
    retired_paths: tuple[str, ...] = (),
    batch_name: str | None = None,
    outer_archive: Path | None = None,
    provider_bindings: dict[str, ProviderBinding] | None = None,
) -> dict[str, Any]:
    candidate = stage.candidate
    _write_json_exclusive(candidate / OUTER_MANIFEST, _outer_manifest(workspace_id))
    core.workspace_stage_complete(candidate, OUTER_CONTRACT)
    candidate_outer_archive = candidate / "ref" / OUTDATED
    if outer_archive is not None:
        _copy_tree_contents(outer_archive, candidate_outer_archive, _tree_digest(outer_archive))
    workbench = candidate / "agent-workbench"
    reference = workbench / "ref"
    reference.mkdir()
    archive = reference / OUTDATED
    archive.mkdir()
    if previous_ref is not None:
        previous_archive = previous_ref / OUTDATED
        _copy_tree_contents(previous_archive, archive, _tree_digest(previous_archive))
    if retired_paths:
        if previous_ref is None or batch_name is None:
            raise WorkspaceError("io_error", "archive_candidate_invalid")
        previous_manifest = _read_json(previous_ref / INNER_MANIFEST)
        raw_items = previous_manifest.get("items")
        by_path = {
            item.get("source_path"): item
            for item in raw_items
            if isinstance(item, dict)
        } if isinstance(raw_items, list) else {}
        batch = archive / batch_name
        batch.mkdir()
        for relative in retired_paths:
            old_item = by_path.get(relative)
            prepared_digest = old_item.get("prepared_digest") if isinstance(old_item, dict) else None
            if not isinstance(prepared_digest, str):
                raise WorkspaceError("validation_error", "archive_source_manifest_invalid", data={"path": relative})
            source_unit = previous_ref.joinpath(*relative.split("/"))
            destination = batch.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_tree(source_unit, destination, prepared_digest)
    projected: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in items:
        route = _route(item)
        if route is None:
            failures.append(_issue("unsupported_source_type", item.relative))
            continue
        unit = reference.joinpath(*item.relative.split("/"))
        try:
            unit.parent.mkdir(parents=True, exist_ok=True)
            if route == "knowledge-unit-copy":
                _copy_tree(item.source, unit, item.digest)
                quality, warnings = "ready", []
            else:
                snapshot = stage.snapshots.joinpath(*item.relative.split("/"))
                _copy_file(item.source, snapshot, item.digest)
                quality, warnings = _run_provider(
                    route, snapshot, unit.parent, unit, core.path,
                    None if provider_bindings is None else provider_bindings[route],
                )
                if _file_digest(snapshot) != item.digest or _file_digest(unit / "src" / snapshot.name) != item.digest:
                    raise WorkspaceError("validation_error", "provider_source_mismatch")
            core.knowledge_unit_validate(unit)
            projected.append(_manifest_item(item, unit, route, quality, warnings))
        except CoreFailure as exc:
            failures.append(_issue(exc.code, item.relative, provider_route=route))
        except WorkspaceError as exc:
            details = {key: value for key, value in exc.data.items() if key not in {"path", "provider_route"}}
            failures.append(_issue(exc.code, item.relative, provider_route=route, **details))
    if failures:
        raise WorkspaceError("validation_error", "projection_failed", issues=failures,
                             data={"failed_items": len(failures)})
    projected.sort(key=lambda item: item["source_path"].encode("utf-8"))
    manifest = _inner_manifest(workspace_id, generation, items, projected)
    _write_json_exclusive(reference / INNER_MANIFEST, manifest)
    core.workspace_stage_complete(workbench, INNER_CONTRACT)
    core.workspace_validate(workbench, INNER_CONTRACT)
    core.workspace_validate(candidate, OUTER_CONTRACT)
    return manifest


def _build_candidate(
    root: Path,
    workspace_id: str,
    generation: int,
    items: list[SourceItem],
    core: CoreRunner,
    *,
    previous_ref: Path | None = None,
    retired_paths: tuple[str, ...] = (),
    batch_name: str | None = None,
    outer_archive: Path | None = None,
    provider_bindings: dict[str, ProviderBinding] | None = None,
) -> tuple[Stage, dict[str, Any]]:
    stage = _new_stage(root)
    try:
        return stage, _populate_candidate(
            stage,
            workspace_id,
            generation,
            items,
            core,
            previous_ref=previous_ref,
            retired_paths=retired_paths,
            batch_name=batch_name,
            outer_archive=outer_archive,
            provider_bindings=provider_bindings,
        )
    except Exception:
        _cleanup_stage(stage)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    _identity(path, directory=False)
    try:
        value = json.loads(path.read_bytes().decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("validation_error", "manifest_invalid", data={"path": str(path)}) from exc
    if not isinstance(value, dict):
        raise WorkspaceError("validation_error", "manifest_invalid", data={"path": str(path)})
    return value


def _manifest_pair(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return _read_json(root / OUTER_MANIFEST), _read_json(root / "agent-workbench" / "ref" / INNER_MANIFEST)


def _fixed_route_issues(inner_manifest: dict[str, Any], items: list[SourceItem]) -> list[dict[str, Any]]:
    issues = [
        _issue("unsupported_source_type", item.relative)
        for item in items if _route(item) is None
    ]
    if inner_manifest.get("source_records") != _records(items):
        return issues
    raw_items = inner_manifest.get("items")
    if not isinstance(raw_items, list):
        return issues
    by_path = {
        item.get("source_path"): item
        for item in raw_items if isinstance(item, dict) and isinstance(item.get("source_path"), str)
    }
    for source in items:
        expected = _route(source)
        if expected is None:
            continue
        projected = by_path.get(source.relative)
        actual = projected.get("provider_route") if isinstance(projected, dict) else None
        if actual != expected:
            issues.append(_issue(
                "provider_route_mismatch", source.relative, expected_provider_route=expected,
            ))
    return issues


def _validate_recognized(
    root: Path, core: CoreRunner,
) -> tuple[dict[str, Any], dict[str, Any], list[SourceItem]]:
    outer = core.workspace_validate(root, OUTER_CONTRACT, allow_invalid=True)
    inner_path = root / "agent-workbench"
    inner = core.workspace_validate(inner_path, INNER_CONTRACT, allow_invalid=True)
    if outer["status"] != "ok" or inner["status"] != "ok":
        issues = [
            _issue(str(item["code"]), str(item.get("path", "")))
            for response in (outer, inner) for item in response["issues"]
        ] or [_issue("workspace_invalid")]
        raise WorkspaceError("validation_error", "workspace_invalid", issues=issues, data={"state": "invalid"})
    outer_manifest, inner_manifest = _manifest_pair(root)
    outer_id = outer["data"].get("workspace_id")
    inner_id = inner["data"].get("workspace_id")
    if (
        outer_id != inner_id
        or outer_manifest.get("workspace_id") != outer_id
        or inner_manifest.get("workspace_id") != inner_id
    ):
        raise WorkspaceError(
            "validation_error", "workspace_id_mismatch", data={"state": "invalid"},
            issues=[_issue("workspace_id_mismatch")],
        )
    source_items, source_issues = _scan_source(root / "ref", core)
    route_issues = _fixed_route_issues(inner_manifest, source_items)
    recognized_issues = _merge_issues(source_issues, route_issues)
    if recognized_issues:
        raise WorkspaceError(
            "validation_error", "workspace_invalid", data={"state": "invalid"},
            issues=recognized_issues,
        )
    return outer_manifest, inner_manifest, source_items


def _literal_empty(path: Path) -> Identity:
    identity = _identity(path, directory=True)
    assert identity is not None
    try:
        if next(path.iterdir(), None) is not None:
            raise WorkspaceError("busy", "workbench_temp_not_empty", data={"state": "busy"})
    except OSError as exc:
        raise WorkspaceError("io_error", "directory_unreadable", data={"path": str(path)}) from exc
    return identity


def _reserved_adoption_issues(root: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        entries = _entries(root)
    except WorkspaceError as exc:
        return [_issue(exc.code)]
    reserved = {_path_key(name): name for name in (*OUTER_GUIDES, OUTER_MANIFEST, "agent-workbench")}
    for entry in entries:
        folded = _path_key(entry.name)
        if folded in reserved:
            issues.append(_issue("adoption_reserved_path_exists", entry.name))
        elif folded == "ref":
            if entry.name != "ref":
                issues.append(_issue("adoption_reference_name_mismatch", entry.name))
            else:
                try:
                    _identity(entry, directory=True)
                except WorkspaceError as exc:
                    issues.append(_issue(exc.code, entry.name))
        elif folded in DENY_FILES or folded in DENY_DIRECTORIES:
            issues.append(_issue("instruction_control_extra", entry.name))
        else:
            entry_identity = _identity(entry)
            assert entry_identity is not None
            if stat.S_ISDIR(entry_identity.mode):
                issues.extend(_unmanaged_tree_issues(entry, entry.name))
    return issues


def _unmanaged_tree_issues(directory: Path, prefix: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        entries = _entries(directory)
    except WorkspaceError as exc:
        return [_issue(exc.code, prefix)]
    for entry in entries:
        relative = f"{prefix}/{entry.name}"
        folded = _path_key(entry.name)
        info = _identity(entry)
        assert info is not None
        if stat.S_ISDIR(info.mode):
            if folded in DENY_DIRECTORIES:
                issues.append(_issue("instruction_control_extra", relative))
            else:
                issues.extend(_unmanaged_tree_issues(entry, relative))
        elif folded in DENY_FILES:
            issues.append(_issue("instruction_control_extra", relative))
    return issues


def _state(root: Path, core: CoreRunner) -> tuple[str, dict[str, Any]]:
    if not root.exists():
        return "uninitialized", {"state": "uninitialized", "recognized": False}
    _identity(root, directory=True)
    manifest = root / OUTER_MANIFEST
    if not manifest.exists():
        adoption_issues = _reserved_adoption_issues(root)
        if adoption_issues:
            return "invalid", {"state": "invalid", "recognized": False, "blockers": adoption_issues}
        return "uninitialized", {"state": "uninitialized", "recognized": False}
    try:
        outer, inner, items = _validate_recognized(root, core)
    except WorkspaceError as exc:
        return "invalid", {"state": "invalid", "recognized": True, "blockers": exc.issues}
    if inner.get("source_records") != _records(items):
        try:
            _literal_empty(root / "agent-workbench" / "temp")
        except WorkspaceError as exc:
            if exc.status == "busy":
                return "busy", {
                    "state": "busy", "recognized": True, "workspace_id": outer.get("workspace_id"),
                    "generation": inner.get("generation"), "blockers": exc.issues,
                }
            raise
        return "stale", {
            "state": "stale", "recognized": True, "workspace_id": outer.get("workspace_id"),
            "generation": inner.get("generation"),
        }
    quality = str(inner["quality"])
    return quality, {
        "state": quality, "recognized": True, "workspace_id": outer["workspace_id"],
        "generation": inner["generation"], "source_items": len(items), "warnings": inner["warnings"],
    }


def _lock_key(root: Path) -> str:
    return _sha256(unicodedata.normalize("NFC", os.path.normcase(str(root))).encode("utf-8"))


def _posix_lock_path(root: Path) -> Path:
    return Path(tempfile.gettempdir()) / ("cortex-collaborative-workspace-" + _lock_key(root) + ".lock")


@contextmanager
def _windows_mutex(root: Path) -> Iterator[None]:
    key = _lock_key(root)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel.ReleaseMutex.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateMutexW(None, 1, "Local\\CortexCollaborativeWorkspace-" + key)
    if not handle:
        raise WorkspaceError("io_error", "workspace_lock_failed")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise WorkspaceError("busy", "workspace_busy", data={"state": "busy"})
    try:
        yield
    finally:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


@contextmanager
def _writer_lock(root: Path) -> Iterator[None]:
    if os.name == "nt":
        with _windows_mutex(root):
            yield
        return
    import fcntl  # POSIX-only
    lock_path = _posix_lock_path(root)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError("busy", "workspace_busy", data={"state": "busy"}) from exc
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _read_lock(root: Path) -> Iterator[None]:
    """Hold writer exclusion without creating or changing filesystem state."""
    if os.name == "nt":
        with _windows_mutex(root):
            yield
        return
    import fcntl  # POSIX-only
    try:
        descriptor = os.open(_posix_lock_path(root), os.O_RDONLY)
    except FileNotFoundError:
        yield
        return
    except OSError as exc:
        raise WorkspaceError("io_error", "workspace_lock_failed") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError("busy", "workspace_busy", data={"state": "busy"}) from exc
        yield
    finally:
        os.close(descriptor)


def _installed_tree(path: Path) -> Installed:
    identity = _identity(path, directory=True)
    assert identity is not None
    return Installed(path, identity, "tree", _tree_digest(path))


def _cleanup_installed(values: list[Installed]) -> list[str]:
    residues: list[str] = []
    for value in reversed(values):
        if not _same_identity(value.path, value.identity):
            residues.append(str(value.path))
            continue
        try:
            if value.kind == "file":
                if _file_digest(value.path) != value.digest:
                    residues.append(str(value.path))
                    continue
                value.path.unlink()
            elif value.kind == "empty":
                if next(value.path.iterdir(), None) is not None:
                    residues.append(str(value.path))
                    continue
                value.path.rmdir()
            else:
                if _tree_digest(value.path) != value.digest:
                    residues.append(str(value.path))
                    continue
                _delete_no_follow(value.path)
        except (OSError, WorkspaceError):
            residues.append(str(value.path))
    return residues


def _create_missing(root: Path, core: CoreRunner) -> dict[str, Any]:
    workspace_id = str(uuid.uuid4())
    stage, manifest = _build_candidate(root, workspace_id, 1, [], core)
    published = False
    try:
        if root.exists():
            raise WorkspaceError("validation_error", "workspace_appeared_during_prepare")
        os.rename(stage.candidate, root)
        published = True
        response = result("collaborative_workspace.prepare", {
            "action": "created", "state": manifest["quality"], "workspace_id": workspace_id,
            "generation": 1, "source_items": 0,
        })
    except WorkspaceError:
        _cleanup_stage(stage)
        raise
    except OSError as exc:
        residue = _cleanup_stage(stage)
        raise WorkspaceError("io_error", "workspace_publish_failed", data={"residue": residue}) from exc
    residue = _cleanup_stage(stage)
    if residue:
        raise WorkspaceError("io_error", "stage_cleanup_failed", data={"published": published, "residue": residue})
    return response


def _adopt(root: Path, core: CoreRunner) -> dict[str, Any]:
    root_identity = _identity(root, directory=True)
    assert root_identity is not None
    blockers = _reserved_adoption_issues(root)
    reference = root / "ref" if (root / "ref").exists() else None
    reference_identity = _identity(reference, directory=True) if reference is not None else None
    outer_archive = reference / OUTDATED if reference is not None and (reference / OUTDATED).exists() else None
    outer_archive_identity = _identity(outer_archive, directory=True) if outer_archive is not None else None
    outer_archive_digest = _tree_digest(outer_archive) if outer_archive is not None else None
    items, scan_issues = _scan_source(reference, core)
    blockers.extend(scan_issues)
    blockers.extend(_issue("unsupported_source_type", item.relative) for item in items if _route(item) is None)
    if blockers:
        raise WorkspaceError("validation_error", "adoption_blocked", issues=blockers,
                             data={"state": "invalid"})
    provider_bindings = _provider_bindings(items)
    _preflight_provider_bindings(provider_bindings)
    workspace_id = str(uuid.uuid4())
    stage, manifest = _build_candidate(
        root, workspace_id, 1, items, core, outer_archive=outer_archive,
        provider_bindings=provider_bindings,
    )
    installed: list[Installed] = []
    recognized = False
    cleanup_attempted = False
    try:
        second_items, second_issues = _scan_source(reference, core)
        if second_issues or _records(second_items) != _records(items):
            raise WorkspaceError("validation_error", "source_changed_during_prepare", issues=second_issues or None)
        current_reference = root / "ref"
        reference_changed = (
            (reference_identity is None and current_reference.exists())
            or (reference_identity is not None and not _same_identity(current_reference, reference_identity))
        )
        archive_changed = (
            (outer_archive_identity is None and reference is not None and (reference / OUTDATED).exists())
            or (
                outer_archive_identity is not None
                and (
                    not _same_identity(outer_archive, outer_archive_identity)
                    or _tree_digest(outer_archive) != outer_archive_digest
                )
            )
        )
        if (
            not _same_identity(root, root_identity)
            or reference_changed
            or archive_changed
            or _reserved_adoption_issues(root)
        ):
            raise WorkspaceError("validation_error", "workspace_changed_during_prepare")
        for name in OUTER_GUIDES:
            target = root / name
            installed.append(_copy_file(stage.candidate / name, target))
        if reference is None:
            staged_ref = stage.candidate / "ref"
            target_ref = root / "ref"
            staged_ref_install = _installed_tree(staged_ref)
            os.rename(staged_ref, target_ref)
            installed.append(Installed(
                target_ref,
                staged_ref_install.identity,
                staged_ref_install.kind,
                staged_ref_install.digest,
            ))
        elif outer_archive is None:
            target_archive = reference / OUTDATED
            target_archive.mkdir()
            archive_identity = _identity(target_archive, directory=True)
            assert archive_identity is not None
            installed.append(Installed(target_archive, archive_identity, "empty"))
        staged_workbench = _installed_tree(stage.candidate / "agent-workbench")
        target_workbench = root / "agent-workbench"
        os.rename(stage.candidate / "agent-workbench", target_workbench)
        installed.append(Installed(
            target_workbench, staged_workbench.identity, staged_workbench.kind, staged_workbench.digest,
        ))
        _copy_file(stage.candidate / OUTER_MANIFEST, root / OUTER_MANIFEST)
        recognized = True
        _validate_recognized(root, core)
        response = result("collaborative_workspace.prepare", {
            "action": "adopted", "state": manifest["quality"], "workspace_id": workspace_id,
            "generation": 1, "source_items": len(items), "warnings": manifest["warnings"],
        })
        cleanup_attempted = True
        residue = _cleanup_stage(stage)
        if residue:
            raise WorkspaceError("io_error", "stage_cleanup_failed", data={"published": True, "residue": residue})
        return response
    except WorkspaceError as exc:
        if recognized:
            raise
        residues = _cleanup_installed(installed)
        if residues:
            exc.data = {**exc.data, "residue": residues}
        raise
    except OSError as exc:
        residues = [] if recognized else _cleanup_installed(installed)
        raise WorkspaceError("io_error", "adoption_publish_failed", data={"residue": residues}) from exc
    finally:
        if not cleanup_attempted:
            _cleanup_stage(stage)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _archive_batch_name(generation: int) -> str:
    observed = _utc_now().astimezone(timezone.utc)
    return f"generation-{generation}-{observed.strftime('%Y%m%dT%H%MZ')}"


def _restore_outer_moves(
    moved: list[tuple[Path, Path, Identity]],
    batch: Path | None,
    batch_identity: Identity | None,
) -> list[str]:
    residues: list[str] = []
    for source, destination, expected in reversed(moved):
        if source.exists() or not _same_identity(destination, expected):
            residues.append(str(destination))
            continue
        try:
            os.rename(destination, source)
        except OSError:
            residues.append(str(destination))
    if batch is not None and batch_identity is not None:
        if not _same_identity(batch, batch_identity):
            residues.append(str(batch))
        else:
            try:
                if any(item["kind"] != "directory" for item in _tree_records_once(batch)):
                    residues.append(str(batch))
                else:
                    _delete_no_follow(batch)
            except (OSError, WorkspaceError):
                residues.append(str(batch))
    return sorted(set(residues))


def _refresh_or_noop(
    root: Path,
    core: CoreRunner,
    outdate_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        outer, inner, items = _validate_recognized(root, core)
    except WorkspaceError as exc:
        source_items, source_issues = _scan_source(root / "ref", core)
        unsupported_issues = [
            _issue("unsupported_source_type", item.relative) for item in source_items if _route(item) is None
        ]
        combined = _merge_issues(exc.issues, source_issues, unsupported_issues)
        raise WorkspaceError(exc.status, exc.code, data=exc.data, issues=combined) from exc
    root_identity = _identity(root, directory=True)
    outer_ref = root / "ref"
    outer_ref_identity = _identity(outer_ref, directory=True)
    outer_archive = outer_ref / OUTDATED
    outer_archive_identity = _identity(outer_archive, directory=True)
    outer_archive_digest = _tree_digest(outer_archive)
    prepared_ref = root / "agent-workbench" / "ref"
    prepared_ref_identity = _identity(prepared_ref, directory=True)
    prepared_ref_digest = _tree_digest(prepared_ref)
    temp = root / "agent-workbench" / "temp"
    old_records = {
        record.get("path"): record
        for record in inner.get("source_records", [])
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    current_by_path = {item.relative: item for item in items}
    explicit_issues: list[dict[str, Any]] = []
    explicit_identities: dict[str, Identity] = {}
    for relative in outdate_paths:
        old_record = old_records.get(relative)
        current = current_by_path.get(relative)
        if old_record is None:
            explicit_issues.append(_issue("outdate_source_not_active", relative))
        elif current is None:
            explicit_issues.append(_issue("outdate_source_missing", relative))
        elif current.record() != old_record:
            explicit_issues.append(_issue("outdate_source_changed", relative))
        else:
            identity = _identity(current.source)
            assert identity is not None
            explicit_identities[relative] = identity
    if explicit_issues:
        raise WorkspaceError(
            "validation_error",
            "outdate_blocked",
            issues=explicit_issues,
            data={"state": "invalid"},
        )
    explicit = set(outdate_paths)
    active_items = [item for item in items if item.relative not in explicit]
    active_records = {item.relative: item.record() for item in active_items}
    retired_paths = tuple(sorted(
        (
            relative
            for relative, old_record in old_records.items()
            if active_records.get(relative) != old_record
        ),
        key=lambda value: value.encode("utf-8"),
    ))
    if inner["source_records"] == _records(active_items):
        second, issues = _scan_source(outer_ref, core)
        if issues or _records(second) != _records(items):
            raise WorkspaceError("validation_error", "source_changed_during_prepare", issues=issues or None)
        return result("collaborative_workspace.prepare", {
            "action": "no_op", "state": inner["quality"], "workspace_id": outer["workspace_id"],
            "generation": inner["generation"], "source_items": len(items), "warnings": inner["warnings"],
        })
    temp_identity = _literal_empty(temp)
    provider_bindings = _provider_bindings(active_items)
    _preflight_provider_bindings(provider_bindings)
    old_generation = int(inner["generation"])
    batch_name = _archive_batch_name(old_generation) if retired_paths else None
    stage, candidate_manifest = _build_candidate(
        root,
        str(outer["workspace_id"]),
        old_generation + 1,
        active_items,
        core,
        previous_ref=prepared_ref,
        retired_paths=retired_paths,
        batch_name=batch_name,
        provider_bindings=provider_bindings,
    )
    candidate_ref = stage.candidate / "agent-workbench" / "ref"
    backup = prepared_ref.with_name(".ref-old-" + uuid.uuid4().hex)
    published = False
    cleanup_attempted = False
    outer_batch: Path | None = None
    outer_batch_identity: Identity | None = None
    moved: list[tuple[Path, Path, Identity]] = []
    try:
        second, issues = _scan_source(outer_ref, core)
        if issues or _records(second) != _records(items):
            raise WorkspaceError("validation_error", "source_changed_during_prepare", issues=issues or None)
        if (
            not _same_identity(root, root_identity) or not _same_identity(outer_ref, outer_ref_identity)
            or not _same_identity(outer_archive, outer_archive_identity)
            or _tree_digest(outer_archive) != outer_archive_digest
            or not _same_identity(prepared_ref, prepared_ref_identity)
            or _tree_digest(prepared_ref) != prepared_ref_digest
            or not _same_identity(temp, temp_identity)
        ):
            raise WorkspaceError("validation_error", "workspace_changed_during_prepare")
        _literal_empty(temp)
        for relative, expected in explicit_identities.items():
            source = outer_ref.joinpath(*relative.split("/"))
            if not _same_identity(source, expected):
                raise WorkspaceError("validation_error", "outdate_source_changed", data={"path": relative})
        if outdate_paths:
            assert batch_name is not None
            outer_batch = outer_archive / batch_name
            if outer_batch.exists():
                raise WorkspaceError("validation_error", "archive_batch_collision")
            outer_batch.mkdir()
            outer_batch_identity = _identity(outer_batch, directory=True)
            assert outer_batch_identity is not None
            for relative in outdate_paths:
                source = outer_ref.joinpath(*relative.split("/"))
                destination = outer_batch.joinpath(*relative.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                expected = explicit_identities[relative]
                os.rename(source, destination)
                moved.append((source, destination, expected))
                if not _same_identity(destination, expected):
                    raise WorkspaceError("io_error", "outdate_move_identity_changed", data={"path": relative})
        if backup.exists():
            raise WorkspaceError("validation_error", "refresh_backup_collision")
        os.rename(prepared_ref, backup)
        try:
            os.rename(candidate_ref, prepared_ref)
            published = True
        except OSError as publish_error:
            try:
                os.rename(backup, prepared_ref)
            except OSError as restore_error:
                raise WorkspaceError(
                    "io_error",
                    "refresh_restore_failed",
                    data={"published": False, "residue": [str(backup)]},
                ) from restore_error
            raise publish_error
        if not _same_identity(backup, prepared_ref_identity):
            raise WorkspaceError(
                "io_error", "refresh_cleanup_identity_changed",
                data={"published": True, "residue": [str(backup)]},
            )
        try:
            _delete_no_follow(backup)
        except OSError as exc:
            raise WorkspaceError(
                "io_error", "refresh_cleanup_failed",
                data={"published": True, "residue": [str(backup)]},
            ) from exc
        _validate_recognized(root, core)
        response_data: dict[str, Any] = {
            "action": "refreshed", "state": candidate_manifest["quality"],
            "workspace_id": outer["workspace_id"], "generation": candidate_manifest["generation"],
            "source_items": len(active_items), "warnings": candidate_manifest["warnings"],
        }
        if retired_paths:
            assert batch_name is not None
            response_data["archive_batch"] = f"{OUTDATED}/{batch_name}"
            response_data["archived_sources"] = list(retired_paths)
        response = result("collaborative_workspace.prepare", response_data)
        cleanup_attempted = True
        residue = _cleanup_stage(stage)
        if residue:
            raise WorkspaceError("io_error", "stage_cleanup_failed", data={"published": True, "residue": residue})
        return response
    except WorkspaceError as exc:
        if not published:
            residues = _restore_outer_moves(moved, outer_batch, outer_batch_identity)
            if residues:
                exc.data = {**exc.data, "residue": residues}
        raise
    except OSError as exc:
        residues = [] if published else _restore_outer_moves(moved, outer_batch, outer_batch_identity)
        data: dict[str, Any] = {"published": published}
        if residues:
            data["residue"] = residues
        raise WorkspaceError("io_error", "refresh_publish_failed", data=data) from exc
    finally:
        if not cleanup_attempted:
            _cleanup_stage(stage)


def prepare(root: Path, outdate: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    root = _absolute_root(root, missing=True)
    outdate_paths = _canonical_outdate_paths(outdate)
    try:
        core = CoreRunner()
        with _writer_lock(root):
            if not root.exists():
                if outdate_paths:
                    raise WorkspaceError("validation_error", "outdate_requires_recognized_workspace")
                return _create_missing(root, core)
            _identity(root, directory=True)
            if not (root / OUTER_MANIFEST).exists():
                if outdate_paths:
                    raise WorkspaceError("validation_error", "outdate_requires_recognized_workspace")
                return _adopt(root, core)
            return _refresh_or_noop(root, core, outdate_paths)
    except CoreFailure as exc:
        raise WorkspaceError(exc.status, exc.code, data=exc.data) from exc


def status(root: Path) -> dict[str, Any]:
    root = _absolute_root(root, missing=True)
    try:
        core = CoreRunner()
        with _read_lock(root):
            _name, data = _state(root, core)
            return result("collaborative_workspace.status", data)
    except CoreFailure as exc:
        raise WorkspaceError(exc.status, exc.code, data=exc.data) from exc


def validate(root: Path) -> dict[str, Any]:
    root = _absolute_root(root, missing=True)
    try:
        core = CoreRunner()
        with _read_lock(root):
            state_name, data = _state(root, core)
            if state_name in {"ready", "ready_with_warnings"}:
                return result("collaborative_workspace.validate", {**data, "valid": True})
            code = {
                "uninitialized": "workspace_uninitialized", "stale": "workspace_stale",
                "invalid": "workspace_invalid", "busy": "workbench_temp_not_empty",
            }[state_name]
            error_status = "busy" if state_name == "busy" else "validation_error"
            raise WorkspaceError(error_status, code, data={**data, "valid": False},
                                 issues=data.get("blockers") or None)
    except CoreFailure as exc:
        raise WorkspaceError(exc.status, exc.code, data=exc.data) from exc


__all__ = [
    "INNER_CONTRACT", "OUTER_CONTRACT", "VERSION", "RuntimeFallback", "WorkspaceError", "failure", "prepare", "result",
    "set_default_provider_runners", "status", "validate",
]
