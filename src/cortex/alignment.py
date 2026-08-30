"""Minimal read-only plan and fixture repair application."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .core_runner import CoreRunner
from .errors import validation_error
from .jsonio import read_json_file
from .native import checked_scandir
from .validation import _validate_workspace_product


def bundle_fingerprint(workspace: Path) -> str:
    digest = hashlib.sha256(b"CORTEX_ALIGN_INPUT_V1\0")
    paths = sorted(workspace.rglob("*"), key=lambda path: path.relative_to(workspace).as_posix().encode("utf-8"))
    for path in paths:
        relative = path.relative_to(workspace).as_posix()
        raw = relative.encode("utf-8", errors="strict")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        if path.is_dir():
            digest.update(b"D")
        elif path.is_file():
            digest.update(b"F")
            digest.update(path.read_bytes())
        else:
            raise validation_error("Alignment input contains an unsupported entry", "align_input_entry", path=relative)
    return digest.hexdigest()


def _require_product_valid(workspace: Path) -> None:
    report = _validate_workspace_product(workspace)
    if report.issues:
        first = report.issues[0]
        raise validation_error(first["message"], first["code"], path=first.get("path"), issues=report.issues)


def _record_paths(workspace: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for partition in checked_scandir(workspace):
        if partition.name == "profiles":
            continue
        for record in checked_scandir(Path(partition.path)):
            result[f"{partition.name}/{record.name}"] = Path(record.path)
    return result


def plan_alignment(workspace: Path, core: CoreRunner) -> dict[str, Any]:
    root = Path(os.path.abspath(workspace))
    _require_product_valid(root)
    repairs: list[str] = []
    for relative, path in _record_paths(root).items():
        result = core.probe_validate(path, private_root_files=("record.json",))
        if result.ok:
            continue
        if result.status != "validation_error":
            core.require_success(result)
        repairs.append(relative)
    return {
        "workspace": str(root),
        "fingerprint": bundle_fingerprint(root),
        "repairs": repairs,
    }


def apply_alignment(plan_operand: str, core: CoreRunner) -> dict[str, Any]:
    if plan_operand == "-":
        raise validation_error("align apply requires an explicit plan file", "align_plan_file_required")
    plan, _payload = read_json_file(Path(os.path.abspath(plan_operand)))
    workspace_value = plan.get("workspace")
    fingerprint = plan.get("fingerprint")
    repairs = plan.get("repairs")
    if not isinstance(workspace_value, str) or not Path(workspace_value).is_absolute():
        raise validation_error("Alignment plan workspace must be absolute", "invalid_align_plan")
    if not isinstance(fingerprint, str) or not isinstance(repairs, list) or any(not isinstance(item, str) for item in repairs):
        raise validation_error("Alignment plan fields are invalid", "invalid_align_plan")
    workspace = Path(workspace_value)
    _require_product_valid(workspace)
    current = bundle_fingerprint(workspace)
    if current != fingerprint:
        raise validation_error("Alignment input no longer matches the plan", "stale_align_plan", path=str(workspace))
    available = _record_paths(workspace)
    if len(set(repairs)) != len(repairs) or any(item not in available for item in repairs):
        raise validation_error("Alignment plan names an unknown or duplicate record", "invalid_align_plan")
    for relative in repairs:
        path = available[relative]
        core.repair(path, private_root_files=("record.json",))
        core.validate(path, private_root_files=("record.json",))
    return {
        "workspace": str(workspace),
        "repaired": repairs,
        "fingerprint": bundle_fingerprint(workspace),
    }


__all__ = ["apply_alignment", "bundle_fingerprint", "plan_alignment"]
