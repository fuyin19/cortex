"""Deterministic JSON, artifact, and filesystem tree identities.

The encoder implements the RFC 8785/JCS rules used by Cortex.  It accepts
I-JSON values only: strings are normalized to NFC, object keys are unique
after normalization, integers fit exactly in an IEEE-754 double, and
non-finite numbers are rejected.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import unicodedata
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import CortexError, Status
from .native import is_reparse, native_path


_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _native_path(path: str | os.PathLike[str]) -> str:
    """Return an absolute path usable beyond ``MAX_PATH`` on Windows."""
    return native_path(path)


def _invalid(message: str, *, code: str = "invalid_canonical_value", **details: object) -> CortexError:
    return CortexError(message, status=Status.VALIDATION_BLOCKED, code=code, details=dict(details))


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _number(value: int | float) -> str:
    if isinstance(value, bool):  # bool is an int subclass
        raise TypeError
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise _invalid("Integer is outside the I-JSON safe range", value=value)
        return str(value)
    if not math.isfinite(value):
        raise _invalid("Non-finite JSON numbers are forbidden", value=repr(value))
    if value == 0:
        return "0"

    negative = value < 0
    magnitude = -value if negative else value
    decimal = Decimal(repr(magnitude))
    sign = "-" if negative else ""

    # ECMAScript JSON.stringify uses plain notation for [1e-6, 1e21).
    if Decimal("1e-6") <= decimal < Decimal("1e21"):
        rendered = format(decimal, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return sign + rendered

    normalized = decimal.normalize()
    digits = "".join(str(digit) for digit in normalized.as_tuple().digits)
    exponent = normalized.adjusted()
    fraction = digits[1:].rstrip("0")
    mantissa = digits[0] + ("." + fraction if fraction else "")
    exponent_text = f"+{exponent}" if exponent >= 0 else str(exponent)
    return f"{sign}{mantissa}e{exponent_text}"


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _quoted(unicodedata.normalize("NFC", value))
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _invalid("JSON object keys must be strings", key_type=type(key).__name__)
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise _invalid(
                    "JSON object keys collide after NFC normalization",
                    code="canonical_key_collision",
                    key=normalized_key,
                )
            normalized[normalized_key] = item
        keys = sorted(normalized, key=lambda item: item.encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(f"{_quoted(key)}:{_encode(normalized[key])}" for key in keys) + "}"
    raise _invalid("Value is not representable as canonical JSON", value_type=type(value).__name__)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize *value* as NFC-normalized RFC 8785/JCS UTF-8 bytes."""

    return _encode(value).encode("utf-8")


def sha256_digest(data: bytes | bytearray | memoryview | str) -> str:
    """Return a lower-case SHA-256 hex digest.

    Strings are encoded as their exact UTF-8 representation.  Callers that
    need canonical JSON identity must pass :func:`canonical_json_bytes`.
    """

    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return hashlib.sha256(payload).hexdigest()


def artifact_digest(artifact: Mapping[str, Any]) -> str:
    """Hash a public artifact after removing its self-identifying fields."""

    projected = dict(artifact)
    projected.pop("artifact_id", None)
    projected.pop("digest", None)
    return sha256_digest(canonical_json_bytes(projected))


def _normalized_manifest_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return _normalized_manifest_relative(relative)


def _normalized_manifest_relative(relative: str) -> str:
    normalized = unicodedata.normalize("NFC", relative)
    if not normalized or normalized.startswith("/") or any(part in ("", ".", "..") for part in normalized.split("/")):
        raise _invalid("Filesystem path cannot be represented safely", code="invalid_tree_path", path=relative)
    return normalized


def tree_manifest(root: str | os.PathLike[str], *, exclude: Iterable[str] = ()) -> dict[str, Any]:
    """Return the content identity of a symlink-free regular-file tree.

    ``tree_digest`` covers only the ordered ``entries`` projection.  Directory
    mtimes, permissions, and host-specific separators never affect identity.
    """

    base = Path(os.path.abspath(os.fspath(root)))
    native_base = _native_path(base)
    try:
        root_metadata = os.lstat(native_base)
    except OSError as exc:
        raise _invalid("Tree root must be an existing real directory", code="invalid_tree_root", root=str(base)) from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode) or (
        getattr(root_metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise _invalid("Tree root must be an existing real directory", code="invalid_tree_root", root=str(base))
    excluded = {
        unicodedata.normalize("NFC", str(item).replace("\\", "/")).strip("/")
        for item in exclude
    }

    entries: list[dict[str, Any]] = []
    collision_keys: dict[str, str] = {}

    def visit(directory: str, prefix: str = "") -> None:
        try:
            children = sorted(
                os.scandir(directory),
                key=lambda item: unicodedata.normalize("NFC", item.name).encode("utf-8"),
            )
        except OSError as exc:
            raise _invalid(
                "Content tree directory cannot be inspected",
                code="tree_entry_unreadable",
                path=prefix or ".",
            ) from exc
        for child in children:
            raw_relative = f"{prefix}/{child.name}" if prefix else child.name
            relative = _normalized_manifest_relative(raw_relative.replace("\\", "/"))
            if any(relative == item or relative.startswith(item + "/") for item in excluded if item):
                continue
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise _invalid(
                    "Content tree entry cannot be inspected",
                    code="tree_entry_unreadable",
                    path=relative,
                ) from exc
            lexical_candidate = base.joinpath(*relative.split("/"))
            if is_reparse(lexical_candidate) or stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise _invalid("Symbolic links are forbidden in content trees", code="symlink_escape", path=relative)
            if stat.S_ISDIR(metadata.st_mode):
                visit(child.path, relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _invalid("Only regular files are allowed in content trees", code="special_file", path=relative)
            collision_key = relative.casefold()
            previous = collision_keys.get(collision_key)
            if previous is not None and previous != relative:
                raise _invalid(
                    "Paths collide under cross-platform case folding",
                    code="tree_path_collision",
                    paths=[previous, relative],
                )
            collision_keys[collision_key] = relative
            with open(child.path, "rb") as stream:
                payload = stream.read()
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": len(payload),
                    "digest": sha256_digest(payload),
                }
            )

    visit(native_base)
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    return {
        "entries": entries,
        "tree_digest": sha256_digest(canonical_json_bytes(entries)),
        "total_entries": len(entries),
        "total_bytes": sum(item["size_bytes"] for item in entries),
    }
