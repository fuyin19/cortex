from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile

import pytest

from cortex.constants import PUBLIC_ROUTES, VERSION


ROOT = Path(__file__).parents[1]
SKILL_NAMES = (
    "cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage",
)
BATCH_SKILL_NAMES = ("cortex-kb-ingest",)
NON_BATCH_SKILL_NAMES = tuple(name for name in SKILL_NAMES if name not in BATCH_SKILL_NAMES)
WHEEL_NAME = "cortex_record_kb-8.1.0-py3-none-any.whl"
PAYLOAD_PATHS = (
    Path("run_cortex.py"),
    Path("run_cortex.cmd"),
    Path("runtime-manifest.json"),
    Path("vendor") / WHEEL_NAME,
)
RUNTIME_SCENARIOS = {
    "runtime-sc001": "Each of three KB skill copies runs Cortex 8.1.0 with the explicitly configured external Core runner.",
    "runtime-sc002": "All three KB skills carry byte-identical runner, manifest, and wheel payloads.",
    "runtime-sc003": "PATH Cortex 4 sentinels are never invoked.",
    "runtime-sc004": "Hostile PYTHONPATH and ambient Cortex modules are ignored by isolated launch.",
    "runtime-sc005": "Runtime launch performs no child install, network, cache, or update action.",
    "runtime-sc006": "Missing, truncated, modified, linked, and wrong-version runtime inputs fail before CLI dispatch.",
    "runtime-sc007": "Coordinated wheel and manifest tampering fails against the runner-pinned digest before import.",
    "runtime-sc008": "Offline deterministic regeneration and Candidate parity checks pass.",
    "runtime-sc009": "The embedded wheel has exact Cortex metadata, no dependencies, and no console script.",
    "runtime-sc010": "A disposable wheel projection creates no command launcher.",
    "runtime-sc011": "The Cortex 8 public routes, package version, and source CLI contract remain closed.",
    "runtime-sc012": "Source and all three bundled KB runtimes produce equal Results and disposable Bundle trees.",
    "runtime-sc013": "Skills, documentation, capability fixture, and runtime scenario mapping agree.",
    "runtime-sc014": "CORTEX_PYTHON binds the exact Python 3.11/UCD 14 executable, and non-init routes require an explicit absolute ANTI_ENTROPY_CORE_RUNNER; invalid configuration fails before mutation.",
    "runtime-sc015": "Human stdout and stderr are UTF-8 while compact JSON Result bytes retain ASCII escaping and shape.",
    "runtime-sc016": "The ingest skill helper accepts full and Markdown-only items and returns one ordered wrapper summary; manage and build have none.",
    "runtime-sc017": "The ingest helper collects a valid middle Cortex failure and continues later batch items sequentially.",
    "runtime-sc018": "The ingest helper rejects malformed jobs, duplicate ids, and relative item paths before any runner call or Bundle mutation.",
    "runtime-sc019": "A populated Bundle accepts a complete keyed-monotonic Tag 2 expansion, preserves existing records and byte-identical Layout 5, and creates the new partition only on the next record add.",
}


def _skill(name: str) -> Path:
    assert name in SKILL_NAMES
    return ROOT / "skills" / "cortex" / "scripts" / "kb"


def _runner(
    skill: Path,
    *args: str,
    env: dict[str, str] | None = None,
    cortex_python: str | None = os.path.abspath(sys.executable),
) -> subprocess.CompletedProcess[str]:
    launch_env = dict(os.environ if env is None else env)
    if cortex_python is None:
        launch_env.pop("CORTEX_PYTHON", None)
    else:
        launch_env["CORTEX_PYTHON"] = cortex_python
    return subprocess.run(
        [sys.executable, "-I", str(skill / "run_cortex.py"), *args],
        cwd=skill,
        env=launch_env,
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


def _copy_skill(tmp_path: Path, name: str = "cortex-kb-ingest") -> Path:
    destination = tmp_path / name
    shutil.copytree(_skill(name), destination)
    return destination


def test_runtime_sc001_independent_complete_skill_copies(tmp_path: Path) -> None:
    for name in SKILL_NAMES:
        copied = _copy_skill(tmp_path, name)
        result = _runner(copied, "--version")
        assert result.returncode == 0
        assert result.stdout == "cortex 8.1.0\n" and result.stderr == ""
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
    result = _runner(_skill("cortex-kb-ingest"), "--version", env=env)
    assert result.returncode == 0 and result.stdout == "cortex 8.1.0\n" and result.stderr == ""
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
    result = _runner(_skill("cortex-kb-manage"), "--version", env=env)
    assert result.returncode == 0 and result.stdout == "cortex 8.1.0\n" and result.stderr == ""
    assert not marker.exists()


def test_runtime_sc005_no_child_network_install_update_or_cache(tmp_path: Path) -> None:
    copied = _copy_skill(tmp_path)
    before = _tree(copied)
    runner_text = (copied / "run_cortex.py").read_text("utf-8")
    builder_text = (ROOT / "tools" / "package_skill_runtime.py").read_text("utf-8")
    for forbidden in ("import subprocess", "import socket", "import urllib", "import requests"):
        assert forbidden not in runner_text
    for forbidden in ("import socket", "import urllib", "import requests"):
        assert forbidden not in builder_text
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
    wheel = copied / "vendor" / WHEEL_NAME
    manifest = copied / "runtime-manifest.json"
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
    manifest = copied / "runtime-manifest.json"
    external = tmp_path / "external-manifest.json"
    external.write_bytes(manifest.read_bytes())
    manifest.unlink()
    try:
        manifest.symlink_to(external)
    except OSError:
        runner_text = (copied / "run_cortex.py").read_text("utf-8")
        assert "stat.S_ISLNK" in runner_text and "FILE_ATTRIBUTE_REPARSE_POINT" in runner_text
        return
    result = _runner(copied, "--version")
    assert result.returncode == 70 and "runtime_path_reparse" in result.stderr


def test_runtime_sc007_coordinated_wheel_manifest_tamper_fails_before_import(tmp_path: Path) -> None:
    copied = _copy_skill(tmp_path)
    wheel = copied / "vendor" / WHEEL_NAME
    marker = tmp_path / "tampered-wheel-imported"
    replacement = tmp_path / "replacement.whl"
    with zipfile.ZipFile(wheel, "r") as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename == "cortex/__init__.py":
                raw += f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n".encode("utf-8")
            target.writestr(info, raw)
    shutil.move(replacement, wheel)
    manifest = copied / "runtime-manifest.json"
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


def _packager_namespace() -> dict[str, object]:
    return runpy.run_path(str(ROOT / "tools" / "package_skill_runtime.py"), run_name="cortex_runtime_packager")


def _synthetic_runtime_root(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    root = tmp_path / "synthetic-root"
    expected = {
        "run_cortex.py": b"runner",
        "run_cortex.cmd": b"launcher",
        "runtime-manifest.json": b"manifest",
        f"vendor/{WHEEL_NAME}": b"wheel",
    }
    (root / "skills" / "cortex" / "scripts" / "kb" / "vendor").mkdir(parents=True)
    return root, expected


def test_runtime_sc008_install_keeps_one_batch_helper(tmp_path: Path) -> None:
    namespace = _packager_namespace()
    root, expected = _synthetic_runtime_root(tmp_path)
    namespace["_install_payload"](root, expected)  # type: ignore[operator]
    helper = namespace["_batch_helper_bytes"]()  # type: ignore[operator]
    assert (root / "skills/cortex/scripts/kb/batch_record_add.py").read_bytes() == helper


def test_runtime_sc008_unexpected_artifact_fails_before_any_generated_write(tmp_path: Path) -> None:
    namespace = _packager_namespace()
    root, expected = _synthetic_runtime_root(tmp_path)
    unexpected = root / "skills/cortex/scripts/kb/unexpected.bin"
    unexpected.write_bytes(b"unexpected")
    with pytest.raises(RuntimeError, match="unexpected KB runtime artifact"):
        namespace["_install_payload"](root, expected)  # type: ignore[operator]
    assert unexpected.read_bytes() == b"unexpected"
    assert not (root / "skills/cortex/scripts/kb/run_cortex.py").exists()


def test_runtime_sc009_wheel_metadata_has_no_dependencies_or_command() -> None:
    wheel = _skill("cortex-kb-ingest") / "vendor" / WHEEL_NAME
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = archive.read("cortex_record_kb-8.1.0.dist-info/METADATA").decode("utf-8")
    assert "Name: cortex-record-kb\n" in metadata and "Version: 8.1.0\n" in metadata
    assert "Requires-Dist:" not in metadata
    assert not any(name.endswith("entry_points.txt") for name in names)
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert "[project.scripts]" not in pyproject and 'cortex = "cortex.cli:main"' not in pyproject


def test_runtime_sc010_disposable_projection_has_no_launcher(tmp_path: Path) -> None:
    wheel = _skill("cortex-kb-manage") / "vendor" / WHEEL_NAME
    projection = tmp_path / "projection"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(projection)
    files = [path.relative_to(projection).as_posix() for path in projection.rglob("*") if path.is_file()]
    assert "cortex/__main__.py" in files
    assert not any(path.casefold().endswith(("cortex.exe", "cortex-script.py", "entry_points.txt")) for path in files)


def test_runtime_sc011_source_contract_is_unchanged() -> None:
    assert VERSION == "8.1.0"
    assert tuple(PUBLIC_ROUTES) == (
        "align.plan", "align.apply",
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
    workspaces = [tmp_path / "source", *(tmp_path / name for name in SKILL_NAMES)]
    common = ("--json", "--workspace")
    source = _source_cli(*common, str(workspaces[0]), "manage", "init")
    bundled = [
        _runner(_skill(name), *common, str(workspace), "manage", "init")
        for name, workspace in zip(SKILL_NAMES, workspaces[1:], strict=True)
    ]
    results = [source, *bundled]
    assert all(result.returncode == 0 and result.stderr == "" for result in results)
    assert [json.loads(result.stdout) for result in results] == [json.loads(source.stdout)] * len(results)
    assert [_tree(workspace) for workspace in workspaces] == [_tree(workspaces[0])] * len(workspaces)
    status_results = [
        _source_cli(*common, str(workspaces[0]), "manage", "status"),
        *(
            _runner(_skill(name), *common, str(workspace), "manage", "status")
            for name, workspace in zip(SKILL_NAMES, workspaces[1:], strict=True)
        ),
    ]
    assert [json.loads(result.stdout) for result in status_results] == [json.loads(status_results[0].stdout)] * len(status_results)


def test_runtime_sc013_surfaces_and_exact_mapping_agree() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    assert fixture["global_command"] is False
    assert fixture["agent_entrypoint"] == "CORTEX_PYTHON absolute-python-3.11-ucd14 -I skill-local-runner; ANTI_ENTROPY_CORE_RUNNER absolute-core-runner"
    assert fixture["skill_runtime"] == {
        "adapter": "skills/cortex/scripts/kb",
        "artifact": WHEEL_NAME,
        "offline": True,
        "required_environment": ["CORTEX_PYTHON", "ANTI_ENTROPY_CORE_RUNNER"],
        "human_stream_encoding": "utf-8",
        "json_ascii_escaping": True,
        "windows_launcher": "run_cortex.cmd",
    }
    assert fixture["batch_helper"] == {
        "role": "kb.ingest", "adapter": "skills/cortex/scripts/kb",
        "script": "batch_record_add.py", "schema_version": 1,
        "command": "record.add.batch", "core_route": False, "sequential": True, "rollback": False,
    }
    combined = "\n".join(
        (ROOT / relative).read_text("utf-8")
        for relative in (
            "AGENTS.md", "README.md", "docs/global-knowledge.md", "docs/record-kb-architecture.md",
            "skills/cortex/SKILL.md", "skills/cortex/references/kb-ingest.md",
            "skills/cortex/references/kb-build.md", "skills/cortex/references/kb-manage.md",
        )
    )
    for required in ("CORTEX_PYTHON", "-I", "skill-local", "8.1.0", "complete", "global", "UTF-8"):
        assert required in combined
    matrix = (ROOT / "docs" / "verification-matrix.md").read_text("utf-8")
    actual: dict[str, str] = {}
    for line in matrix.splitlines():
        if line.startswith("| runtime-sc"):
            cells = [cell.strip() for cell in line.split("|")]
            actual[cells[1]] = cells[2]
    assert actual == RUNTIME_SCENARIOS


@pytest.mark.parametrize(
    ("configured", "expected"),
    ((None, "cortex_python_required"), ("python", "non_absolute_path")),
)
def test_runtime_sc014_missing_or_relative_binding_fails_before_mutation(
    tmp_path: Path, configured: str | None, expected: str
) -> None:
    workspace = tmp_path / "must-not-exist"
    result = _runner(
        _skill("cortex-kb-ingest"),
        "--workspace", str(workspace), "manage", "init",
        cortex_python=configured,
    )
    assert result.returncode == 70 and expected in result.stderr and result.stdout == ""
    assert not workspace.exists()


def test_runtime_sc014_wrong_file_binding_fails_before_mutation(tmp_path: Path) -> None:
    wrong = tmp_path / Path(sys.executable).name
    shutil.copyfile(sys.executable, wrong)
    workspace = tmp_path / "must-not-exist"
    result = _runner(
        _skill("cortex-kb-manage"),
        "--workspace", str(workspace), "manage", "init",
        cortex_python=str(wrong.absolute()),
    )
    assert result.returncode == 70 and "cortex_python_mismatch" in result.stderr
    assert not workspace.exists()


def test_runtime_sc014_python312_fails_before_mutation_when_available(tmp_path: Path) -> None:
    candidates = [shutil.which("python3.12")]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(str(Path(local_app_data) / "Programs" / "Python" / "Python312" / "python.exe"))
    python312 = next((Path(value) for value in candidates if value and Path(value).is_file()), None)
    if python312 is None:
        pytest.skip("Python 3.12 is unavailable")
    workspace = tmp_path / "must-not-exist"
    env = dict(os.environ)
    env["CORTEX_PYTHON"] = str(python312.absolute())
    result = subprocess.run(
        [str(python312), "-I", str(_skill("cortex-kb-ingest") / "run_cortex.py"),
         "--workspace", str(workspace), "manage", "init"],
        cwd=_skill("cortex-kb-ingest"), env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 70 and "python_3_11_required" in result.stderr
    assert not workspace.exists()


def _configure_bundle_for_runtime(tmp_path: Path, *, env: dict[str, str] | None = None) -> Path:
    bundle = tmp_path / "bundle"
    assert _runner(
        _skill("cortex-kb-build"), "--json", "--workspace", str(bundle), "manage", "init", env=env,
    ).returncode == 0
    values = {
        "tags": {"version": 2, "groups": [
            {"name": "project", "tags": [{"tag": "project-alpha", "description": "Alpha"}]},
            {"name": "kind", "tags": [{"tag": "research", "description": "Research"}]},
        ]},
        "layout": {"version": 5, "partition_tag_group": "project", "partition_name_strategy": "tag",
                   "unit_name_strategy": "tag-title-date", "max_component_length": 96,
                   "duplicate_name_strategy": "reject"},
    }
    for name, value in values.items():
        operand = tmp_path / f"{name}.json"
        operand.write_text(json.dumps(value), encoding="utf-8")
        result = _runner(
            _skill("cortex-kb-build"), "--json", "--workspace", str(bundle), "manage", "config", "set",
            "--profile", name, "--file", str(operand), env=env,
        )
        assert result.returncode == 0, result.stdout
    return bundle


def _batch(skill: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["CORTEX_PYTHON"] = os.path.abspath(sys.executable)
    return subprocess.run(
        [sys.executable, "-I", str(skill / "batch_record_add.py"), *args],
        cwd=skill, env=env, input=stdin, capture_output=True, timeout=30, check=False,
    )


def _metadata(title: str) -> dict[str, object]:
    return {"title": title, "timestamp": "2026-08-22T00:00:00Z", "tags": ["project-alpha", "research"]}


def test_runtime_sc015_human_utf8_and_json_ascii_parity(tmp_path: Path) -> None:
    bundle = _configure_bundle_for_runtime(tmp_path)
    source = tmp_path / "unicode.md"
    source.write_bytes("# 漢字\n".encode("utf-8"))
    metadata = tmp_path / "unicode.json"
    metadata.write_text(json.dumps(_metadata("漢字 memo"), ensure_ascii=False), encoding="utf-8")
    added = _runner(
        _skill("cortex-kb-ingest"), "--json", "--workspace", str(bundle), "record", "add",
        "--source", str(source), "--metadata", str(metadata),
    )
    coordinates = json.loads(added.stdout)["data"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "ascii"
    env["CORTEX_PYTHON"] = os.path.abspath(sys.executable)
    command = [
        sys.executable, "-I", str(_skill("cortex-kb-ingest") / "run_cortex.py"),
        "--workspace", str(bundle), "record", "show", "--partition", coordinates["partition"],
        "--record", coordinates["record"],
    ]
    human = subprocess.run(command, cwd=_skill("cortex-kb-ingest"), env=env, capture_output=True, timeout=30, check=False)
    machine = subprocess.run(command[:3] + ["--json", *command[3:]], cwd=_skill("cortex-kb-ingest"), env=env,
                             capture_output=True, timeout=30, check=False)
    assert human.returncode == 0 and "漢字".encode("utf-8") in human.stdout and human.stderr == b""
    assert machine.returncode == 0 and machine.stderr == b"" and all(byte < 128 for byte in machine.stdout)
    assert b"\\u6f22\\u5b57" in machine.stdout
    assert set(json.loads(machine.stdout)) == {"status", "exit_code", "command", "data", "issues"}


@pytest.mark.parametrize("skill_name", BATCH_SKILL_NAMES)
def test_runtime_sc016_ingest_batch_mixes_full_and_markdown(tmp_path: Path, skill_name: str) -> None:
    helpers = [(_skill(name) / "batch_record_add.py") for name in BATCH_SKILL_NAMES]
    assert all(path.is_file() and not path.is_symlink() for path in helpers)
    assert all(path.read_bytes() == helpers[0].read_bytes() for path in helpers)
    assert all(_skill(name) == _skill("cortex-kb-ingest") for name in NON_BATCH_SKILL_NAMES)
    bundle = _configure_bundle_for_runtime(tmp_path)
    registry = tmp_path / "registry-input.json"
    registry.write_text(json.dumps({
        "version": 1,
        "bundles": [{"id": "alpha", "path": bundle.name, "description": "Alpha"}],
    }), encoding="utf-8")
    registered = _runner(
        _skill("cortex-kb-build"), "--json", "--kb-root", str(tmp_path), "registry", "set",
        "--file", str(registry),
    )
    assert registered.returncode == 0, registered.stdout
    markdown = tmp_path / "plain.md"
    markdown.write_bytes(b"# plain\n")
    source = tmp_path / "full.pdf"
    source.write_bytes(b"pdf-source")
    conversion = tmp_path / "conversion"
    (conversion / "src").mkdir(parents=True)
    (conversion / "full.md").write_bytes(b"# full\n")
    (conversion / "full.json").write_bytes(b"{}\n")
    (conversion / "src" / "full.pdf").write_bytes(source.read_bytes())
    job = {"version": 1, "items": [
        {"id": "full", "source": str(source), "conversion": str(conversion), "metadata": _metadata("Full item")},
        {"id": "markdown", "source": str(markdown), "metadata": _metadata("Markdown item")},
    ]}
    result = _batch(_skill(skill_name), "--kb-root", str(tmp_path), "--bundle-id", "alpha", "--job", "-",
                    stdin=json.dumps(job).encode("utf-8"))
    wrapper = json.loads(result.stdout)
    assert result.returncode == 0 and result.stderr == b""
    assert wrapper["command"] == "record.add.batch" and wrapper["schema_version"] == 1
    assert [item["id"] for item in wrapper["items"]] == ["full", "markdown"]
    assert wrapper["summary"] == {"total": 2, "succeeded": 2, "failed": 0}


def test_sc021_batch_v2_accepts_source_conversion_and_both(tmp_path: Path) -> None:
    bundle = _configure_bundle_for_runtime(tmp_path)
    source_only = tmp_path / "source-only.bin"
    source_only.write_bytes(b"source-only")
    conversion_only = tmp_path / "conversion-only"
    conversion_only.mkdir()
    (conversion_only / "converted.md").write_bytes(b"converted")
    retained = tmp_path / "retained.pdf"
    retained.write_bytes(b"retained")
    combined = tmp_path / "combined"
    combined.mkdir()
    (combined / "retained.md").write_bytes(b"combined")
    job = {"version": 2, "items": [
        {"id": "source", "source": str(source_only), "metadata": _metadata("Source v2")},
        {"id": "conversion", "conversion": str(conversion_only), "metadata": _metadata("Conversion v2")},
        {
            "id": "both", "source": str(retained), "conversion": str(combined),
            "metadata": _metadata("Both v2"),
        },
    ]}
    result = _batch(
        _skill("cortex-kb-ingest"), "--workspace", str(bundle), "--job", "-",
        stdin=json.dumps(job).encode("utf-8"),
    )
    wrapper = json.loads(result.stdout)
    assert result.returncode == 0 and result.stderr == b""
    assert wrapper["summary"] == {"total": 3, "succeeded": 3, "failed": 0}
    records = [bundle / item["result"]["data"]["partition"] / item["result"]["data"]["record"] for item in wrapper["items"]]
    assert (records[0] / "source-only.bin").read_bytes() == source_only.read_bytes()
    assert (records[1] / "src" / ".keep").read_bytes() == b""
    assert (records[2] / "src" / "retained.pdf").read_bytes() == retained.read_bytes()


def test_sc021_batch_v2_syntax_failure_is_whole_job_zero_write(tmp_path: Path) -> None:
    workspace = tmp_path / "must-not-exist"
    valid_source = tmp_path / "valid.md"
    valid_source.write_bytes(b"valid")
    job = {"version": 2, "items": [
        {"id": "valid", "source": str(valid_source), "metadata": _metadata("Valid")},
        {"id": "invalid", "metadata": _metadata("Missing input")},
    ]}
    result = _batch(
        _skill("cortex-kb-ingest"), "--workspace", str(workspace), "--job", "-",
        stdin=json.dumps(job).encode("utf-8"),
    )
    assert result.returncode == 2 and result.stdout == b""
    assert b"job_item_shape_invalid" in result.stderr
    assert not workspace.exists()


@pytest.mark.parametrize("skill_name", BATCH_SKILL_NAMES)
def test_runtime_sc017_middle_failure_continues(tmp_path: Path, skill_name: str) -> None:
    bundle = _configure_bundle_for_runtime(tmp_path)
    first = tmp_path / "first.md"; first.write_bytes(b"first")
    duplicate = tmp_path / "duplicate.pdf"; duplicate.write_bytes(b"duplicate")
    last = tmp_path / "last.md"; last.write_bytes(b"last")
    job = {"version": 1, "items": [
        {"id": "first", "source": str(first), "metadata": _metadata("First")},
        {"id": "duplicate", "source": str(duplicate), "metadata": _metadata("First")},
        {"id": "last", "source": str(last), "metadata": _metadata("Last")},
    ]}
    result = _batch(_skill(skill_name), "--workspace", str(bundle), "--job", "-",
                    stdin=json.dumps(job).encode("utf-8"))
    wrapper = json.loads(result.stdout)
    assert result.returncode == 1 and result.stderr == b""
    assert [item["result"]["status"] for item in wrapper["items"]] == ["ok", "validation_error", "ok"]
    assert wrapper["summary"] == {"total": 3, "succeeded": 2, "failed": 1}
    assert any(path.name.startswith("project-alpha-last-") for path in (bundle / "project-alpha").iterdir())


@pytest.mark.parametrize("skill_name", BATCH_SKILL_NAMES)
@pytest.mark.parametrize("case", ("malformed", "duplicate", "relative"))
def test_runtime_sc018_invalid_job_rejects_before_runner_or_mutation(
    tmp_path: Path, case: str, skill_name: str
) -> None:
    workspace = tmp_path / "must-not-exist"
    if case == "malformed":
        raw = b'{"version":1,"items":['
    elif case == "duplicate":
        item = {"id": "same", "source": str((tmp_path / "a.md").absolute()), "metadata": _metadata("A")}
        raw = json.dumps({"version": 1, "items": [item, item]}).encode("utf-8")
    else:
        raw = json.dumps({"version": 1, "items": [
            {"id": "relative", "source": "relative.md", "metadata": _metadata("Relative")},
        ]}).encode("utf-8")
    result = _batch(_skill(skill_name), "--workspace", str(workspace), "--job", "-", stdin=raw)
    assert result.returncode == 2 and result.stdout == b"" and b"cortex record batch error:" in result.stderr
    assert not workspace.exists()


def test_runtime_sc019_populated_tag_expansion_preserves_layout_and_defers_partition(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["ANTI_ENTROPY_CORE_RUNNER"] = str(
        (ROOT.parent / "anti-entropy-core" / "scripts" / "knowledge_unit_runner.py").resolve()
    )
    bundle = _configure_bundle_for_runtime(tmp_path, env=env)
    first_source = tmp_path / "first.md"
    first_source.write_bytes(b"# first\n")
    first_metadata = tmp_path / "first.json"
    first_metadata.write_text(json.dumps(_metadata("First")), encoding="utf-8")
    first = _runner(
        _skill("cortex-kb-ingest"), "--json", "--workspace", str(bundle), "record", "add",
        "--source", str(first_source), "--metadata", str(first_metadata), env=env,
    )
    assert first.returncode == 0, first.stdout

    layout_before = (bundle / "profiles" / "layout.json").read_bytes()
    tags = json.loads((bundle / "profiles" / "tags.json").read_text("utf-8"))
    tags["groups"][0]["tags"].append({"tag": "project-lighthouse", "description": "Lighthouse"})
    candidate = tmp_path / "expanded-tags.json"
    candidate.write_text(json.dumps(tags), encoding="utf-8")
    expanded = _runner(
        _skill("cortex-kb-build"), "--json", "--workspace", str(bundle), "manage", "config", "set",
        "--profile", "tags", "--file", str(candidate), env=env,
    )
    assert expanded.returncode == 0, expanded.stdout
    assert (bundle / "profiles" / "layout.json").read_bytes() == layout_before
    assert not (bundle / "project-lighthouse").exists()
    one = _runner(
        _skill("cortex-kb-manage"), "--json", "--workspace", str(bundle), "manage", "validate", env=env,
    )
    assert json.loads(one.stdout)["data"] == {"version": "8.1.0", "valid": True, "count": 1}

    second_source = tmp_path / "second.md"
    second_source.write_bytes(b"# second\n")
    second_metadata = tmp_path / "second.json"
    second_metadata.write_text(json.dumps({
        "title": "Lighthouse", "timestamp": "2026-08-23T00:00:00Z",
        "tags": ["project-lighthouse", "research"],
    }), encoding="utf-8")
    second = _runner(
        _skill("cortex-kb-ingest"), "--json", "--workspace", str(bundle), "record", "add",
        "--source", str(second_source), "--metadata", str(second_metadata), env=env,
    )
    assert second.returncode == 0, second.stdout
    assert json.loads(second.stdout)["data"]["partition"] == "project-lighthouse"
    two = _runner(
        _skill("cortex-kb-manage"), "--json", "--workspace", str(bundle), "manage", "validate", env=env,
    )
    assert json.loads(two.stdout)["data"] == {"version": "8.1.0", "valid": True, "count": 2}
