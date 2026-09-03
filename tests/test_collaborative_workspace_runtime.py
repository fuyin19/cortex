from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "cortex" / "scripts" / "collaborative-workspace"
RUNNER = SKILL / "run_collaborative_workspace.py"
WHEEL = SKILL / "vendor" / "cortex_collaborative_workspace-1.1.1-py3-none-any.whl"
CORE_RUNNER = Path(os.environ["CORTEX_REAL_CORE_RUNNER"])
PAYLOADS = (
    "run_collaborative_workspace.py",
    "run_collaborative_workspace.cmd",
    "runtime-manifest.json",
    "vendor/cortex_collaborative_workspace-1.1.1-py3-none-any.whl",
)


def _environment() -> dict[str, str]:
    value = dict(os.environ)
    value["CORTEX_PYTHON"] = os.path.abspath(sys.executable)
    value["ANTI_ENTROPY_CORE_RUNNER"] = str(CORE_RUNNER.resolve())
    value["PIP_NO_INDEX"] = "1"
    return value


def _run(*args: str, skill: Path = SKILL, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(skill / "run_collaborative_workspace.py"), *args],
        cwd=skill, env=env or _environment(), capture_output=True, text=True, timeout=60, check=False,
    )


def test_workspace_runtime_is_deterministic_closed_and_dependency_free() -> None:
    before = {relative: (SKILL / relative).read_bytes() for relative in PAYLOADS}
    checked = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "package_collaborative_workspace_runtime.py"), "--check"],
        cwd=ROOT, env=_environment(), capture_output=True, text=True, timeout=30, check=False,
    )
    after = {relative: (SKILL / relative).read_bytes() for relative in PAYLOADS}
    assert checked.returncode == 0 and checked.stderr == "" and "sha256=" in checked.stdout
    assert before == after
    manifest = json.loads((SKILL / "runtime-manifest.json").read_text("utf-8"))
    assert manifest == {
        "distribution": "cortex-collaborative-workspace",
        "import": "cortex_collaborative_workspace",
        "isolation": "-I",
        "python": "3.11",
        "schema_version": 1,
        "version": "1.1.1",
        "wheel": WHEEL.name,
        "wheel_sha256": hashlib.sha256(WHEEL.read_bytes()).hexdigest(),
    }
    with zipfile.ZipFile(WHEEL) as archive:
        names = archive.namelist()
        metadata = archive.read("cortex_collaborative_workspace-1.1.1.dist-info/METADATA").decode("utf-8")
    assert "Requires-Dist:" not in metadata and not any(name.endswith("entry_points.txt") for name in names)
    assert "cortex_collaborative_workspace/workspace.py" in names
    assert not (SKILL / "SKILL.md").exists()


def test_workspace_runtime_version_tamper_and_binding_fail_closed(tmp_path: Path) -> None:
    version = _run("--version")
    assert version.returncode == 0 and version.stdout == "cortex-collaborative-workspace 1.1.1\n"
    copied = tmp_path / "skill"
    shutil.copytree(SKILL, copied)
    wheel = copied / "vendor" / WHEEL.name
    wheel.write_bytes(wheel.read_bytes() + b"tamper")
    tampered = _run("--version", skill=copied)
    assert tampered.returncode == 70 and "wheel_digest_mismatch" in tampered.stderr
    environment = _environment()
    environment.pop("CORTEX_PYTHON")
    missing = _run("--version", env=environment)
    assert missing.returncode == 70 and "cortex_python_required" in missing.stderr


def test_workspace_runtime_packaged_create_and_closed_surface(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    created = _run("--json", "prepare", "--root", str(root))
    payload = json.loads(created.stdout)
    assert created.returncode == 0 and created.stderr == ""
    assert payload["command"] == "collaborative_workspace.prepare"
    assert set(payload) == {"status", "exit_code", "command", "data", "issues"}
    assert payload["data"]["action"] == "created"
    status = _run("--json", "status", "--root", str(root))
    assert status.returncode == 0 and json.loads(status.stdout)["data"]["state"] == "ready"
    rejected = _run("--json", "delete", "--root", str(root))
    assert rejected.returncode == 2 and json.loads(rejected.stdout)["issues"] == [{"code": "invalid_arguments"}]


def test_workspace_surface_fixture_and_router_are_exact() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex-collaborative-workspace-surface.json").read_text("utf-8"))
    assert fixture["routes"] == [
        "collaborative_workspace.prepare", "collaborative_workspace.status", "collaborative_workspace.validate",
    ]
    assert fixture["skill"] == "cortex"
    assert fixture["adapter"] == "skills/cortex/scripts/collaborative-workspace"
    router = (ROOT / "skills" / "cortex" / "SKILL.md").read_text("utf-8")
    assert "Collaborative Workspace" in router and "scripts/" in router


def test_workspace_runtime_source_and_wheel_have_parity(tmp_path: Path) -> None:
    roots = [tmp_path / "source", tmp_path / "wheel"]
    code = (
        "import sys;"
        f"sys.path.insert(0,{str(ROOT / 'collaborative_workspace_runtime' / 'src')!r});"
        "from cortex_collaborative_workspace.cli import main;"
        "raise SystemExit(main(sys.argv[1:]))"
    )
    source = subprocess.run(
        [sys.executable, "-I", "-c", code, "--json", "prepare", "--root", str(roots[0])],
        cwd=ROOT, env=_environment(), capture_output=True, text=True, timeout=60, check=False,
    )
    bundled = _run("--json", "prepare", "--root", str(roots[1]))
    assert source.returncode == bundled.returncode == 0
    source_value, bundled_value = json.loads(source.stdout), json.loads(bundled.stdout)
    for value in (source_value, bundled_value):
        value["data"].pop("workspace_id")
    assert source_value == bundled_value
    def normalized_tree(root: Path) -> dict[str, bytes]:
        values = {}
        for path in root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                raw = path.read_bytes()
                if relative in {"collaborative-workspace.json", "agent-workbench/ref/.agent-workbench.json"}:
                    parsed = json.loads(raw)
                    parsed["workspace_id"] = "<workspace-id>"
                    raw = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                values[relative] = raw
        return values
    assert normalized_tree(roots[0]) == normalized_tree(roots[1])
