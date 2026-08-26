#!/usr/bin/env python3
"""Offline, skill-local Cortex launcher. Generated; do not edit."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import unicodedata
import zipfile


EXPECTED_VERSION = "8.0.0"
EXPECTED_DISTRIBUTION = "cortex-record-kb"
EXPECTED_WHEEL_FILENAME = "cortex_record_kb-8.0.0-py3-none-any.whl"
EXPECTED_WHEEL_SHA256 = "b9022bf869a3ddc646a9f24853fc141b8749d3454c357f8a3465f23feb063ab5"
EXPECTED_MANIFEST_KEYS = {
    "schema_version", "distribution", "import", "version", "wheel",
    "wheel_sha256", "python", "isolation",
}
BOOTSTRAP_EXIT = 70


class BootstrapError(Exception):
    pass


def _fail(code: str) -> int:
    sys.stderr.write(f"cortex skill runtime error: {code}\n")
    return BOOTSTRAP_EXIT


def _is_reparse(result: os.stat_result) -> bool:
    attributes = getattr(result, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _check_chain(path: Path, *, final_file: bool) -> Path:
    if not path.is_absolute():
        raise BootstrapError("non_absolute_path")
    nodes = list(reversed((path,) + tuple(path.parents)))
    for index, node in enumerate(nodes):
        try:
            result = node.lstat()
        except OSError as exc:
            raise BootstrapError("runtime_path_unreadable") from exc
        if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
            raise BootstrapError("runtime_path_reparse")
        is_final = index == len(nodes) - 1
        if is_final and final_file:
            if not stat.S_ISREG(result.st_mode):
                raise BootstrapError("runtime_file_not_regular")
        elif not stat.S_ISDIR(result.st_mode):
            raise BootstrapError("runtime_parent_not_directory")
    return path


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("manifest_invalid") from exc
    expected = {
        "schema_version": 1,
        "distribution": EXPECTED_DISTRIBUTION,
        "import": "cortex",
        "version": EXPECTED_VERSION,
        "wheel": EXPECTED_WHEEL_FILENAME,
        "wheel_sha256": EXPECTED_WHEEL_SHA256,
        "python": "3.11",
        "isolation": "-I",
    }
    if not isinstance(value, dict) or set(value) != EXPECTED_MANIFEST_KEYS or value != expected:
        raise BootstrapError("manifest_mismatch")
    return value


def _verify_archive(path: Path) -> None:
    metadata_name = "cortex_record_kb-8.0.0.dist-info/METADATA"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or metadata_name not in names:
                raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names):
                raise BootstrapError("wheel_entry_point_forbidden")
            metadata = archive.read(metadata_name).decode("utf-8")
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as exc:
        raise BootstrapError("wheel_invalid") from exc
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        if not line:
            break
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    if fields.get("Name") != EXPECTED_DISTRIBUTION or fields.get("Version") != EXPECTED_VERSION:
        raise BootstrapError("wheel_version_mismatch")
    if any(line.startswith("Requires-Dist:") for line in metadata.splitlines()):
        raise BootstrapError("wheel_dependency_forbidden")


def _module_is_from_wheel(module: object, wheel: Path) -> bool:
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return False
    normalized_origin = os.path.normcase(os.path.abspath(origin)).replace("\\", "/")
    normalized_wheel = os.path.normcase(str(wheel)).replace("\\", "/")
    return normalized_origin.startswith(normalized_wheel + "/cortex/")


def _run() -> int:
    configured_raw = os.environ.get("CORTEX_PYTHON")
    if configured_raw is None or configured_raw == "":
        raise BootstrapError("cortex_python_required")
    configured = _check_chain(Path(configured_raw), final_file=True)
    try:
        same_interpreter = os.path.samefile(configured, sys.executable)
    except OSError as exc:
        raise BootstrapError("cortex_python_unreadable") from exc
    if not same_interpreter:
        raise BootstrapError("cortex_python_mismatch")
    if sys.version_info[:2] != (3, 11):
        raise BootstrapError("python_3_11_required")
    if unicodedata.unidata_version != "14.0.0":
        raise BootstrapError("unicode_14_required")
    if not sys.flags.isolated:
        raise BootstrapError("isolated_mode_required")
    runner = _check_chain(Path(os.path.abspath(__file__)), final_file=True)
    scripts = runner.parent
    skill = scripts.parent
    manifest_path = _check_chain(scripts / "runtime-manifest.json", final_file=True)
    wheel = _check_chain(scripts / "vendor" / EXPECTED_WHEEL_FILENAME, final_file=True)
    _load_manifest(manifest_path)
    try:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    except OSError as exc:
        raise BootstrapError("wheel_unreadable") from exc
    if digest != EXPECTED_WHEEL_SHA256:
        raise BootstrapError("wheel_digest_mismatch")
    _verify_archive(wheel)
    if "cortex" in sys.modules:
        raise BootstrapError("ambient_cortex_loaded")
    sys.path.insert(0, str(wheel))
    package = importlib.import_module("cortex")
    if getattr(package, "__version__", None) != EXPECTED_VERSION or not _module_is_from_wheel(package, wheel):
        raise BootstrapError("import_origin_mismatch")
    cli = importlib.import_module("cortex.cli")
    if not _module_is_from_wheel(cli, wheel):
        raise BootstrapError("import_origin_mismatch")
    return int(cli.main(sys.argv[1:]))


def main() -> int:
    try:
        return _run()
    except BootstrapError as exc:
        return _fail(str(exc))
    except Exception:
        return _fail("bootstrap_failed")


if __name__ == "__main__":
    raise SystemExit(main())
