#!/usr/bin/env python3
"""Offline Collaborative Workspace launcher. Generated; do not edit."""
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

VERSION = "1.1.3"
WHEEL = "cortex_collaborative_workspace-1.1.3-py3-none-any.whl"
DIGEST = "51139e06e83d61c5372567289f8ec1237b3bbc682d0f2d6787b546260e83180c"
BOOTSTRAP_EXIT = 70

class BootstrapError(Exception): pass

def _reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

def _ordinary(path: Path, directory: bool) -> None:
    if not path.is_absolute(): raise BootstrapError("non_absolute_path")
    for node in reversed((path,) + tuple(path.parents)):
        try: info = node.lstat()
        except OSError as exc: raise BootstrapError("runtime_path_unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or _reparse(info): raise BootstrapError("runtime_path_reparse")
        final = node == path
        if final and not directory and not stat.S_ISREG(info.st_mode): raise BootstrapError("runtime_file_not_regular")
        if (not final or directory) and not stat.S_ISDIR(info.st_mode): raise BootstrapError("runtime_parent_not_directory")

def _run() -> int:
    configured = os.environ.get("CORTEX_PYTHON")
    if not configured: raise BootstrapError("cortex_python_required")
    python = Path(configured); _ordinary(python, False)
    try: same = os.path.samefile(python, sys.executable)
    except OSError as exc: raise BootstrapError("cortex_python_unreadable") from exc
    if not same: raise BootstrapError("cortex_python_mismatch")
    if sys.version_info[:2] != (3, 11): raise BootstrapError("python_3_11_required")
    if unicodedata.unidata_version != "14.0.0": raise BootstrapError("unicode_14_required")
    if not sys.flags.isolated: raise BootstrapError("isolated_mode_required")
    runner = Path(os.path.abspath(__file__)); _ordinary(runner, False)
    scripts = runner.parent
    manifest = scripts / "runtime-manifest.json"; wheel = scripts / "vendor" / WHEEL
    _ordinary(manifest, False); _ordinary(wheel, False)
    expected = {"schema_version":1,"distribution":"cortex-collaborative-workspace","import":"cortex_collaborative_workspace","version":VERSION,"wheel":WHEEL,"wheel_sha256":DIGEST,"python":"3.11","isolation":"-I"}
    try: actual = json.loads(manifest.read_bytes().decode("utf-8"))
    except Exception as exc: raise BootstrapError("manifest_invalid") from exc
    if actual != expected or set(actual) != set(expected): raise BootstrapError("manifest_mismatch")
    try:
        raw = wheel.read_bytes()
        if hashlib.sha256(raw).hexdigest() != DIGEST: raise BootstrapError("wheel_digest_mismatch")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata_name = "cortex_collaborative_workspace-1.1.3.dist-info/METADATA"
            if len(names) != len(set(names)) or metadata_name not in names: raise BootstrapError("wheel_metadata_invalid")
            metadata = archive.read(metadata_name).decode("utf-8")
            if "Name: cortex-collaborative-workspace\n" not in metadata or "Version: 1.1.3\n" not in metadata or "Requires-Dist:" in metadata: raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names): raise BootstrapError("wheel_entry_point_forbidden")
    except BootstrapError: raise
    except Exception as exc: raise BootstrapError("wheel_invalid") from exc
    if "cortex_collaborative_workspace" in sys.modules: raise BootstrapError("ambient_workspace_runtime_loaded")
    sys.path.insert(0, str(wheel)); package = importlib.import_module("cortex_collaborative_workspace")
    origin = os.path.normcase(os.path.abspath(package.__file__ or "")).replace("\\", "/")
    prefix = os.path.normcase(str(wheel)).replace("\\", "/") + "/cortex_collaborative_workspace/"
    if package.__version__ != VERSION or not origin.startswith(prefix): raise BootstrapError("import_origin_mismatch")
    core_client = importlib.import_module("cortex_collaborative_workspace.core_runner")
    for boundary in runner.parents:
        if boundary.name == "cortex":
            marker = boundary / "SKILL.md"
            try:
                marker_info = marker.lstat()
            except OSError:
                break
            if stat.S_ISREG(marker_info.st_mode) and not stat.S_ISLNK(marker_info.st_mode) and not bool(getattr(marker_info, "st_file_attributes", 0) & 0x400):
                core_skill = boundary.parent / "anti-entropy-core"
                core_client.set_default_runner(core_skill / "scripts" / "knowledge_unit_runner.py", core_skill / "SKILL.md")
            break
    cli = importlib.import_module("cortex_collaborative_workspace.cli")
    return int(cli.main(sys.argv[1:]))

def main() -> int:
    try: return _run()
    except BootstrapError as exc:
        sys.stderr.write(f"cortex collaborative workspace runtime error: {exc}\n"); return BOOTSTRAP_EXIT
    except Exception:
        sys.stderr.write("cortex collaborative workspace runtime error: bootstrap_failed\n"); return BOOTSTRAP_EXIT

if __name__ == "__main__": raise SystemExit(main())
