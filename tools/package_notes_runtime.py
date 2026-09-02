#!/usr/bin/env python3
"""Build and verify the deterministic offline Cortex Notes skill runtime."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile


VERSION = "2.1.0"
DISTRIBUTION = "cortex-notes"
IMPORT_NAME = "cortex_notes"
WHEEL_NAME = "cortex_notes-2.1.0-py3-none-any.whl"
DIST_INFO = "cortex_notes-2.1.0.dist-info"
ADAPTER = Path("skills/cortex/scripts/notes")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_digest(raw: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _source(root: Path) -> list[tuple[str, bytes]]:
    package = root / "notes_runtime" / "src" / IMPORT_NAME
    result = []
    for path in sorted(package.glob("*.py"), key=lambda item: item.name.encode("utf-8")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("unsafe Notes source")
        result.append((f"{IMPORT_NAME}/{path.name}", path.read_bytes()))
    if {name.rsplit("/", 1)[-1] for name, _raw in result} != {"__init__.py", "__main__.py", "cli.py", "core.py"}:
        raise RuntimeError("Notes source package is incomplete")
    project = (root / "notes_runtime" / "pyproject.toml").read_text("utf-8")
    for exact in ('name = "cortex-notes"', 'version = "2.1.0"', 'dependencies = []'):
        if exact not in project:
            raise RuntimeError("Notes project contract mismatch")
    if "[project.scripts]" in project:
        raise RuntimeError("installed command is forbidden")
    return result


def _wheel(root: Path) -> bytes:
    members = _source(root)
    members.extend((
        (f"{DIST_INFO}/METADATA", (
            "Metadata-Version: 2.1\nName: cortex-notes\nVersion: 2.1.0\n"
            "Summary: Minimal file-native Notes runtime for Cortex\nRequires-Python: >=3.11,<3.12\n\n"
        ).encode()),
        (f"{DIST_INFO}/WHEEL", b"Wheel-Version: 1.0\nGenerator: cortex-notes-runtime\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"),
    ))
    rows = [[name, _record_digest(raw), str(len(raw))] for name, raw in members]
    rows.append([f"{DIST_INFO}/RECORD", "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    members.append((f"{DIST_INFO}/RECORD", buffer.getvalue().encode()))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, raw in members:
            archive.writestr(_zip_info(name), raw)
    return output.getvalue()


def _runner(digest: str) -> bytes:
    template = r'''#!/usr/bin/env python3
"""Offline, skill-local Cortex Notes launcher. Generated; do not edit."""
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

VERSION = "2.1.0"
WHEEL = "cortex_notes-2.1.0-py3-none-any.whl"
DIGEST = "__DIGEST__"
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
    expected = {"schema_version":1,"distribution":"cortex-notes","import":"cortex_notes","version":VERSION,"wheel":WHEEL,"wheel_sha256":DIGEST,"python":"3.11","isolation":"-I"}
    try: actual = json.loads(manifest.read_bytes().decode("utf-8"))
    except Exception as exc: raise BootstrapError("manifest_invalid") from exc
    if actual != expected or set(actual) != set(expected): raise BootstrapError("manifest_mismatch")
    try:
        raw = wheel.read_bytes()
        if hashlib.sha256(raw).hexdigest() != DIGEST: raise BootstrapError("wheel_digest_mismatch")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or "cortex_notes-2.1.0.dist-info/METADATA" not in names: raise BootstrapError("wheel_metadata_invalid")
            metadata = archive.read("cortex_notes-2.1.0.dist-info/METADATA").decode("utf-8")
            if "Name: cortex-notes\n" not in metadata or "Version: 2.1.0\n" not in metadata or "Requires-Dist:" in metadata: raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names): raise BootstrapError("wheel_entry_point_forbidden")
    except BootstrapError: raise
    except Exception as exc: raise BootstrapError("wheel_invalid") from exc
    if "cortex_notes" in sys.modules: raise BootstrapError("ambient_cortex_notes_loaded")
    sys.path.insert(0, str(wheel)); package = importlib.import_module("cortex_notes")
    origin = os.path.normcase(os.path.abspath(package.__file__ or "")).replace("\\", "/")
    prefix = os.path.normcase(str(wheel)).replace("\\", "/") + "/cortex_notes/"
    if package.__version__ != VERSION or not origin.startswith(prefix): raise BootstrapError("import_origin_mismatch")
    cli = importlib.import_module("cortex_notes.cli")
    return int(cli.main(sys.argv[1:]))

def main() -> int:
    try: return _run()
    except BootstrapError as exc:
        sys.stderr.write(f"cortex notes skill runtime error: {exc}\n"); return BOOTSTRAP_EXIT
    except Exception:
        sys.stderr.write("cortex notes skill runtime error: bootstrap_failed\n"); return BOOTSTRAP_EXIT

if __name__ == "__main__": raise SystemExit(main())
'''
    return template.replace("__DIGEST__", digest).encode()


def _payload(root: Path) -> dict[str, bytes]:
    wheel = _wheel(root); digest = _sha256(wheel)
    manifest = {"schema_version":1,"distribution":DISTRIBUTION,"import":IMPORT_NAME,"version":VERSION,"wheel":WHEEL_NAME,"wheel_sha256":digest,"python":"3.11","isolation":"-I"}
    return {
        "run_notes.py": _runner(digest),
        "run_notes.cmd": ("@echo off\r\nif not defined CORTEX_PYTHON (\r\n  >&2 echo cortex notes skill runtime error: cortex_python_required\r\n  exit /b 70\r\n)\r\n\"%CORTEX_PYTHON%\" -I \"%~dp0run_notes.py\" %*\r\nexit /b %ERRORLEVEL%\r\n").encode("ascii"),
        "runtime-manifest.json": (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        f"vendor/{WHEEL_NAME}": wheel,
    }


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _prepare(root: Path, expected: dict[str, bytes]) -> Path:
    target = root / ADAPTER
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode): raise RuntimeError("unsafe skill directory")
    scripts = target; vendor = scripts / "vendor"
    vendor.mkdir(exist_ok=True)
    allowed = set(expected)
    for path in scripts.rglob("*"):
        relative = path.relative_to(target).as_posix(); info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info): raise RuntimeError("linked runtime artifact")
        generated_wheel = relative.startswith("vendor/cortex_notes-") and relative.endswith("-py3-none-any.whl")
        if stat.S_ISREG(info.st_mode) and relative not in allowed and not generated_wheel: raise RuntimeError("unexpected runtime artifact")
        if stat.S_ISDIR(info.st_mode) and relative not in {"vendor"}: raise RuntimeError("unexpected runtime directory")
    return target


def _check_router(root: Path) -> None:
    target = root / "skills" / "cortex"
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("unsafe Cortex router")
    skill = target / "SKILL.md"
    info = skill.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Cortex router instructions missing")
    if not (target / "scripts" / "notes").is_dir(): raise RuntimeError("Cortex Notes adapter missing")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]; expected = _payload(root)
    _check_router(root)
    prepared = [_prepare(root, expected)]
    if not args.check:
        for skill in prepared:
            for stale in (skill / "vendor").glob("cortex_notes-*-py3-none-any.whl"):
                if stale.name != WHEEL_NAME: stale.unlink()
            for relative, raw in expected.items():
                path = skill / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    observed = []
    for skill in prepared:
        actual = {relative: (skill / relative).read_bytes() for relative in expected}
        if actual != expected: raise RuntimeError("Notes skill runtime drift")
        observed.append(actual)
    if any(value != observed[0] for value in observed[1:]): raise RuntimeError("Notes runtimes differ")
    print(f"{WHEEL_NAME} sha256={_sha256(expected[f'vendor/{WHEEL_NAME}'])}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
