#!/usr/bin/env python3
"""Build and verify the deterministic Collaborative Workspace skill runtime."""

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


VERSION = "1.2.0"
DISTRIBUTION = "cortex-collaborative-workspace"
IMPORT_NAME = "cortex_collaborative_workspace"
WHEEL_NAME = "cortex_collaborative_workspace-1.2.0-py3-none-any.whl"
DIST_INFO = "cortex_collaborative_workspace-1.2.0.dist-info"
ADAPTER = Path("skills/cortex/scripts/collaborative-workspace")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SOURCE_FILES = {"__init__.py", "__main__.py", "cli.py", "core_runner.py", "workspace.py"}


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
    package = root / "collaborative_workspace_runtime" / "src" / IMPORT_NAME
    members: list[tuple[str, bytes]] = []
    for path in sorted(package.glob("*.py"), key=lambda item: item.name.encode("utf-8")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("unsafe Collaborative Workspace source")
        members.append((f"{IMPORT_NAME}/{path.name}", path.read_bytes()))
    if {name.rsplit("/", 1)[-1] for name, _raw in members} != SOURCE_FILES:
        raise RuntimeError("Collaborative Workspace source package is incomplete")
    project = (root / "collaborative_workspace_runtime" / "pyproject.toml").read_text("utf-8")
    for exact in (
        'name = "cortex-collaborative-workspace"', 'version = "1.2.0"', 'dependencies = []',
    ):
        if exact not in project:
            raise RuntimeError("Collaborative Workspace project contract mismatch")
    if "[project.scripts]" in project:
        raise RuntimeError("installed command is forbidden")
    return members


def _wheel(root: Path) -> bytes:
    members = _source(root)
    members.extend((
        (f"{DIST_INFO}/METADATA", (
            "Metadata-Version: 2.1\nName: cortex-collaborative-workspace\nVersion: 1.2.0\n"
            "Summary: Explicit Collaborative Workspace and Agent Workbench runtime for Cortex\n"
            "Requires-Python: >=3.11,<3.12\n\n"
        ).encode()),
        (f"{DIST_INFO}/WHEEL", b"Wheel-Version: 1.0\nGenerator: cortex-collaborative-workspace-runtime\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"),
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
"""Offline Collaborative Workspace launcher. Generated; do not edit."""
from __future__ import annotations
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import unicodedata
import zipfile

VERSION = "1.2.0"
WHEEL = "cortex_collaborative_workspace-1.2.0-py3-none-any.whl"
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
    expected = {"schema_version":1,"distribution":"cortex-collaborative-workspace","import":"cortex_collaborative_workspace","version":VERSION,"wheel":WHEEL,"wheel_sha256":DIGEST,"python":"3.11","isolation":"-I"}
    try: actual = json.loads(manifest.read_bytes().decode("utf-8"))
    except Exception as exc: raise BootstrapError("manifest_invalid") from exc
    if actual != expected or set(actual) != set(expected): raise BootstrapError("manifest_mismatch")
    try:
        raw = wheel.read_bytes()
        if hashlib.sha256(raw).hexdigest() != DIGEST: raise BootstrapError("wheel_digest_mismatch")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata_name = "cortex_collaborative_workspace-1.2.0.dist-info/METADATA"
            if len(names) != len(set(names)) or metadata_name not in names: raise BootstrapError("wheel_metadata_invalid")
            metadata = archive.read(metadata_name).decode("utf-8")
            if "Name: cortex-collaborative-workspace\n" not in metadata or "Version: 1.2.0\n" not in metadata or "Requires-Dist:" in metadata: raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names): raise BootstrapError("wheel_entry_point_forbidden")
    except BootstrapError: raise
    except Exception as exc: raise BootstrapError("wheel_invalid") from exc
    if "cortex_collaborative_workspace" in sys.modules: raise BootstrapError("ambient_workspace_runtime_loaded")
    sys.path.insert(0, str(wheel)); package = importlib.import_module("cortex_collaborative_workspace")
    origin = os.path.normcase(os.path.abspath(package.__file__ or "")).replace("\\", "/")
    prefix = os.path.normcase(str(wheel)).replace("\\", "/") + "/cortex_collaborative_workspace/"
    if package.__version__ != VERSION or not origin.startswith(prefix): raise BootstrapError("import_origin_mismatch")
    core_client = importlib.import_module("cortex_collaborative_workspace.core_runner")
    workspace_client = importlib.import_module("cortex_collaborative_workspace.workspace")
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
                workspace_client.set_default_provider_runners({
                    "file-conversion": boundary.parent / "file-conversion" / "scripts" / "pipeline.py",
                    "markdown-conversion": boundary.parent / "markdown-conversion" / "scripts" / "pipeline.py",
                })
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
'''
    return template.replace("__DIGEST__", digest).encode()


def _selector() -> bytes:
    return r'''#!/usr/bin/env python3
"""Private deterministic interpreter selector for Collaborative Workspace."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unicodedata

EXIT_BOOTSTRAP = 70
MAX_INVENTORY = 1024 * 1024
MAX_CANDIDATES = 1000
PROBE = "import json,sys,unicodedata;print(json.dumps({'python':list(sys.version_info[:2]),'ucd':unicodedata.unidata_version,'isolated':bool(sys.flags.isolated),'bytecode':bool(sys.dont_write_bytecode)},sort_keys=True,separators=(',',':')))"

def fail(code: str) -> int:
    sys.stderr.write(f"cortex collaborative workspace runtime error: {code}\n")
    return EXIT_BOOTSTRAP

def ordinary(path: Path) -> Path:
    if not path.is_absolute(): raise ValueError
    resolved = path.resolve(strict=True)
    info = resolved.lstat()
    if stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400) or not stat.S_ISREG(info.st_mode):
        raise ValueError
    return resolved

def probe(path: Path) -> bool:
    try:
        completed = subprocess.run([str(path), "-I", "-B", "-c", PROBE], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
        if len(completed.stdout) > 512 or len(completed.stderr) > 512 or completed.returncode != 0 or completed.stderr:
            return False
        value = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        return value == {"python":[3,11],"ucd":"14.0.0","isolated":True,"bytecode":True}
    except (OSError, subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError):
        return False

def run_child(python: Path, runner: Path, argv: list[str], has_fallback: bool) -> int:
    env = dict(os.environ)
    env["CORTEX_PYTHON"] = str(python)
    if has_fallback: env["CORTEX_RUNTIME_FALLBACK"] = "1"
    else: env.pop("CORTEX_RUNTIME_FALLBACK", None)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    child = subprocess.Popen([str(python), "-I", "-B", str(runner), *argv], env=env,
                             stdin=subprocess.DEVNULL, creationflags=flags)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.kill()
        child.wait()
        raise

def main() -> int:
    discover = len(sys.argv) > 1 and sys.argv[1] == "--discover"
    try:
        runner_index = 2 if discover else 1
        runner = ordinary(Path(sys.argv[runner_index]))
        if discover:
            raw_candidates = [
                os.environ.get("CORTEX_PYTHON", ""),
                str(Path(os.environ["VIRTUAL_ENV"]) / "bin" / "python") if os.environ.get("VIRTUAL_ENV") else "",
                str(Path(os.environ["CONDA_PREFIX"]) / "bin" / "python") if os.environ.get("CONDA_PREFIX") else "",
                shutil.which("python3.11") or "", shutil.which("python3") or "", sys.executable,
                "/opt/homebrew/bin/python3.11", "/usr/local/bin/python3.11",
            ]
            candidates = []
            for item in raw_candidates:
                if item and item not in candidates:
                    candidates.append(item)
        else:
            raw = sys.stdin.buffer.read(MAX_INVENTORY + 1)
            if len(raw) > MAX_INVENTORY: return fail("runtime_inventory_transport_invalid")
            value = json.loads(raw.decode("utf-8", errors="strict"))
            if not isinstance(value, dict) or set(value) != {"schema_version","candidates","probed","end"} or value["schema_version"] != 1:
                return fail("runtime_inventory_transport_invalid")
            candidates = value["candidates"]
            if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES or value["end"] != len(candidates) or value["probed"] != 0:
                return fail("runtime_inventory_transport_invalid")
        paths: list[Path] = []
        seen: set[str] = set()
        for raw_path in candidates:
            if not isinstance(raw_path, str): return fail("runtime_inventory_transport_invalid")
            path = ordinary(Path(raw_path))
            key = os.path.normcase(os.path.abspath(raw_path))
            if key in seen: return fail("runtime_inventory_transport_invalid")
            seen.add(key); paths.append(path)
        if not discover and not os.path.samefile(paths[0], sys.executable): return fail("runtime_inventory_transport_invalid")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, IndexError):
        return fail("runtime_inventory_transport_invalid")
    eligible = [path for path in paths if probe(path)] if discover else [paths[0], *(path for path in paths[1:] if probe(path))]
    if not eligible:
        return fail("no_compatible_python")
    for index, path in enumerate(eligible):
        code = run_child(path, runner, sys.argv[runner_index + 1:], index + 1 < len(eligible))
        if code != 75:
            return code
    return fail("conversion_python_dependency_unavailable")

if __name__ == "__main__":
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
'''.encode("utf-8")


def _powershell_launcher() -> bytes:
    return r'''$ErrorActionPreference = "Stop"
$scripts = [IO.Path]::GetDirectoryName($PSCommandPath)
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$candidates = [Collections.Generic.List[string]]::new()
function Add-Candidate([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try { $full = [IO.Path]::GetFullPath($Path); $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop } catch { return }
    if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { return }
    if ($seen.Add($full)) { $candidates.Add($full) }
}
Add-Candidate $env:CORTEX_PYTHON
if ($env:VIRTUAL_ENV) { Add-Candidate (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe') }
if ($env:CONDA_PREFIX) { Add-Candidate (Join-Path $env:CONDA_PREFIX 'python.exe') }
Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { Add-Candidate $_.Source }
$registryRoots = @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore')
foreach ($root in $registryRoots) {
    Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue | Sort-Object PSChildName | ForEach-Object {
        try { Add-Candidate ((Get-ItemProperty -LiteralPath (Join-Path $_.PSPath 'InstallPath') -ErrorAction Stop).ExecutablePath) } catch {}
    }
}
if ($env:LOCALAPPDATA) { Add-Candidate (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe') }
$probe = "import json,sys,unicodedata;print(json.dumps({'python':list(sys.version_info[:2]),'ucd':unicodedata.unidata_version,'isolated':bool(sys.flags.isolated),'bytecode':bool(sys.dont_write_bytecode)},sort_keys=True,separators=(',',':')))"
$selected = $null; $selectedIndex = -1
for ($index = 0; $index -lt $candidates.Count; $index++) {
    $path = $candidates[$index]
    try {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $path; $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
        $start.CreateNoWindow = $true; $start.Arguments = '-I -B -c "' + $probe + '"'
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.WaitForExit(); continue }
        $value = $process.StandardOutput.ReadToEnd().TrimEnd("`r", "`n")
        $errorText = $process.StandardError.ReadToEnd()
    } catch { continue }
    if ($process.ExitCode -eq 0 -and -not $errorText -and $value -eq '{"bytecode":true,"isolated":true,"python":[3,11],"ucd":"14.0.0"}') { $selected = $path; $selectedIndex = $index; break }
}
if (-not $selected) { [Console]::Error.WriteLine('cortex collaborative workspace runtime error: no_compatible_python'); exit 70 }
$ordered = @($selected)
for ($index = $selectedIndex + 1; $index -lt $candidates.Count; $index++) { $ordered += $candidates[$index] }
$payload = @{schema_version=1;candidates=$ordered;probed=0;end=$ordered.Count} | ConvertTo-Json -Compress
$payload | & $selected -I -B (Join-Path $scripts 'select_collaborative_workspace.py') (Join-Path $scripts 'run_collaborative_workspace.py') @args
exit $LASTEXITCODE
'''.encode("utf-8")


def _posix_launcher() -> bytes:
    return r'''#!/bin/sh
set -eu
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
host=${CORTEX_PYTHON:-}
if [ -z "$host" ]; then host=$(command -v python3 2>/dev/null || true); fi
if [ -z "$host" ]; then printf '%s\n' 'cortex collaborative workspace runtime error: python_host_required' >&2; exit 70; fi
exec "$host" -I -B "$scripts/select_collaborative_workspace.py" --discover "$scripts/run_collaborative_workspace.py" "$@"
'''.encode("utf-8")


def _payload(root: Path) -> dict[str, bytes]:
    wheel = _wheel(root)
    digest = _sha256(wheel)
    manifest = {
        "schema_version": 1, "distribution": DISTRIBUTION, "import": IMPORT_NAME, "version": VERSION,
        "wheel": WHEEL_NAME, "wheel_sha256": digest, "python": "3.11", "isolation": "-I",
    }
    return {
        "run_collaborative_workspace.py": _runner(digest),
        "select_collaborative_workspace.py": _selector(),
        "run_collaborative_workspace.ps1": _powershell_launcher(),
        "run_collaborative_workspace.sh": _posix_launcher(),
        "run_collaborative_workspace.cmd": (
            "@echo off\r\nif not defined CORTEX_PYTHON (\r\n"
            "  >&2 echo cortex collaborative workspace runtime error: cortex_python_required\r\n"
            "  exit /b 70\r\n)\r\n"
            '"%CORTEX_PYTHON%" -I -B "%~dp0run_collaborative_workspace.py" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        ).encode("ascii"),
        "runtime-manifest.json": (_canonical_manifest(manifest) + "\n").encode("utf-8"),
        f"vendor/{WHEEL_NAME}": wheel,
    }


def _canonical_manifest(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _prepare(root: Path, expected: dict[str, bytes]) -> Path:
    target = root / ADAPTER
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("unsafe Collaborative Workspace skill directory")
    scripts = target
    vendor = scripts / "vendor"
    vendor.mkdir(exist_ok=True)
    allowed = set(expected)
    for path in scripts.rglob("*"):
        relative = path.relative_to(target).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise RuntimeError("linked runtime artifact")
        generated_wheel = relative.startswith("vendor/cortex_collaborative_workspace-") and relative.endswith("-py3-none-any.whl")
        if stat.S_ISREG(info.st_mode) and relative not in allowed and not generated_wheel:
            raise RuntimeError("unexpected runtime artifact")
        if stat.S_ISDIR(info.st_mode) and relative not in {"vendor"}:
            raise RuntimeError("unexpected runtime directory")
    return target


def _check_router(root: Path) -> None:
    target = root / "skills" / "cortex"
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("unsafe Cortex router")
    if not (target / "scripts" / "collaborative-workspace").is_dir(): raise RuntimeError("Collaborative Workspace adapter missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    expected = _payload(root)
    _check_router(root)
    target = _prepare(root, expected)
    if not args.check:
        for stale in (target / "vendor").glob("cortex_collaborative_workspace-*-py3-none-any.whl"):
            if stale.name != WHEEL_NAME:
                stale.unlink()
        for relative, raw in expected.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    if sorted(path.name for path in (target / "vendor").glob("*.whl")) != [WHEEL_NAME]:
        raise RuntimeError("unexpected runtime artifact")
    actual = {relative: (target / relative).read_bytes() for relative in expected}
    if actual != expected:
        raise RuntimeError("Collaborative Workspace skill runtime drift")
    print(f"{WHEEL_NAME} sha256={_sha256(expected[f'vendor/{WHEEL_NAME}'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
