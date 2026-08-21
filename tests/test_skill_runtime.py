from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from cortex.constants import PUBLIC_ROUTES, VERSION


ROOT = Path(__file__).parents[1]
SKILL_NAMES = ("cortex-build", "cortex-manage")
WHEEL_NAME = "cortex_record_kb-7.0.0-py3-none-any.whl"
PAYLOAD_PATHS = (
    Path("scripts/run_cortex.py"),
    Path("scripts/runtime-manifest.json"),
    Path("scripts/vendor") / WHEEL_NAME,
)
RUNTIME_SCENARIOS = {
    "runtime-sc001": "Each complete skill copy runs Cortex 7.0.0 independently.",
    "runtime-sc002": "Both skills carry byte-identical runner, manifest, and wheel payloads.",
    "runtime-sc003": "PATH Cortex 4 sentinels are never invoked.",
    "runtime-sc004": "Hostile PYTHONPATH and ambient Cortex modules are ignored by isolated launch.",
    "runtime-sc005": "Runtime launch performs no child install, network, cache, or update action.",
    "runtime-sc006": "Missing, truncated, modified, linked, and wrong-version runtime inputs fail before CLI dispatch.",
    "runtime-sc007": "Coordinated wheel and manifest tampering fails against the runner-pinned digest before import.",
    "runtime-sc008": "Offline deterministic regeneration and Candidate parity checks pass.",
    "runtime-sc009": "The embedded wheel has exact Cortex metadata, no dependencies, and no console script.",
    "runtime-sc010": "A disposable wheel projection creates no command launcher.",
    "runtime-sc011": "The Cortex 7 public routes, package version, and source CLI contract remain closed.",
    "runtime-sc012": "Source and both bundled runtimes produce equal Results and disposable Bundle trees.",
    "runtime-sc013": "Skills, documentation, capability fixture, and runtime scenario mapping agree.",
}


def _skill(name: str) -> Path:
    return ROOT / "skills" / name


def _runner(skill: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(skill / "scripts" / "run_cortex.py"), *args],
        cwd=skill,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _tree(root: Path) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root).as_posix()
        result[relative] = None if path.is_dir() else path.read_bytes()
    return result


def _copy_skill(tmp_path: Path, name: str = "cortex-build") -> Path:
    destination = tmp_path / name
    shutil.copytree(_skill(name), destination)
    return destination


def test_runtime_sc001_independent_complete_skill_copies(tmp_path: Path) -> None:
    for name in SKILL_NAMES:
        copied = _copy_skill(tmp_path, name)
        result = _runner(copied, "--version")
        assert result.returncode == 0
        assert result.stdout == "cortex 7.0.0\n" and result.stderr == ""
        shutil.rmtree(copied)


def test_runtime_sc002_payloads_are_byte_identical_and_complete() -> None:
    observed = []
    for name in SKILL_NAMES:
        skill = _skill(name)
        assert all((skill / relative).is_file() and not (skill / relative).is_symlink() for relative in PAYLOAD_PATHS)
        observed.append({relative.as_posix(): (skill / relative).read_bytes() for relative in PAYLOAD_PATHS})
    assert observed[0] == observed[1]


def test_runtime_sc003_path_cortex4_sentinel_is_never_used(tmp_path: Path) -> None:
    sentinel = tmp_path / "path-v4"
    sentinel.mkdir()
    marker = tmp_path / "v4-was-called"
    # This safely represents both a stale PATH executable and its side effect; it is never invoked.
    (sentinel / "cortex.exe").write_text(f"cortex 4.0.0\n{marker}\n", encoding="utf-8")
    (sentinel / "cortex.bat").write_text(f"@echo cortex 4.0.0>{marker}\n", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(sentinel) + os.pathsep + env.get("PATH", "")
    result = _runner(_skill("cortex-build"), "--version", env=env)
    assert result.returncode == 0 and result.stdout == "cortex 7.0.0\n" and result.stderr == ""
    assert not marker.exists()


def test_runtime_sc004_hostile_pythonpath_and_ambient_module_are_ignored(tmp_path: Path) -> None:
    hostile = tmp_path / "hostile"
    package = hostile / "cortex"
    package.mkdir(parents=True)
    marker = tmp_path / "ambient-was-imported"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n__version__='4.0.0'\n",
        encoding="utf-8",
    )
    (hostile / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('site')\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(hostile)
    result = _runner(_skill("cortex-manage"), "--version", env=env)
    assert result.returncode == 0 and result.stdout == "cortex 7.0.0\n" and result.stderr == ""
    assert not marker.exists()


def test_runtime_sc005_no_child_network_install_update_or_cache(tmp_path: Path) -> None:
    copied = _copy_skill(tmp_path)
    before = _tree(copied)
    runner_text = (copied / "scripts" / "run_cortex.py").read_text("utf-8")
    builder_text = (ROOT / "tools" / "package_skill_runtime.py").read_text("utf-8")
    for forbidden in ("import subprocess", "import socket", "import urllib", "import requests"):
        assert forbidden not in runner_text and forbidden not in builder_text
    for forbidden_call in ("pip install", "latest", "auto-update"):
        assert forbidden_call not in runner_text.lower()
    sentinel = tmp_path / "commands"
    sentinel.mkdir()
    marker = tmp_path / "child-was-called"
    for command in ("cortex", "pip", "curl", "wget"):
        (sentinel / f"{command}.bat").write_text(f"@echo called>{marker}\n", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(sentinel) + os.pathsep + env.get("PATH", "")
    result = _runner(copied, "--version", env=env)
    assert result.returncode == 0 and not marker.exists()
    assert _tree(copied) == before


@pytest.mark.parametrize("fault", ["missing", "truncated", "modified", "wrong-version"])
def test_runtime_sc006_invalid_runtime_fails_before_dispatch(tmp_path: Path, fault: str) -> None:
    copied = _copy_skill(tmp_path)
    wheel = copied / "scripts" / "vendor" / WHEEL_NAME
    manifest = copied / "scripts" / "runtime-manifest.json"
    if fault == "missing":
        wheel.unlink()
    elif fault == "truncated":
        wheel.write_bytes(wheel.read_bytes()[:64])
    elif fault == "modified":
        wheel.write_bytes(wheel.read_bytes() + b"tamper")
    else:
        value = json.loads(manifest.read_text("utf-8"))
        value["version"] = "4.0.0"
        manifest.write_text(json.dumps(value), encoding="utf-8")
    marker = tmp_path / "dispatch-marker"
    result = _runner(copied, "--workspace", str(marker), "manage", "init")
    assert result.returncode == 70 and result.stdout == ""
    assert result.stderr.startswith("cortex skill runtime error: ")
    assert not marker.exists()


def test_runtime_sc006_linked_manifest_rejected_when_supported(tmp_path: Path) -> None:
    copied = _copy_skill(tmp_path)
    manifest = copied / "scripts" / "runtime-manifest.json"
    external = tmp_path / "external-manifest.json"
    external.write_bytes(manifest.read_bytes())
    manifest.unlink()
    try:
        manifest.symlink_to(external)
    except OSError:
        runner_text = (copied / "scripts" / "run_cortex.py").read_text("utf-8")
        assert "stat.S_ISLNK" in runner_text and "FILE_ATTRIBUTE_REPARSE_POINT" in runner_text
        return
    result = _runner(copied, "--version")
    assert result.returncode == 70 and "runtime_path_reparse" in result.stderr


def test_runtime_sc007_coordinated_wheel_manifest_tamper_fails_before_import(tmp_path: Path) -> None:
    copied = _copy_skill(tmp_path)
    wheel = copied / "scripts" / "vendor" / WHEEL_NAME
    marker = tmp_path / "tampered-wheel-imported"
    replacement = tmp_path / "replacement.whl"
    with zipfile.ZipFile(wheel, "r") as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename == "cortex/__init__.py":
                raw += f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n".encode("utf-8")
            target.writestr(info, raw)
    shutil.move(replacement, wheel)
    manifest = copied / "scripts" / "runtime-manifest.json"
    value = json.loads(manifest.read_text("utf-8"))
    value["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    result = _runner(copied, "--version")
    assert result.returncode == 70 and "manifest_mismatch" in result.stderr
    assert not marker.exists()


def test_runtime_sc008_offline_deterministic_candidate_check() -> None:
    before = [{relative.as_posix(): (_skill(name) / relative).read_bytes() for relative in PAYLOAD_PATHS} for name in SKILL_NAMES]
    env = dict(os.environ)
    env["PIP_NO_INDEX"] = "1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "package_skill_runtime.py"), "--check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    after = [{relative.as_posix(): (_skill(name) / relative).read_bytes() for relative in PAYLOAD_PATHS} for name in SKILL_NAMES]
    assert result.returncode == 0 and result.stderr == "" and "sha256=" in result.stdout
    assert before == after and before[0] == before[1]


def test_runtime_sc009_wheel_metadata_has_no_dependencies_or_command() -> None:
    wheel = _skill("cortex-build") / "scripts" / "vendor" / WHEEL_NAME
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = archive.read("cortex_record_kb-7.0.0.dist-info/METADATA").decode("utf-8")
    assert "Name: cortex-record-kb\n" in metadata and "Version: 7.0.0\n" in metadata
    assert "Requires-Dist:" not in metadata
    assert not any(name.endswith("entry_points.txt") for name in names)
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert "[project.scripts]" not in pyproject and 'cortex = "cortex.cli:main"' not in pyproject


def test_runtime_sc010_disposable_projection_has_no_launcher(tmp_path: Path) -> None:
    wheel = _skill("cortex-manage") / "scripts" / "vendor" / WHEEL_NAME
    projection = tmp_path / "projection"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(projection)
    files = [path.relative_to(projection).as_posix() for path in projection.rglob("*") if path.is_file()]
    assert "cortex/__main__.py" in files
    assert not any(path.casefold().endswith(("cortex.exe", "cortex-script.py", "entry_points.txt")) for path in files)


def test_runtime_sc011_source_contract_is_unchanged() -> None:
    assert VERSION == "7.0.0"
    assert tuple(PUBLIC_ROUTES) == (
        "registry.show", "registry.validate", "registry.resolve", "registry.set",
        "manage.init", "manage.status", "manage.validate", "manage.config.show", "manage.config.set",
        "record.add", "record.edit", "record.show", "record.delete",
    )


def _source_cli(*args: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys;"
        f"sys.path.insert(0,{str(ROOT / 'src')!r});"
        "from cortex.cli import main;"
        "raise SystemExit(main(sys.argv[1:]))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", code, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_runtime_sc012_source_and_bundles_have_equal_results_and_trees(tmp_path: Path) -> None:
    workspaces = [tmp_path / "source", tmp_path / "build-skill", tmp_path / "manage-skill"]
    common = ("--json", "--workspace")
    source = _source_cli(*common, str(workspaces[0]), "manage", "init")
    build = _runner(_skill("cortex-build"), *common, str(workspaces[1]), "manage", "init")
    manage = _runner(_skill("cortex-manage"), *common, str(workspaces[2]), "manage", "init")
    results = [source, build, manage]
    assert all(result.returncode == 0 and result.stderr == "" for result in results)
    assert json.loads(source.stdout) == json.loads(build.stdout) == json.loads(manage.stdout)
    assert _tree(workspaces[0]) == _tree(workspaces[1]) == _tree(workspaces[2])
    status_results = [
        _source_cli(*common, str(workspaces[0]), "manage", "status"),
        _runner(_skill("cortex-build"), *common, str(workspaces[1]), "manage", "status"),
        _runner(_skill("cortex-manage"), *common, str(workspaces[2]), "manage", "status"),
    ]
    assert [json.loads(result.stdout) for result in status_results] == [json.loads(status_results[0].stdout)] * 3


def test_runtime_sc013_surfaces_and_exact_mapping_agree() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    assert fixture["global_command"] is False
    assert fixture["agent_entrypoint"] == "absolute-python-3.11 -I skill-local-runner"
    assert fixture["skill_runtime"] == {
        "artifact": WHEEL_NAME,
        "offline": True,
        "independently_complete": True,
        "payloads_byte_identical": True,
    }
    combined = "\n".join(
        (ROOT / relative).read_text("utf-8")
        for relative in (
            "AGENTS.md", "README.md", "docs/global-knowledge.md", "docs/record-kb-architecture.md",
            "skills/cortex-build/SKILL.md", "skills/cortex-manage/SKILL.md",
        )
    )
    for required in ("-I", "skill-local", "7.0.0", "complete", "global"):
        assert required in combined
    matrix = (ROOT / "docs" / "verification-matrix.md").read_text("utf-8")
    actual: dict[str, str] = {}
    for line in matrix.splitlines():
        if line.startswith("| runtime-sc"):
            cells = [cell.strip() for cell in line.split("|")]
            actual[cells[1]] = cells[2]
    assert actual == RUNTIME_SCENARIOS
