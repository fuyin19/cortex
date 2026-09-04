"""Real installed-launcher acceptance for the Core 1.2.1 carrier binding."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[1]
ABI = "anti-entropy-core.runner/v1"
CORE_VERSION = "1.2.1"


def _core(destination: Path, label: str, log: Path, mode: str = "normal") -> Path:
    """Copy the actual Candidate and trace transport without replacing Core semantics."""
    source = Path(os.environ["CORTEX_REAL_CORE_RUNNER"])
    assert source.parent.name == "scripts" and (source.parent.parent / "SKILL.md").is_file()
    shutil.copytree(source.parent.parent, destination, ignore=shutil.ignore_patterns("__pycache__"))
    runner = destination / "scripts" / "knowledge_unit_runner.py"
    original = runner.with_name("core_implementation.py")
    runner.rename(original)
    runner.write_text(
        "import json, pathlib, subprocess, sys\n"
        f"log=pathlib.Path({str(log)!r}); label={label!r}; mode={mode!r}\n"
        "for raw in sys.stdin.buffer:\n"
        "    frame=json.loads(raw)\n"
        "    with log.open('a', encoding='utf-8') as stream: stream.write(json.dumps({'runner':label,'command':frame['command']})+'\\n')\n"
        "    done=subprocess.run([sys.executable,'-I','-S',str(pathlib.Path(__file__).with_name('core_implementation.py'))],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)\n"
        "    if done.returncode: sys.stderr.buffer.write(done.stderr); raise SystemExit(done.returncode)\n"
        "    value=json.loads(done.stdout)\n"
        "    if frame['command']=='capabilities':\n"
        "        if mode=='old': value['data']['version']='1.2.0'\n"
        "        if mode=='new': value['data']['version']='1.2.2'\n"
        "        if mode=='missing': value['data'].pop('version',None)\n"
        "        if mode=='invalid': value['data']['version']=123\n"
        "        if mode=='abi': value['abi']='wrong-abi'\n"
        "        if mode=='extra': value['extra']=True\n"
        "        if mode=='status': value['exit_code']=3\n"
        "        if mode=='json': sys.stdout.write('not-json\\n'); sys.stdout.flush(); continue\n"
        "    sys.stdout.write(json.dumps(value,separators=(',',':'))+'\\n'); sys.stdout.flush()\n",
        encoding="utf-8",
    )
    return runner


def _installed(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    initial = tmp_path / "original" / "skills"
    skill = initial / "cortex"
    shutil.copytree(ROOT / "skills" / "cortex", skill)
    log = tmp_path / "calls.jsonl"
    _core(initial / "anti-entropy-core", "default", log)
    relocated = tmp_path / "搬迁 installed skills"
    initial.rename(relocated)
    skill = relocated / "cortex"
    runner = relocated / "anti-entropy-core" / "scripts" / "knowledge_unit_runner.py"
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    hostile = unrelated / "anti_entropy_core.py"
    hostile.write_text("raise AssertionError('ambient Core import forbidden')\n", encoding="utf-8")
    # A different plausible skill root cannot influence deterministic binding.
    _core(unrelated / "anti-entropy-core", "distractor", log)
    env = dict(os.environ)
    env.pop("ANTI_ENTROPY_CORE_RUNNER", None)
    env["CORTEX_PYTHON"] = os.path.abspath(sys.executable)
    env["PYTHONPATH"] = str(unrelated)
    env["PATH"] = str(unrelated)
    env["BINDING_TEST_CWD"] = str(unrelated)
    return skill, runner, log, env


def _run(skill: Path, role: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    names = {"kb": "run_cortex.py", "collaborative-workspace": "run_collaborative_workspace.py", "notes": "run_notes.py"}
    return subprocess.run(
        [sys.executable, "-I", str(skill / "scripts" / role / names[role]), *args],
        env=env, cwd=env["BINDING_TEST_CWD"], capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
    )


def _calls(log: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []


def _tree(path: Path) -> dict[str, bytes | None]:
    return {p.relative_to(path).as_posix(): None if p.is_dir() else p.read_bytes()
            for p in path.rglob("*")} if path.exists() else {}


def _business(skill: Path, role: str, env: dict[str, str], root: Path) -> subprocess.CompletedProcess[str]:
    if role == "kb":
        if not root.exists():
            initialized = _run(skill, role, env, "--json", "--workspace", str(root), "manage", "init")
            assert initialized.returncode == 0, initialized.stdout + initialized.stderr
        candidate = root.parent / "tags-candidate.json"
        candidate.write_text(json.dumps({"version": 2, "groups": [{"name": "project", "tags": [{"tag": "alpha", "description": "alpha"}]}]}), encoding="utf-8")
        return _run(skill, role, env, "--json", "--workspace", str(root), "manage", "config", "set", "--profile", "tags", "--file", str(candidate))
    return _run(skill, role, env, "--json", "prepare", "--root", str(root))


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc002_relocated_real_installed_launcher_uses_exact_sibling(tmp_path: Path, role: str) -> None:
    skill, _runner, log, env = _installed(tmp_path)
    result = _business(skill, role, env, tmp_path / "business")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(log)
    assert calls and {item["runner"] for item in calls} == {"default"}
    assert calls[0]["command"] == "capabilities"
    assert sum(item["command"] == "capabilities" for item in calls) == 1


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc003_valid_explicit_override_never_calls_default(tmp_path: Path, role: str) -> None:
    skill, _runner, log, env = _installed(tmp_path)
    override = _core(tmp_path / "other root" / "anti-entropy-core", "override", log)
    env["ANTI_ENTROPY_CORE_RUNNER"] = str(override)
    result = _business(skill, role, env, tmp_path / "business")
    assert result.returncode == 0, result.stdout + result.stderr
    assert {item["runner"] for item in _calls(log)} == {"override"}


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
@pytest.mark.parametrize("kind", ["empty", "relative", "missing", "directory", "default-missing", "default-directory", "default-marker"])
def test_sc004_invalid_path_fails_before_business_writes(tmp_path: Path, role: str, kind: str) -> None:
    skill, runner, log, env = _installed(tmp_path)
    root = tmp_path / "business"
    if role == "kb":
        assert _run(skill, role, env, "--json", "--workspace", str(root), "manage", "init").returncode == 0
    before = _tree(root)
    if kind == "empty": env["ANTI_ENTROPY_CORE_RUNNER"] = ""
    elif kind == "relative": env["ANTI_ENTROPY_CORE_RUNNER"] = "relative.py"
    elif kind == "missing": env["ANTI_ENTROPY_CORE_RUNNER"] = str(tmp_path / "absent.py")
    elif kind == "directory": env["ANTI_ENTROPY_CORE_RUNNER"] = str(tmp_path)
    elif kind == "default-missing": runner.unlink()
    elif kind == "default-directory": runner.unlink(); runner.mkdir()
    elif kind == "default-marker": (runner.parent.parent / "SKILL.md").unlink()
    result = _business(skill, role, env, root)
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["issues"][0]["code"] in {"core_runner_not_absolute", "core_runner_path_invalid"}
    assert "1.2.1" in result.stdout and "anti-entropy-core.runner/v1" in result.stdout
    assert _tree(root) == before and not _calls(log)
    assert not list(tmp_path.glob(".cortex-collaborative-workspace-*"))


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
@pytest.mark.parametrize("mode", ["old", "new", "missing", "invalid", "abi", "extra", "status", "json"])
def test_sc005_sc007_invalid_version_or_result_fails_before_writes(tmp_path: Path, role: str, mode: str) -> None:
    skill, _runner, log, env = _installed(tmp_path)
    override = _core(tmp_path / "bad core", "invalid", log, mode)
    env["ANTI_ENTROPY_CORE_RUNNER"] = str(override)
    root = tmp_path / "business"
    if role == "kb":
        assert _run(skill, role, env, "--json", "--workspace", str(root), "manage", "init").returncode == 0
    before = _tree(root)
    result = _business(skill, role, env, root)
    version_failure = mode in {"old", "new", "missing", "invalid", "abi"}
    assert result.returncode == (2 if version_failure else 6), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["issues"][0]["code"] == ("core_version_mismatch" if version_failure else "core_protocol_error")
    if version_failure:
        assert "expected_version" in result.stdout and "actual_version" in result.stdout
        assert str(override) in json.dumps(payload, ensure_ascii=False).replace("\\\\", "\\")
    assert _tree(root) == before
    assert _calls(log) == [{"runner": "invalid", "command": "capabilities"}]


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc004_installed_parent_reparse_is_rejected(tmp_path: Path, role: str) -> None:
    skill, runner, log, env = _installed(tmp_path)
    scripts = runner.parent
    saved = scripts.with_name("scripts-real")
    scripts.rename(saved)
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        made = subprocess.run(["powershell", "-NoProfile", "-Command",
            "New-Item -ItemType Junction -Path " + quote(scripts) + " -Target " + quote(saved) + " | Out-Null"],
            capture_output=True, timeout=30, check=False)
        assert made.returncode == 0, made.stderr
    else:
        scripts.symlink_to(saved, target_is_directory=True)
    try:
        root = tmp_path / "business"
        result = _business(skill, role, env, root)
        assert result.returncode == 2, result.stdout + result.stderr
        assert json.loads(result.stdout)["issues"][0]["code"] == "core_runner_path_invalid"
        assert not _calls(log)
    finally:
        # Removing the directory link never traverses its target.
        if os.name == "nt": scripts.rmdir()
        else: scripts.unlink()


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc006_binding_is_fixed_and_next_operation_rebinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    from cortex.core_runner import CoreRunner as KBRunner
    from cortex_collaborative_workspace.core_runner import CoreRunner as WorkspaceRunner
    log = tmp_path / "calls.jsonl"
    first = _core(tmp_path / "first", "first", log)
    second = _core(tmp_path / "second", "second", log)
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", str(first))
    binding = KBRunner.from_config() if role == "kb" else WorkspaceRunner()
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", str(second))
    stage = tmp_path / "stage"
    stage.mkdir(); (stage / "memo.md").write_text("# test\n", encoding="utf-8")
    if role == "kb":
        binding.stage_complete(stage); binding.validate(stage)
    else:
        binding.knowledge_unit_stage_complete(stage); binding.knowledge_unit_validate(stage)
    assert {item["runner"] for item in _calls(log)} == {"first"}
    assert sum(item["command"] == "capabilities" for item in _calls(log)) == 1
    next_binding = KBRunner.from_config() if role == "kb" else WorkspaceRunner()
    assert next_binding.path == second and _calls(log)[-1] == {"runner": "second", "command": "capabilities"}


def test_sc006_actual_cw_operation_passes_fixed_runner_to_real_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill, first, log, env = _installed(tmp_path)
    second = _core(tmp_path / "second", "second", log)
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import argparse,json,os,pathlib,shutil,subprocess,sys\n"
        "if '--runtime-preflight-json' in sys.argv:\n"
        "    print(json.dumps({'schema_version':1,'status':'ok','scope':'ready','code':'runtime_ready'},sort_keys=True,separators=(',',':'))); raise SystemExit(0)\n"
        "p=argparse.ArgumentParser(); p.add_argument('--config'); p.add_argument('--input'); p.add_argument('--output-dir'); p.add_argument('--bundle-name-mode'); a=p.parse_args()\n"
        f"assert os.environ['ANTI_ENTROPY_CORE_RUNNER']=={str(first)!r}\n"
        "source=pathlib.Path(a.input); unit=pathlib.Path(a.output_dir)/source.name; unit.mkdir()\n"
        "(unit/(source.name+'.md')).write_text('# converted\\n'); (unit/(source.name+'.json')).write_text(json.dumps({'quality':{'status':'complete','warnings':[]}}))\n"
        "(unit/'src').mkdir(); shutil.copyfile(source,unit/'src'/source.name); (unit/'assets').mkdir()\n"
        "for command,request in [('capabilities',{}),('stage.complete',{'path':str(unit),'private_root_files':[]})]:\n"
        "    wire=(json.dumps({'command':command,'request':request})+'\\n').encode()\n"
        "    done=subprocess.run([sys.executable,'-I',os.environ['ANTI_ENTROPY_CORE_RUNNER']],input=wire,stdout=subprocess.PIPE,timeout=30)\n"
        "    value=json.loads(done.stdout); assert done.returncode==0 and value['status']=='ok'\n"
        "    if command=='capabilities': assert value['abi']=='anti-entropy-core.runner/v1' and value['data']['version']=='1.2.1'\n",
        encoding="utf-8",
    )
    config = tmp_path / "provider-config.json"; config.write_text("{}", encoding="utf-8")
    for route in ("FILE", "MARKDOWN"):
        env[route + "_CONVERSION_RUNNER"] = str(provider)
        env[route + "_CONVERSION_CONFIG"] = str(config)
    root = tmp_path / "workspace"; (root / "ref").mkdir(parents=True)
    (root / "ref" / "memo.txt").write_text("text", encoding="utf-8")
    (root / "ref" / "report.pdf").write_bytes(b"synthetic provider fixture")
    launcher = skill / "scripts" / "collaborative-workspace" / "run_collaborative_workspace.py"
    # Load the real installed launcher. The harness changes only ambient configuration
    # before provider startup, leaving binding, preflight, Core calls and child execution intact.
    harness = tmp_path / "launch.py"
    harness.write_text(
        "import importlib,os,runpy,sys\n"
        "original_import=importlib.import_module\n"
        "def load(name,*args,**kwargs):\n"
        "    module=original_import(name,*args,**kwargs)\n"
        "    if name=='cortex_collaborative_workspace.cli':\n"
        "        workspace=sys.modules['cortex_collaborative_workspace.workspace']; original=workspace._provider_binding\n"
        "        def bind(route,suffixes=()):\n"
        f"            os.environ['ANTI_ENTROPY_CORE_RUNNER']={str(second)!r}\n"
        "            return original(route,suffixes)\n"
        "        workspace._provider_binding=bind\n"
        "    return module\n"
        "importlib.import_module=load\n"
        f"sys.argv=[{str(launcher)!r},'--json','prepare','--root',{str(root)!r}]\n"
        f"loaded=runpy.run_path({str(launcher)!r})\n"
        "raise SystemExit(loaded['main']())\n",
        encoding="utf-8",
    )
    completed = subprocess.run([sys.executable, "-I", str(harness)], env=env,
        cwd=env["BINDING_TEST_CWD"], capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "ok" and result["data"]["source_items"] == 2
    assert {item["runner"] for item in _calls(log)} == {"default"}
    assert sum(item["command"] == "capabilities" for item in _calls(log)) == 3  # parent and each child
    env["ANTI_ENTROPY_CORE_RUNNER"] = str(second)
    status = _run(skill, "collaborative-workspace", env, "--json", "status", "--root", str(root))
    assert status.returncode == 0, status.stdout + status.stderr
    assert _calls(log)[-1]["runner"] == "second"


@pytest.mark.parametrize("configured", ["absent", "broken"])
def test_sc009_actual_notes_business_and_core_free_entries(tmp_path: Path, configured: str) -> None:
    skill, runner, log, env = _installed(tmp_path)
    # Even a discoverable Core that fails on execution must be irrelevant to Notes/init/help.
    runner.write_text("raise AssertionError('Core must not execute')\n", encoding="utf-8")
    if configured == "broken": env["ANTI_ENTROPY_CORE_RUNNER"] = str(tmp_path / "missing.py")
    else: env.pop("ANTI_ENTROPY_CORE_RUNNER", None); runner.unlink()
    for role in ("kb", "collaborative-workspace", "notes"):
        assert _run(skill, role, env, "--version").returncode == 0
        assert _run(skill, role, env, "--help").returncode == 0
    kb = tmp_path / "kb"
    assert _run(skill, "kb", env, "--json", "--workspace", str(kb), "manage", "init").returncode == 0
    notes = tmp_path / "notes"
    base = ("--json", "--root", str(notes))
    for args in (("registry", "init"), ("bundle", "init", "--bundle", "daily-notes")):
        result = _run(skill, "notes", env, *base, *args)
        assert result.returncode == 0, result.stdout + result.stderr
    body = tmp_path / "body.md"; body.write_text("# note\n", encoding="utf-8")
    added = _run(skill, "notes", env, *base, "note", "add", "--bundle", "daily-notes", "--title", "Entry", "--body-file", str(body), "--timestamp", "2026-09-03T08:00:00.000000+08:00")
    assert added.returncode == 0, added.stdout + added.stderr
    assert list(notes.rglob("note.json")) and not _calls(log)


@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc005_preflight_has_bounded_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    from cortex import core_runner as kb
    from cortex_collaborative_workspace import core_runner as cw
    module = kb if role == "kb" else cw
    runner = tmp_path / "runner.py"; runner.write_text("", encoding="utf-8")
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 30
        assert command[:2] == [sys.executable, "-I"]
        assert json.loads(kwargs["input"])["command"] == "capabilities"
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(Exception) as captured:
        module.CoreRunner(str(runner))
    assert captured.value.code == "core_runner_start_failed"

@pytest.mark.parametrize("role", ["kb", "collaborative-workspace"])
def test_sc004_damaged_nearest_consumer_boundary_never_uses_outer_cortex(tmp_path: Path, role: str) -> None:
    skill, _runner, log, env = _installed(tmp_path)
    outer = tmp_path / "cortex"; outer.mkdir()
    (outer / "SKILL.md").write_text("outer boundary", encoding="utf-8")
    relocated = outer / "nested skills"
    skill.parent.rename(relocated)
    skill = relocated / "cortex"
    (skill / "SKILL.md").unlink()
    _core(tmp_path / "anti-entropy-core", "outer", log)
    result = _business(skill, role, env, tmp_path / "business")
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["issues"][0]["code"] == "core_runner_required"
    assert not _calls(log)
