#!/usr/bin/env python3
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
