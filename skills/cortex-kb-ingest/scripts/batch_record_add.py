#!/usr/bin/env python3
"""Build-skill-only sequential wrapper for Cortex record add."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA_VERSION = 1
RESULT_KEYS = {"status", "exit_code", "command", "data", "issues"}
STATUS_EXIT_CODES = {
    "ok": 0,
    "usage_error": 2,
    "validation_error": 3,
    "busy": 5,
    "io_error": 6,
}


class BatchUsage(Exception):
    pass


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BatchUsage("invalid_arguments")


def _parser() -> ContractParser:
    parser = ContractParser(prog="cortex-record-add-batch")
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--workspace")
    selectors.add_argument("--kb-root")
    parser.add_argument("--bundle-id")
    parser.add_argument("--job", required=True)
    return parser


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchUsage("duplicate_json_key")
        result[key] = value
    return result


def _read_job(operand: str) -> object:
    try:
        raw = sys.stdin.buffer.read() if operand == "-" else Path(operand).read_bytes()
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except BatchUsage:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatchUsage("job_invalid") from exc


def _absolute_string(value: object) -> bool:
    return isinstance(value, str) and value != "" and Path(value).is_absolute()


def _validate_job(value: object) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != {"version", "items"}:
        raise BatchUsage("job_shape_invalid")
    if type(value["version"]) is not int or value["version"] not in {1, 2}:
        raise BatchUsage("job_version_invalid")
    version = value["version"]
    items = value["items"]
    if not isinstance(items, list):
        raise BatchUsage("job_items_invalid")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise BatchUsage("job_item_invalid")
        keys = set(item)
        v1_shapes = ({"id", "source", "metadata"}, {"id", "source", "conversion", "metadata"})
        v2_shapes = (
            {"id", "source", "metadata"},
            {"id", "conversion", "metadata"},
            {"id", "source", "conversion", "metadata"},
        )
        if keys not in (v1_shapes if version == 1 else v2_shapes):
            raise BatchUsage("job_item_shape_invalid")
        item_id = item["id"]
        if not isinstance(item_id, str) or item_id == "" or item_id in ids:
            raise BatchUsage("job_item_id_invalid")
        ids.add(item_id)
        if "source" in item and not _absolute_string(item["source"]):
            raise BatchUsage("job_source_not_absolute")
        if "conversion" in item and not _absolute_string(item["conversion"]):
            raise BatchUsage("job_conversion_not_absolute")
        metadata = item["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != {"title", "timestamp", "tags"}:
            raise BatchUsage("job_metadata_shape_invalid")
        if not isinstance(metadata["title"], str) or not isinstance(metadata["timestamp"], str):
            raise BatchUsage("job_metadata_value_invalid")
        tags = metadata["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise BatchUsage("job_metadata_value_invalid")
        normalized.append(item)
    return version, normalized


def _selectors(args: argparse.Namespace) -> list[str]:
    if args.workspace is not None:
        if args.bundle_id is not None:
            raise BatchUsage("invalid_selector")
        return ["--workspace", args.workspace]
    if args.bundle_id is None:
        raise BatchUsage("invalid_selector")
    return ["--kb-root", args.kb_root, "--bundle-id", args.bundle_id]


def _invoke(command: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _result(process: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        value = json.loads(process.stdout.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchUsage("runner_non_result") from exc
    if (
        process.stderr != b""
        or not isinstance(value, dict)
        or set(value) != RESULT_KEYS
        or value.get("command") != "record.add"
        or not isinstance(value.get("data"), dict)
        or not isinstance(value.get("issues"), list)
        or any(not isinstance(issue, dict) for issue in value["issues"])
        or type(value.get("exit_code")) is not int
        or value.get("status") not in STATUS_EXIT_CODES
        or STATUS_EXIT_CODES[value["status"]] != value["exit_code"]
        or process.returncode != value["exit_code"]
    ):
        raise BatchUsage("runner_non_result")
    return value


def _write_wrapper(items: list[dict[str, Any]], total: int) -> int:
    succeeded = sum(item["result"]["status"] == "ok" for item in items)
    value = {
        "schema_version": SCHEMA_VERSION,
        "command": "record.add.batch",
        "items": items,
        "summary": {"total": total, "succeeded": succeeded, "failed": total - succeeded},
    }
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
    return 0 if succeeded == total else 1


def _run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    selectors = _selectors(args)
    _job_version, items = _validate_job(_read_job(args.job))
    runner = Path(__file__).absolute().parent / "run_cortex.py"
    base = [sys.executable, "-I", str(runner)]
    with tempfile.TemporaryDirectory(prefix="cortex-record-add-batch-") as temporary:
        normalized = Path(temporary) / "job.json"
        normalized.write_text(json.dumps({"version": _job_version, "items": items}, ensure_ascii=False,
                                         separators=(",", ":")) + "\n", encoding="utf-8")
        process = _invoke([*base, *selectors, "--_record-add-batch", str(normalized)])
        if process.returncode not in {0, 1} or process.stderr != b"":
            raise BatchUsage("runner_bootstrap_failed" if process.returncode == 70 else "runner_non_result")
        try:
            wrapper = json.loads(process.stdout.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BatchUsage("runner_non_result") from exc
        if not isinstance(wrapper, dict) or wrapper.get("command") != "record.add.batch":
            raise BatchUsage("runner_non_result")
        sys.stdout.buffer.write(process.stdout)
        return process.returncode


def main() -> int:
    try:
        return _run(sys.argv[1:])
    except KeyboardInterrupt:
        sys.stderr.write("cortex record batch error: interrupted\n")
        return 2
    except BatchUsage as exc:
        sys.stderr.write(f"cortex record batch error: {exc}\n")
        return 2
    except Exception:
        sys.stderr.write("cortex record batch error: wrapper_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
