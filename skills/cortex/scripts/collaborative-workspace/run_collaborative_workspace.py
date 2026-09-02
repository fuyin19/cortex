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

VERSION = "1.1.0"
WHEEL = "cortex_collaborative_workspace-1.1.0-py3-none-any.whl"
DIGEST = "675116b76b7f549fd53e6a90ef0f46f300c446b8c61b7b8b49cab39bb557d304"
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
            metadata_name = "cortex_collaborative_workspace-1.1.0.dist-info/METADATA"
            if len(names) != len(set(names)) or metadata_name not in names: raise BootstrapError("wheel_metadata_invalid")
            metadata = archive.read(metadata_name).decode("utf-8")
            if "Name: cortex-collaborative-workspace\n" not in metadata or "Version: 1.1.0\n" not in metadata or "Requires-Dist:" in metadata: raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names): raise BootstrapError("wheel_entry_point_forbidden")
    except BootstrapError: raise
    except Exception as exc: raise BootstrapError("wheel_invalid") from exc
    if "cortex_collaborative_workspace" in sys.modules: raise BootstrapError("ambient_workspace_runtime_loaded")
    sys.path.insert(0, str(wheel)); package = importlib.import_module("cortex_collaborative_workspace")
    origin = os.path.normcase(os.path.abspath(package.__file__ or "")).replace("\\", "/")
    prefix = os.path.normcase(str(wheel)).replace("\\", "/") + "/cortex_collaborative_workspace/"
    if package.__version__ != VERSION or not origin.startswith(prefix): raise BootstrapError("import_origin_mismatch")
    cli = importlib.import_module("cortex_collaborative_workspace.cli")
    return int(cli.main(sys.argv[1:]))

def main() -> int:
    try: return _run()
    except BootstrapError as exc:
        sys.stderr.write(f"cortex collaborative workspace runtime error: {exc}\n"); return BOOTSTRAP_EXIT
    except Exception:
        sys.stderr.write("cortex collaborative workspace runtime error: bootstrap_failed\n"); return BOOTSTRAP_EXIT

if __name__ == "__main__": raise SystemExit(main())
