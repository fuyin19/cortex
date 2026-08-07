"""Cross-platform path guards for portable Cortex artifacts."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .errors import CortexError, Status
from .native import canonical_handle_path, exists, is_dir, is_reparse


_WINDOWS_DEVICES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _path_error(message: str, code: str, path: str) -> CortexError:
    return CortexError(message, status=Status.POLICY_BLOCKED, code=code, details={"path": path})


def normalize_relative_path(value: str | os.PathLike[str]) -> str:
    """Normalize a portable POSIX relative path or reject it fail-closed."""

    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw:
        raise _path_error("Path must be a non-empty string", "invalid_path", str(raw))
    if "\\" in raw:
        raise _path_error("Backslash separators are forbidden", "windows_separator", raw)
    if raw.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise _path_error("Absolute, UNC, and drive paths are forbidden", "absolute_path", raw)

    decoded = unquote(raw)
    if unquote(decoded) != decoded:
        raise _path_error("Double percent decoding is forbidden", "double_percent_decode", raw)
    if "\\" in decoded or decoded.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", decoded):
        raise _path_error("Encoded absolute paths or separators are forbidden", "encoded_separator", raw)

    normalized_parts: list[str] = []
    for part in decoded.split("/"):
        if part in ("", ".", ".."):
            code = "path_escape" if part == ".." else "invalid_path"
            raise _path_error("Empty, dot, and parent path segments are forbidden", code, raw)
        part = unicodedata.normalize("NFC", part)
        if _CONTROL.search(part) or ":" in part:
            raise _path_error("Control characters and colons are forbidden", "invalid_path", raw)
        if part.endswith((".", " ")):
            raise _path_error("Windows trailing dots and spaces are forbidden", "windows_path", raw)
        stem = part.split(".", 1)[0].rstrip(" .").upper()
        if stem in _WINDOWS_DEVICES:
            raise _path_error("Windows device names are forbidden", "windows_device", raw)
        normalized_parts.append(part)
    return "/".join(normalized_parts)


def validate_concept_id(value: str) -> str:
    """Validate an OKF Concept ID (bundle-relative path without ``.md``)."""

    normalized = normalize_relative_path(value)
    if normalized.endswith(".md"):
        raise _path_error("Concept IDs must omit the .md suffix", "invalid_concept_id", value)
    if PurePosixPath(normalized).name.casefold() in {"index", "log"}:
        raise _path_error("Reserved documents are not concepts", "reserved_concept_id", value)
    return normalized


def safe_join(root: str | os.PathLike[str], relative: str | os.PathLike[str]) -> Path:
    """Resolve a portable relative path under *root*, rejecting symlink hops."""

    normalized = normalize_relative_path(relative)
    base = Path(root)
    if not exists(base) or not is_dir(base) or is_reparse(base):
        raise _path_error("Authorized root must be an existing real directory", "invalid_root", str(base))
    base_key = canonical_handle_path(base)
    current = base
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if exists(current) and is_reparse(current):
            raise _path_error("Symbolic link traversal is forbidden", "symlink_escape", normalized)
    current_key = canonical_handle_path(current)
    separator = "\\" if os.name == "nt" else "/"
    if current_key != base_key and not current_key.startswith(base_key.rstrip("\\/") + separator):
        raise _path_error("Path escapes the authorized root", "path_escape", normalized)
    return current


def collision_key(value: str | os.PathLike[str]) -> str:
    """Return the case-insensitive portable collision key for a safe path."""

    return normalize_relative_path(value).casefold()
