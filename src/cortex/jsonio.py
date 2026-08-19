"""Strict UTF-8 JSON input and deterministic owned JSON output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .errors import CortexError, io_error, validation_error


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise validation_error("JSON object contains a duplicate key", "duplicate_json_key", key=key)
        value[key] = item
    return value


def loads_object(payload: bytes, *, label: str) -> dict[str, Any]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise validation_error("JSON must not contain a UTF-8 BOM", "json_bom", path=label)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise validation_error("JSON is not strict UTF-8", "invalid_utf8", path=label) from exc
    if not text.strip():
        raise validation_error("JSON input is empty", "empty_json", path=label)
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except CortexError:
        raise
    except json.JSONDecodeError as exc:
        raise validation_error(
            "JSON syntax or trailing data is invalid",
            "invalid_json",
            path=label,
            line=exc.lineno,
            column=exc.colno,
        ) from exc
    if not isinstance(value, dict):
        raise validation_error("JSON top level must be an object", "invalid_json_top_level", path=label)
    return value


def read_json_file(path: Path, *, label: str | None = None) -> tuple[dict[str, Any], bytes]:
    from .native import require_regular_file

    require_regular_file(path, code="json_input_not_ordinary")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise io_error("JSON input could not be read", "input_unreadable", path=str(path), os_error=str(exc)) from exc
    return loads_object(payload, label=label or str(path)), payload


def _stdin_bytes(stream: TextIO | BinaryIO | None = None) -> bytes:
    selected = sys.stdin if stream is None else stream
    binary = getattr(selected, "buffer", None)
    try:
        value = binary.read() if binary is not None else selected.read()
    except (OSError, UnicodeError) as exc:
        raise io_error("Standard input could not be read", "stdin_unreadable", os_error=str(exc)) from exc
    if isinstance(value, bytes):
        return value
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise validation_error("Standard input is not strict UTF-8", "invalid_utf8", path="-") from exc


def read_json_operand(operand: str, *, stdin: TextIO | BinaryIO | None = None) -> tuple[dict[str, Any], bytes]:
    if operand == "-":
        payload = _stdin_bytes(stdin)
        return loads_object(payload, label="-"), payload
    return read_json_file(Path(operand))
