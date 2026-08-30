"""Compatibility entry points delegated to the explicit Core runner."""

from __future__ import annotations

import os
from pathlib import Path

from .core_runner import CoreRunner, require_core
from .errors import usage_error, validation_error
from .native import inspect_conversion


def inspect_input(
    path: Path,
    *,
    core: CoreRunner | None = None,
) -> tuple[list[tuple[str, Path, bool]], str, Path | None]:
    data = require_core(core).inspect(path)
    kind, entries = inspect_conversion(path)
    if kind != "directory":
        raise validation_error("Conversion must be a real directory", "conversion_directory_required", path=str(path))
    stem = data.get("stem")
    if not isinstance(stem, str):
        stem = ""
    retained = [
        source
        for relative, source, is_directory in entries
        if not is_directory
        and relative.startswith("src/")
        and relative.count("/") == 1
        and Path(relative).name != ".keep"
    ]
    return entries, stem, retained[0] if len(retained) == 1 else None


def validate_complete_directory(
    path: Path,
    *,
    cortex_record: bool = True,
    core: CoreRunner | None = None,
) -> str:
    data = require_core(core).validate(
        Path(os.path.abspath(path)),
        private_root_files=("record.json",) if cortex_record else (),
    )
    stem = data.get("stem")
    return stem if isinstance(stem, str) else ""


def finalize_staged(
    path: Path,
    *,
    source: Path | None,
    core: CoreRunner | None = None,
) -> str:
    if source is not None:
        raise usage_error(
            "A retained source must be staged before Core stage completion",
            "source_must_be_prestaged",
        )
    data = require_core(core).stage_complete(
        Path(os.path.abspath(path)),
        private_root_files=("record.json",),
    )
    stem = data.get("stem")
    return stem if isinstance(stem, str) else ""


__all__ = ["finalize_staged", "inspect_input", "validate_complete_directory"]
