from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import stat

import pytest

from cortex_collaborative_workspace import workspace
from cortex_collaborative_workspace.core_runner import CoreRunner


ROOT = Path(__file__).parents[1]
CORE_RUNNER = Path(os.environ["CORTEX_REAL_CORE_RUNNER"])
REAL_PREFLIGHT_BINDINGS = workspace._preflight_provider_bindings


@pytest.fixture(autouse=True)
def explicit_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", str(CORE_RUNNER.resolve()))
    monkeypatch.setenv("FILE_CONVERSION_RUNNER", str(CORE_RUNNER.resolve()))
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(CORE_RUNNER.resolve()))
    monkeypatch.setattr(workspace, "_preflight_provider_bindings", lambda _bindings: None)


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text("utf-8"))


def _tree(root: Path) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        result[path.relative_to(root).as_posix()] = None if path.is_dir() else path.read_bytes()
    return result


def _fake_providers(monkeypatch: pytest.MonkeyPatch, *, warning: bool = False) -> list[Path]:
    observed: list[Path] = []

    def convert(route: str, snapshot: Path, output_parent: Path, expected_unit: Path, core_runner: Path, binding=None):
        assert route in {"file-conversion", "markdown-conversion"}
        assert "agent-workbench" not in snapshot.parts
        assert snapshot.name == expected_unit.name
        observed.append(snapshot)
        expected_unit.mkdir()
        basename = snapshot.name
        (expected_unit / f"{basename}.md").write_bytes(b"# converted\n")
        warnings = [{"code": "fixture_warning", "message": "fixture"}] if warning else []
        metadata = {"quality": {"status": "complete_with_warnings" if warning else "complete", "warnings": warnings}}
        (expected_unit / f"{basename}.json").write_bytes(
            (json.dumps(metadata, separators=(",", ":")) + "\n").encode("utf-8")
        )
        (expected_unit / "src").mkdir()
        (expected_unit / "src" / basename).write_bytes(snapshot.read_bytes())
        (expected_unit / "assets").mkdir()
        CoreRunner(str(core_runner)).knowledge_unit_stage_complete(expected_unit)
        return ("ready_with_warnings", ["fixture_warning"]) if warning else ("ready", [])

    monkeypatch.setattr(workspace, "_run_provider", convert)
    return observed


def _failing_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr: bytes,
    stdout: bytes = b"",
    exit_code: int = 23,
) -> None:
    provider = tmp_path / "failing-provider.py"
    provider.write_text(
        """from __future__ import annotations
import argparse, json, pathlib, sys
p=argparse.ArgumentParser(); p.add_argument('--config', required=True); p.add_argument('--input', required=True); p.add_argument('--output-dir', required=True); p.add_argument('--bundle-name-mode', required=True); a=p.parse_args()
payload=json.loads(pathlib.Path(a.config).read_text(encoding='utf-8'))
sys.stdout.buffer.write(bytes.fromhex(payload['stdout']))
sys.stderr.buffer.write(bytes.fromhex(payload['stderr']))
raise SystemExit(payload['exit_code'])
""",
        encoding="utf-8",
    )
    config = tmp_path / "failing-provider.json"
    config.write_text(
        json.dumps({"stderr": stderr.hex(), "stdout": stdout.hex(), "exit_code": exit_code}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.setenv("MARKDOWN_CONVERSION_CONFIG", str(config.resolve()))
    monkeypatch.setenv("FILE_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.setenv("FILE_CONVERSION_CONFIG", str(config.resolve()))


def _provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr: bytes,
    stdout: bytes = b"",
) -> workspace.WorkspaceError:
    _failing_provider(tmp_path, monkeypatch, stderr=stderr, stdout=stdout)
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_bytes(b"source")
    output = tmp_path / "candidate"
    output.mkdir()
    with pytest.raises(workspace.WorkspaceError, match="provider_conversion_failed") as captured:
        workspace._run_provider(
            "markdown-conversion", snapshot, output, output / snapshot.name, CORE_RUNNER,
        )
    return captured.value


def _configless_provider(tmp_path: Path) -> Path:
    provider = tmp_path / "configless-provider.py"
    provider.write_text(
        """from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys
assert '--config' not in sys.argv
p=argparse.ArgumentParser(); p.add_argument('--input', required=True); p.add_argument('--output-dir', required=True); p.add_argument('--bundle-name-mode', required=True); a=p.parse_args()
assert a.bundle_name_mode == 'source-basename'
source=pathlib.Path(a.input); unit=pathlib.Path(a.output_dir)/source.name; unit.mkdir()
(unit/(source.name+'.md')).write_text('# converted\\n', encoding='utf-8')
(unit/(source.name+'.json')).write_text(json.dumps({'quality':{'status':'complete','warnings':[]}}), encoding='utf-8')
(unit/'src').mkdir(); shutil.copyfile(source, unit/'src'/source.name); (unit/'assets').mkdir()
wire=(json.dumps({'command':'stage.complete','request':{'path':str(unit.resolve()),'private_root_files':[]}},separators=(',',':'))+'\\n').encode()
done=subprocess.run([sys.executable,'-I',os.environ['ANTI_ENTROPY_CORE_RUNNER']],input=wire,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
if done.returncode == 0 and os.environ.get('SYNTHETIC_PROVIDER_LOG'):
    with open(os.environ['SYNTHETIC_PROVIDER_LOG'],'a',encoding='utf-8') as stream: stream.write(source.name+'\\n')
raise SystemExit(done.returncode)
""",
        encoding="utf-8",
    )
    return provider


@pytest.mark.parametrize("count", [1, 1000])
def test_provider_readiness_runs_once_per_binding_with_suffix_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    provider = tmp_path / "preflight-provider.py"
    provider.write_text(
        """import json, os, pathlib, sys
pathlib.Path(os.environ['PREFLIGHT_LOG']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
print(json.dumps({'schema_version':1,'status':'ok','scope':'ready','code':'runtime_ready'}, sort_keys=True, separators=(',',':')))
""",
        encoding="utf-8",
    )
    log = tmp_path / "preflight.json"
    monkeypatch.setenv("PREFLIGHT_LOG", str(log))
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(provider.resolve()))
    items = [
        workspace.SourceItem(f"item-{index}.txt", "file", "0" * 64, tmp_path / f"item-{index}.txt")
        for index in range(count)
    ]
    bindings = workspace._provider_bindings(items)
    REAL_PREFLIGHT_BINDINGS(bindings)
    argv = json.loads(log.read_text("utf-8"))
    assert argv.count("--runtime-preflight-json") == 1
    assert argv.count("--required-suffix") == 1
    assert argv[-1] == ".txt"


def test_zero_readiness_for_create_empty_adoption_ku_noop_status_and_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(workspace, "_preflight_provider_bindings", REAL_PREFLIGHT_BINDINGS)
    monkeypatch.setattr(workspace, "_provider_preflight", lambda binding: calls.append(binding.route))

    created = tmp_path / "created"
    workspace.prepare(created)
    workspace.status(created)
    workspace.validate(created)
    empty = tmp_path / "empty"
    empty.mkdir()
    workspace.prepare(empty)
    ku_root = tmp_path / "ku-root"
    unit = ku_root / "ref" / "manual"
    unit.mkdir(parents=True)
    (unit / "manual.md").write_bytes(b"manual")
    CoreRunner().knowledge_unit_stage_complete(unit)
    workspace.prepare(ku_root)
    assert calls == []

    routed = tmp_path / "routed"
    (routed / "ref").mkdir(parents=True)
    (routed / "ref" / "memo.txt").write_bytes(b"memo")
    _fake_providers(monkeypatch)
    workspace.prepare(routed)
    calls.clear()
    monkeypatch.setattr(workspace, "_preflight_provider_bindings", REAL_PREFLIGHT_BINDINGS)
    assert workspace.prepare(routed)["data"]["action"] == "no_op"
    workspace.status(routed)
    workspace.validate(routed)
    assert calls == []


def test_python_environment_fallback_happens_before_stage_or_workspace_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "memo.txt").write_bytes(b"memo")
    before = _tree(root)
    provider = tmp_path / "missing-dependency-provider.py"
    provider.write_text(
        "import json\n"
        "print(json.dumps({'schema_version':1,'status':'error','scope':'python_environment','code':'conversion_python_dependency_unavailable'},sort_keys=True,separators=(',',':')))\n"
        "raise SystemExit(75)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.setenv("CORTEX_RUNTIME_FALLBACK", "1")
    monkeypatch.setattr(workspace, "_preflight_provider_bindings", REAL_PREFLIGHT_BINDINGS)
    stage_called = False

    def forbidden_stage(_root: Path):
        nonlocal stage_called
        stage_called = True
        raise AssertionError("stage must not be created before runtime readiness")

    monkeypatch.setattr(workspace, "_new_stage", forbidden_stage)
    with pytest.raises(workspace.RuntimeFallback):
        workspace.prepare(root)
    assert not stage_called and _tree(root) == before
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_create_is_complete_and_read_only_status_validate(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    created = workspace.prepare(root)
    assert created["data"] == {
        "action": "created", "state": "ready", "workspace_id": created["data"]["workspace_id"],
        "generation": 1, "source_items": 0,
    }
    assert set(path.name for path in root.iterdir()) == {
        "AGENTS.md", "CLAUDE.md", "collaborative-workspace.json", "ref", "agent-workbench",
    }
    assert set(path.name for path in (root / "ref").iterdir()) == {"_outdated"}
    assert (root / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n"
    assert set(path.name for path in (root / "agent-workbench").iterdir()) == {
        "AGENTS.md", "CLAUDE.md", "ref", "temp", "output",
    }
    assert set(path.name for path in (root / "agent-workbench" / "ref").iterdir()) == {
        ".agent-workbench.json", "_outdated",
    }
    before = _tree(root)
    assert workspace.status(root)["data"]["state"] == "ready"
    assert workspace.validate(root)["data"]["valid"] is True
    assert _tree(root) == before


def test_workspace_adopts_ref_projects_full_basename_and_preserves_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref" / "nested").mkdir(parents=True)
    source = root / "ref" / "nested" / "report.txt"
    source.write_bytes(b"source")
    extra = root / "human-extra"
    extra.write_bytes(b"keep")
    before_ref = _tree(root / "ref")
    snapshots = _fake_providers(monkeypatch)
    adopted = workspace.prepare(root)
    assert adopted["data"]["action"] == "adopted"
    after_ref = _tree(root / "ref")
    assert after_ref == {"_outdated": None, **before_ref} and extra.read_bytes() == b"keep"
    unit = root / "agent-workbench" / "ref" / "nested" / "report.txt"
    assert (unit / "report.txt.md").is_file()
    assert (unit / "report.txt.json").is_file()
    assert (unit / "src" / "report.txt").read_bytes() == b"source"
    assert snapshots and all(not str(path).startswith(str(root / "ref")) for path in snapshots)
    manifest = _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")
    assert manifest["source_records"] == [{
        "path": "nested/report.txt", "kind": "file", "digest": hashlib.sha256(b"source").hexdigest(),
    }]
    assert manifest["items"][0]["provider_route"] == "markdown-conversion"


def test_workspace_adopts_existing_outer_archive_without_projecting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    archive = root / "ref" / "_outdated" / "human-layout"
    archive.mkdir(parents=True)
    (archive / "retired.txt").write_bytes(b"retired")
    (root / "ref" / "active.txt").write_bytes(b"active")
    before_archive = _tree(root / "ref" / "_outdated")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    assert _tree(root / "ref" / "_outdated") == before_archive
    manifest = _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")
    assert [record["path"] for record in manifest["source_records"]] == ["active.txt"]
    assert list((root / "agent-workbench" / "ref" / "_outdated").iterdir()) == []


def test_workspace_exact_noop_then_refresh_preserves_output_and_only_replaces_prepared_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    assert workspace.prepare(root)["data"]["action"] == "adopted"
    before = _tree(root)
    assert workspace.prepare(root)["data"]["action"] == "no_op"
    assert _tree(root) == before
    deliverable = root / "agent-workbench" / "output" / "answer.md"
    deliverable.write_bytes(b"answer")
    source.write_bytes(b"two")
    refreshed = workspace.prepare(root)
    assert refreshed["data"]["action"] == "refreshed"
    assert refreshed["data"]["generation"] == 2
    assert deliverable.read_bytes() == b"answer"
    unit = root / "agent-workbench" / "ref" / "memo.txt"
    assert (unit / "src" / "memo.txt").read_bytes() == b"two"


def test_workspace_add_only_refreshes_without_archive_batch_or_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    workspace.prepare(root)
    (root / "ref" / "new.txt").write_bytes(b"new")
    _fake_providers(monkeypatch)
    monkeypatch.setattr(
        workspace,
        "_utc_now",
        lambda: (_ for _ in ()).throw(AssertionError("add-only refresh must not create a retirement batch")),
    )
    refreshed = workspace.prepare(root)
    assert refreshed["data"] == {
        "action": "refreshed",
        "state": "ready",
        "workspace_id": refreshed["data"]["workspace_id"],
        "generation": 2,
        "source_items": 1,
        "warnings": [],
    }
    prepared = root / "agent-workbench" / "ref"
    assert (prepared / "new.txt" / "src" / "new.txt").read_bytes() == b"new"
    assert list((prepared / "_outdated").iterdir()) == []
    assert workspace.status(root)["data"]["state"] == "ready"


def test_workspace_changed_source_archives_old_ku_and_keeps_outer_archive_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    monkeypatch.setattr(
        workspace, "_utc_now", lambda: datetime(2026, 9, 1, 3, 4, tzinfo=timezone.utc),
    )
    source.write_bytes(b"two")
    refreshed = workspace.prepare(root)
    assert refreshed["data"]["archive_batch"] == "_outdated/generation-1-20260901T0304Z"
    assert refreshed["data"]["archived_sources"] == ["memo.txt"]
    assert list((root / "ref" / "_outdated").iterdir()) == []
    inner = root / "agent-workbench" / "ref"
    archived = inner / "_outdated" / "generation-1-20260901T0304Z" / "memo.txt"
    assert (archived / "src" / "memo.txt").read_bytes() == b"one"
    assert (inner / "memo.txt" / "src" / "memo.txt").read_bytes() == b"two"
    assert workspace.validate(root)["data"]["valid"] is True


def test_workspace_explicit_outdate_moves_outer_source_and_archives_same_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref" / "nested").mkdir(parents=True)
    (root / "ref" / "one.txt").write_bytes(b"one")
    (root / "ref" / "nested" / "two.txt").write_bytes(b"two")
    (root / "ref" / "keep.txt").write_bytes(b"keep")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    monkeypatch.setattr(
        workspace, "_utc_now", lambda: datetime(2026, 9, 1, 5, 6, tzinfo=timezone.utc),
    )
    result_value = workspace.prepare(root, ("nested/two.txt", "one.txt"))
    assert result_value["data"]["generation"] == 2
    assert result_value["data"]["source_items"] == 1
    assert result_value["data"]["archived_sources"] == ["nested/two.txt", "one.txt"]
    batch = "generation-1-20260901T0506Z"
    outer = root / "ref" / "_outdated" / batch
    inner = root / "agent-workbench" / "ref" / "_outdated" / batch
    assert (outer / "one.txt").read_bytes() == b"one"
    assert (outer / "nested" / "two.txt").read_bytes() == b"two"
    assert (inner / "one.txt" / "src" / "one.txt").read_bytes() == b"one"
    assert (inner / "nested" / "two.txt" / "src" / "two.txt").read_bytes() == b"two"
    assert not (root / "ref" / "one.txt").exists()
    assert not (root / "ref" / "nested" / "two.txt").exists()
    manifest = _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")
    assert [record["path"] for record in manifest["source_records"]] == ["keep.txt"]


def test_workspace_removed_source_is_archived_and_multiple_generations_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    moments = iter([
        datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 6, 1, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(workspace, "_utc_now", lambda: next(moments))
    source.write_bytes(b"two")
    workspace.prepare(root)
    source.unlink()
    workspace.prepare(root)
    archive = root / "agent-workbench" / "ref" / "_outdated"
    assert {path.name for path in archive.iterdir()} == {
        "generation-1-20260901T0600Z", "generation-2-20260901T0601Z",
    }
    assert (archive / "generation-1-20260901T0600Z" / "memo.txt" / "src" / "memo.txt").read_bytes() == b"one"
    assert (archive / "generation-2-20260901T0601Z" / "memo.txt" / "src" / "memo.txt").read_bytes() == b"two"
    assert _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")["generation"] == 3


def test_workspace_explicit_outdate_requires_exact_active_source_and_is_zero_write_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    source.write_bytes(b"changed")
    before = _tree(root)
    with pytest.raises(workspace.WorkspaceError, match="outdate_blocked") as captured:
        workspace.prepare(root, ("memo.txt", "missing.txt"))
    assert {item["code"] for item in captured.value.issues} == {
        "outdate_source_changed", "outdate_source_not_active",
    }
    assert _tree(root) == before


def test_workspace_explicit_outdate_rejects_missing_or_unrecognized_workspace_without_write(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(workspace.WorkspaceError, match="outdate_requires_recognized_workspace"):
        workspace.prepare(missing, ("memo.txt",))
    assert not missing.exists()
    ordinary = tmp_path / "ordinary"
    (ordinary / "ref").mkdir(parents=True)
    (ordinary / "ref" / "memo.txt").write_bytes(b"user")
    before = _tree(ordinary)
    with pytest.raises(workspace.WorkspaceError, match="outdate_requires_recognized_workspace"):
        workspace.prepare(ordinary, ("memo.txt",))
    assert _tree(ordinary) == before


def test_workspace_noop_does_not_read_archive_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "workspace"
    workspace.prepare(root)
    monkeypatch.setattr(
        workspace,
        "_utc_now",
        lambda: (_ for _ in ()).throw(AssertionError("no-op must not read the clock")),
    )
    assert workspace.prepare(root)["data"]["action"] == "no_op"


def test_workspace_nonempty_temp_is_busy_and_zero_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    source.write_bytes(b"two")
    temp = root / "agent-workbench" / "temp" / "in-progress"
    temp.write_bytes(b"work")
    before = _tree(root)
    assert workspace.status(root)["data"]["state"] == "busy"
    with pytest.raises(workspace.WorkspaceError, match="workbench_temp_not_empty") as validation:
        workspace.validate(root)
    assert validation.value.status == "busy"
    with pytest.raises(workspace.WorkspaceError, match="workbench_temp_not_empty") as captured:
        workspace.prepare(root)
    assert captured.value.status == "busy" and _tree(root) == before


def test_workspace_source_drift_before_adoption_publication_leaves_only_user_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    original_scan = workspace._scan_source
    calls = 0

    def scan_with_drift(reference: Path | None, core: CoreRunner):
        nonlocal calls
        calls += 1
        if calls == 2:
            source.write_bytes(b"two")
        return original_scan(reference, core)

    monkeypatch.setattr(workspace, "_scan_source", scan_with_drift)
    with pytest.raises(workspace.WorkspaceError, match="source_changed_during_prepare"):
        workspace.prepare(root)
    assert set(path.name for path in root.iterdir()) == {"ref"}
    assert source.read_bytes() == b"two"
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_final_temp_check_blocks_refresh_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    source.write_bytes(b"two")
    prepared_ref = root / "agent-workbench" / "ref"
    before_ref = _tree(prepared_ref)
    original_build = workspace._build_candidate

    def build_with_temp_race(candidate_root: Path, workspace_id: str, generation: int,
                             items: list[workspace.SourceItem], core: CoreRunner, **kwargs: object):
        built = original_build(candidate_root, workspace_id, generation, items, core, **kwargs)
        (root / "agent-workbench" / "temp" / "raced.tmp").write_bytes(b"work")
        return built

    monkeypatch.setattr(workspace, "_build_candidate", build_with_temp_race)
    with pytest.raises(workspace.WorkspaceError, match="workbench_temp_not_empty") as captured:
        workspace.prepare(root)
    assert captured.value.status == "busy"
    assert _tree(prepared_ref) == before_ref
    assert _json(prepared_ref / ".agent-workbench.json")["generation"] == 1
    assert (root / "agent-workbench" / "temp" / "raced.tmp").read_bytes() == b"work"
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_aggregates_unsupported_and_instruction_control_without_publication(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "one.bin").write_bytes(b"1")
    (root / "ref" / "two.exe").write_bytes(b"2")
    (root / "ref" / "CLAUDE.local.md").write_bytes(b"instructions")
    with pytest.raises(workspace.WorkspaceError) as captured:
        workspace.prepare(root)
    codes = [item["code"] for item in captured.value.issues]
    assert codes.count("unsupported_source_type") == 2
    assert "instruction_control_source" in codes
    assert not (root / "collaborative-workspace.json").exists()
    assert set(path.name for path in root.iterdir()) == {"ref"}


def test_workspace_refresh_aggregates_core_source_and_route_blockers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "valid.txt").write_bytes(b"valid")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    (root / "ref" / "unsupported.bin").write_bytes(b"unsupported")
    (root / "ref" / "CLAUDE.local.md").write_bytes(b"instructions")
    with pytest.raises(workspace.WorkspaceError) as captured:
        workspace.prepare(root)
    codes = {item["code"] for item in captured.value.issues}
    assert "unsupported_source_type" in codes
    assert "instruction_control_source" in codes or "instruction_control_path" in codes


def test_workspace_copies_existing_knowledge_unit_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    unit = root / "ref" / "manual"
    unit.mkdir(parents=True)
    (unit / "manual.md").write_bytes(b"manual")
    CoreRunner().knowledge_unit_stage_complete(unit)
    before = _tree(unit)
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    projected = root / "agent-workbench" / "ref" / "manual"
    assert _tree(projected) == before
    manifest = _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")
    assert manifest["items"][0]["provider_route"] == "knowledge-unit-copy"
    assert manifest["items"][0]["source_digest"] == manifest["items"][0]["prepared_digest"]


def test_workspace_warning_quality_is_published_and_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "warning.txt").write_bytes(b"warning")
    _fake_providers(monkeypatch, warning=True)
    prepared = workspace.prepare(root)
    assert prepared["data"]["state"] == "ready_with_warnings"
    assert prepared["data"]["warnings"] == ["fixture_warning"]
    assert workspace.validate(root)["data"]["state"] == "ready_with_warnings"


def test_workspace_tamper_is_invalid_and_never_readopted(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace.prepare(root)
    manifest = root / "collaborative-workspace.json"
    manifest.write_bytes(b"{}\n")
    assert workspace.status(root)["data"]["state"] == "invalid"
    with pytest.raises(workspace.WorkspaceError, match="workspace_invalid"):
        workspace.prepare(root)
    assert manifest.read_bytes() == b"{}\n"


@pytest.mark.parametrize("mutation", ["mismatch", "missing", "unsupported"])
def test_workspace_manifest_provider_route_tamper_is_invalid_and_cannot_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "memo.txt").write_bytes(b"source")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    manifest_path = root / "agent-workbench" / "ref" / ".agent-workbench.json"
    manifest = _json(manifest_path)
    item = manifest["items"][0]
    if mutation == "mismatch":
        item["provider_route"] = "file-conversion"
    elif mutation == "missing":
        del item["provider_route"]
    else:
        item["provider_route"] = "dynamic-provider"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    before = _tree(root)
    status = workspace.status(root)
    assert status["data"]["state"] == "invalid" and status["data"]["recognized"] is True
    if mutation == "mismatch":
        assert any(item["code"] == "provider_route_mismatch" for item in status["data"]["blockers"])
    with pytest.raises(workspace.WorkspaceError, match="workspace_invalid"):
        workspace.validate(root)
    with pytest.raises(workspace.WorkspaceError, match="workspace_invalid"):
        workspace.prepare(root)
    assert _tree(root) == before


def test_workspace_cli_surface_is_closed_and_requires_absolute_root(capsys: pytest.CaptureFixture[str]) -> None:
    from cortex_collaborative_workspace.cli import main

    assert main(["--json", "prepare", "--root", "relative"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "collaborative_workspace.prepare"
    assert value["issues"] == [{"code": "absolute_root_required"}]
    assert main(["--json", "delete", "--root", "C:\\workspace"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["issues"] == [{"code": "invalid_arguments"}]
    assert main(["--json", "prepare", "--root", str(Path.cwd()), "--outdate", "../memo.txt"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["issues"] == [{"code": "invalid_outdate_path"}]


def test_workspace_provider_binding_uses_exact_snapshot_cli_and_candidate_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = tmp_path / "provider.py"
    provider.write_text(
        """from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys
assert sys.argv.count('--config') == 1
p=argparse.ArgumentParser(); p.add_argument('--config', required=True); p.add_argument('--input', required=True); p.add_argument('--output-dir', required=True); p.add_argument('--bundle-name-mode', required=True); a=p.parse_args()
assert a.config == os.environ['EXPECTED_PROVIDER_CONFIG']
assert pathlib.Path(a.config).read_text() == 'config' and a.bundle_name_mode == 'source-basename'
source=pathlib.Path(a.input); unit=pathlib.Path(a.output_dir)/source.name; unit.mkdir()
(unit/(source.name+'.md')).write_text('# converted\\n', encoding='utf-8')
(unit/(source.name+'.json')).write_text(json.dumps({'quality':{'status':'complete','warnings':[]}}), encoding='utf-8')
(unit/'src').mkdir(); shutil.copyfile(source, unit/'src'/source.name); (unit/'assets').mkdir()
wire=(json.dumps({'command':'stage.complete','request':{'path':str(unit.resolve()),'private_root_files':[]}},separators=(',',':'))+'\\n').encode()
done=subprocess.run([sys.executable,'-I',os.environ['ANTI_ENTROPY_CORE_RUNNER']],input=wire,stdout=subprocess.PIPE,check=False)
raise SystemExit(done.returncode)
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text("config", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.setenv("MARKDOWN_CONVERSION_CONFIG", str(config.resolve()))
    monkeypatch.setenv("EXPECTED_PROVIDER_CONFIG", str(config.resolve()))
    snapshot = tmp_path / "snapshots" / "report.txt"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"source")
    output = tmp_path / "candidate"
    output.mkdir()
    quality, warnings = workspace._run_provider(
        "markdown-conversion", snapshot, output, output / "report.txt", CORE_RUNNER,
    )
    assert (quality, warnings) == ("ready", [])
    assert (output / "report.txt" / "report.txt.md").is_file()
    assert CoreRunner().knowledge_unit_validate(output / "report.txt")["status"] == "ok"


def test_workspace_both_provider_routes_omit_absent_config_and_publish_valid_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "report.pdf").write_bytes(b"pdf source")
    (root / "ref" / "memo.txt").write_bytes(b"text source")
    provider = _configless_provider(tmp_path)
    log = tmp_path / "provider.log"
    monkeypatch.setenv("SYNTHETIC_PROVIDER_LOG", str(log))
    for route in ("FILE", "MARKDOWN"):
        monkeypatch.setenv(route + "_CONVERSION_RUNNER", str(provider.resolve()))
        monkeypatch.delenv(route + "_CONVERSION_CONFIG", raising=False)

    prepared = workspace.prepare(root)

    assert prepared["data"]["action"] == "adopted"
    assert log.read_text("utf-8").splitlines() == ["memo.txt", "report.pdf"]
    manifest = _json(root / "agent-workbench" / "ref" / ".agent-workbench.json")
    assert {
        item["source_path"]: item["provider_route"] for item in manifest["items"]
    } == {"memo.txt": "markdown-conversion", "report.pdf": "file-conversion"}
    assert workspace.validate(root)["data"]["valid"] is True


@pytest.mark.parametrize(
    ("configured", "expected_code"),
    [
        pytest.param("empty", "file_conversion_config_required", id="empty"),
        pytest.param("relative", "file_conversion_config_not_absolute", id="relative"),
        pytest.param("missing", "path_missing", id="missing"),
        pytest.param("directory", "ordinary_file_required", id="directory"),
        pytest.param("link", "linked_or_reparse_path", id="link"),
        pytest.param("reparse-ancestor", "linked_or_reparse_path", id="reparse-ancestor"),
    ],
)
def test_workspace_present_invalid_provider_config_fails_item_without_provider_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configured: str,
    expected_code: str,
) -> None:
    from cortex_collaborative_workspace.cli import main

    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "report.pdf").write_bytes(b"source")
    before = _tree(root)
    marker = tmp_path / "provider-called"
    provider = tmp_path / "must-not-run.py"
    provider.write_text(
        "import os, pathlib\npathlib.Path(os.environ['PROVIDER_MARKER']).write_text('called')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROVIDER_MARKER", str(marker))
    monkeypatch.setenv("FILE_CONVERSION_RUNNER", str(provider.resolve()))
    if configured == "empty":
        raw = ""
    elif configured == "relative":
        raw = "provider-config.json"
    elif configured == "missing":
        raw = str(tmp_path / "missing-config.json")
    elif configured == "directory":
        directory = tmp_path / "config-directory"
        directory.mkdir()
        raw = str(directory.resolve())
    elif configured == "link":
        linked = tmp_path / "config-link.json"
        linked.write_text("{}", encoding="utf-8")
        raw = str(linked.absolute())
        ordinary_lstat = Path.lstat

        def linked_lstat(path: Path) -> os.stat_result:
            info = ordinary_lstat(path)
            if Path(path) == linked:
                values = list(info)
                values[0] = stat.S_IFLNK | stat.S_IMODE(info.st_mode)
                return os.stat_result(values)
            return info

        monkeypatch.setattr(Path, "lstat", linked_lstat)
    else:
        parent = tmp_path / "reparse-parent"
        parent.mkdir()
        config = parent / "config.json"
        config.write_text("{}", encoding="utf-8")
        parent_info = parent.lstat()
        parent_identity = (int(parent_info.st_dev), int(parent_info.st_ino))
        ordinary_reparse = workspace._is_reparse
        monkeypatch.setattr(
            workspace,
            "_is_reparse",
            lambda info: ordinary_reparse(info)
            or (int(info.st_dev), int(info.st_ino)) == parent_identity,
        )
        raw = str(config.resolve())
    monkeypatch.setenv("FILE_CONVERSION_CONFIG", raw)

    assert main(["--json", "prepare", "--root", str(root)]) == 3
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "validation_error"
    assert value["exit_code"] == 3
    assert value["data"] == {"failed_items": 1}
    assert value["issues"] == [{
        "code": expected_code,
        "path": "report.pdf",
        "provider_route": "file-conversion",
    }]
    assert not marker.exists()
    assert _tree(root) == before
    assert not any(
        path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir()
    )


def test_workspace_provider_runner_remains_required_when_config_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from cortex_collaborative_workspace.cli import main

    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "report.pdf").write_bytes(b"source")
    before = _tree(root)
    monkeypatch.delenv("FILE_CONVERSION_RUNNER", raising=False)
    monkeypatch.delenv("FILE_CONVERSION_CONFIG", raising=False)

    assert main(["--json", "prepare", "--root", str(root)]) == 3
    value = json.loads(capsys.readouterr().out)
    assert value["issues"] == [{
        "code": "file_conversion_runner_required",
        "path": "report.pdf",
        "provider_route": "file-conversion",
    }]
    assert _tree(root) == before
    assert not any(
        path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir()
    )


def test_workspace_mixed_route_binding_failure_prevents_all_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from cortex_collaborative_workspace.cli import main

    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "broken.pdf").write_bytes(b"broken route")
    (root / "ref" / "valid.txt").write_bytes(b"valid route")
    before = _tree(root)
    provider = _configless_provider(tmp_path)
    log = tmp_path / "provider.log"
    monkeypatch.setenv("SYNTHETIC_PROVIDER_LOG", str(log))
    monkeypatch.setenv("FILE_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.setenv("FILE_CONVERSION_CONFIG", "")
    monkeypatch.setenv("MARKDOWN_CONVERSION_RUNNER", str(provider.resolve()))
    monkeypatch.delenv("MARKDOWN_CONVERSION_CONFIG", raising=False)

    assert main(["--json", "prepare", "--root", str(root)]) == 3
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "validation_error" and value["exit_code"] == 3
    assert value["data"] == {"failed_items": 1}
    assert value["issues"] == [{
        "code": "file_conversion_config_required",
        "path": "broken.pdf",
        "provider_route": "file-conversion",
    }]
    assert not log.exists()
    assert _tree(root) == before
    assert not any(
        path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir()
    )


@pytest.mark.parametrize(
    ("stderr", "expected", "truncated"),
    [
        pytest.param(b"short failure\n", "short failure\n", False, id="short"),
        pytest.param(b"discarded" + b"L" * 4096, "L" * 4096, True, id="long-right-tail"),
        pytest.param(
            "文".encode("utf-8") + b"x" * 4095,
            "文" + "x" * 4095,
            False,
            id="utf8-character-crosses-old-byte-boundary",
        ),
        pytest.param(b"bad:\xff\xfe", r"bad:\xff\xfe", False, id="invalid-utf8"),
        pytest.param(
            b"nul:\x00 cr:\r lf:\n tab:\t esc:\x1b del:\x7f c1:" + "\u009b\u009d".encode("utf-8"),
            r"nul:\x00 cr:\r lf:" + "\n" + " tab:\t" + r" esc:\x1b del:\x7f c1:\x9b\x9d",
            False,
            id="c0-c1-controls",
        ),
        pytest.param(
            b"\x00" * 1025,
            (r"\x00" * 1025)[-4096:],
            True,
            id="escape-expansion-is-truncated-after-sanitizing",
        ),
    ],
)
def test_workspace_provider_failure_exposes_sanitized_bounded_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    expected: str,
    truncated: bool,
) -> None:
    captured = _provider_failure(
        tmp_path, monkeypatch, stderr=stderr, stdout=b"stdout-must-not-be-returned",
    )
    assert captured.data == {
        "provider_route": "markdown-conversion",
        "provider_exit_code": 23,
        "provider_stderr_excerpt": expected,
        "provider_stderr_truncated": truncated,
    }
    encoded = json.dumps(workspace.failure("collaborative_workspace.prepare", captured), ensure_ascii=True)
    assert json.loads(encoded)["data"] == captured.data
    assert len(captured.data["provider_stderr_excerpt"]) <= 4096
    assert "stdout-must-not-be-returned" not in encoded


def test_workspace_provider_failure_omits_diagnostics_for_empty_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _provider_failure(
        tmp_path, monkeypatch, stderr=b"", stdout=b"stdout-must-not-be-returned",
    )
    assert captured.data == {
        "provider_route": "markdown-conversion",
        "provider_exit_code": 23,
    }


def test_workspace_provider_failures_aggregate_without_adoption_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cortex_collaborative_workspace.cli import main

    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "one.pdf").write_bytes(b"one")
    (root / "ref" / "two.pdf").write_bytes(b"two")
    before = _tree(root)
    _failing_provider(
        tmp_path,
        monkeypatch,
        stderr=b"missing file-processing support skill\r\n",
        stdout=b"stdout-must-not-be-returned",
    )

    assert main(["--json", "prepare", "--root", str(root)]) == 3
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "validation_error"
    assert value["command"] == "collaborative_workspace.prepare"
    assert value["data"] == {"failed_items": 2}
    assert value["issues"] == [
        {
            "code": "provider_conversion_failed",
            "path": name,
            "provider_route": "file-conversion",
            "provider_exit_code": 23,
            "provider_stderr_excerpt": "missing file-processing support skill\\r\n",
            "provider_stderr_truncated": False,
        }
        for name in ("one.pdf", "two.pdf")
    ]
    encoded = json.dumps(value)
    assert value["exit_code"] == 3
    assert "stdout-must-not-be-returned" not in encoded
    assert _tree(root) == before
    assert not any(
        path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir()
    )


def test_workspace_provider_failure_preserves_previous_projection_on_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.pdf"
    source.write_bytes(b"one")
    real_provider = workspace._run_provider
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    source.write_bytes(b"two")
    before = _tree(root)
    monkeypatch.setattr(workspace, "_run_provider", real_provider)
    _failing_provider(tmp_path, monkeypatch, stderr=b"conversion runtime unavailable")

    with pytest.raises(workspace.WorkspaceError, match="projection_failed") as captured:
        workspace.prepare(root)

    assert captured.value.issues == [{
        "code": "provider_conversion_failed",
        "path": "memo.pdf",
        "provider_route": "file-conversion",
        "provider_exit_code": 23,
        "provider_stderr_excerpt": "conversion runtime unavailable",
        "provider_stderr_truncated": False,
    }]
    assert _tree(root) == before
    assert (
        root / "agent-workbench" / "ref" / "memo.pdf" / "src" / "memo.pdf"
    ).read_bytes() == b"one"
    assert not any(
        path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir()
    )


def test_workspace_adoption_failure_cleans_only_unchanged_owned_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    extra = root / "existing.txt"
    extra.write_bytes(b"keep")
    original = workspace._copy_file

    def fail_manifest(
        source: Path, destination: Path, expected_digest: str | None = None,
    ) -> workspace.Installed:
        if destination.name == "collaborative-workspace.json":
            raise workspace.WorkspaceError("io_error", "injected_manifest_failure")
        return original(source, destination, expected_digest)

    monkeypatch.setattr(workspace, "_copy_file", fail_manifest)
    with pytest.raises(workspace.WorkspaceError, match="injected_manifest_failure") as captured:
        workspace.prepare(root)
    assert captured.value.data.get("residue", []) == []
    assert {path.name for path in root.iterdir()} == {"existing.txt"}
    assert extra.read_bytes() == b"keep"


def test_copy_file_exclusive_collision_preserves_preexisting_target(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"source")
    destination = tmp_path / "destination.txt"
    destination.write_bytes(b"preexisting")
    with pytest.raises(workspace.WorkspaceError, match="source_snapshot_failed"):
        workspace._copy_file(source, destination)
    assert destination.read_bytes() == b"preexisting"


@pytest.mark.parametrize("replace_destination", [False, True])
def test_workspace_adoption_post_copy_source_drift_uses_creation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_destination: bool,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    original_identity = workspace._identity
    source_checks = 0

    def drift_after_copy(
        path: Path, *, directory: bool | None = None, missing: bool = False,
    ) -> workspace.Identity | None:
        nonlocal source_checks
        observed = original_identity(path, directory=directory, missing=missing)
        if path.name == "AGENTS.md" and "candidate" in path.parts and directory is False:
            source_checks += 1
            if source_checks == 2:
                assert observed is not None
                if replace_destination:
                    destination = root / "AGENTS.md"
                    destination.unlink()
                    destination.write_bytes(b"user replacement")
                return workspace.Identity(observed.device, observed.inode + 1, observed.mode)
        return observed

    monkeypatch.setattr(workspace, "_identity", drift_after_copy)
    with pytest.raises(workspace.WorkspaceError, match="source_changed_during_snapshot") as captured:
        workspace.prepare(root)
    if replace_destination:
        assert captured.value.data["residue"] == [str(root / "AGENTS.md")]
        assert {path.name for path in root.iterdir()} == {"AGENTS.md"}
        assert (root / "AGENTS.md").read_bytes() == b"user replacement"
    else:
        assert "residue" not in captured.value.data
        assert list(root.iterdir()) == []
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_adoption_copy_collision_preserves_raced_target_and_cleans_owned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    original = workspace._copy_file

    def collide_on_second_guide(
        source: Path, destination: Path, expected_digest: str | None = None,
    ) -> workspace.Installed:
        if destination == root / "CLAUDE.md":
            destination.write_bytes(b"raced")
        return original(source, destination, expected_digest)

    monkeypatch.setattr(workspace, "_copy_file", collide_on_second_guide)
    with pytest.raises(workspace.WorkspaceError, match="source_snapshot_failed"):
        workspace.prepare(root)
    assert {path.name for path in root.iterdir()} == {"CLAUDE.md"}
    assert (root / "CLAUDE.md").read_bytes() == b"raced"
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_adoption_cleanup_preserves_replacement_after_exclusive_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    original = workspace._copy_file

    def replace_first_then_fail(
        source: Path, destination: Path, expected_digest: str | None = None,
    ) -> workspace.Installed:
        if destination == root / "CLAUDE.md":
            raise workspace.WorkspaceError("io_error", "injected_later_artifact_failure")
        copied = original(source, destination, expected_digest)
        if destination == root / "AGENTS.md":
            destination.unlink()
            destination.write_bytes(b"user replacement")
        return copied

    monkeypatch.setattr(workspace, "_copy_file", replace_first_then_fail)
    with pytest.raises(workspace.WorkspaceError, match="injected_later_artifact_failure") as captured:
        workspace.prepare(root)
    assert captured.value.data["residue"] == [str(root / "AGENTS.md")]
    assert {path.name for path in root.iterdir()} == {"AGENTS.md"}
    assert (root / "AGENTS.md").read_bytes() == b"user replacement"
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_adoption_cleanup_preserves_replaced_renamed_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    original_copy = workspace._copy_file
    original_rename = workspace.os.rename

    def replace_after_rename(source: Path, destination: Path) -> None:
        original_rename(source, destination)
        destination = Path(destination)
        if destination in {root / "ref", root / "agent-workbench"}:
            workspace._delete_no_follow(destination)
            destination.mkdir()
            (destination / "user-owned.txt").write_bytes(destination.name.encode("utf-8"))

    def fail_manifest(
        source: Path, destination: Path, expected_digest: str | None = None,
    ) -> workspace.Installed:
        if destination == root / "collaborative-workspace.json":
            raise workspace.WorkspaceError("io_error", "injected_manifest_failure")
        return original_copy(source, destination, expected_digest)

    monkeypatch.setattr(workspace.os, "rename", replace_after_rename)
    monkeypatch.setattr(workspace, "_copy_file", fail_manifest)
    with pytest.raises(workspace.WorkspaceError, match="injected_manifest_failure") as captured:
        workspace.prepare(root)
    assert captured.value.data["residue"] == [
        str(root / "agent-workbench"), str(root / "ref"),
    ]
    assert {path.name for path in root.iterdir()} == {"ref", "agent-workbench"}
    assert (root / "ref" / "user-owned.txt").read_bytes() == b"ref"
    assert (root / "agent-workbench" / "user-owned.txt").read_bytes() == b"agent-workbench"
    assert not any(path.name.startswith(".cortex-collaborative-workspace-") for path in root.parent.iterdir())


def test_workspace_adoption_rejects_unsafe_nested_extra_before_any_write(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    unsafe = root / "extra" / ".claude"
    unsafe.mkdir(parents=True)
    (unsafe / "settings.json").write_bytes(b"{}")
    before = _tree(root)
    with pytest.raises(workspace.WorkspaceError) as captured:
        workspace.prepare(root)
    assert any(item["code"] == "instruction_control_extra" for item in captured.value.issues)
    assert _tree(root) == before


def test_workspace_refresh_rename_failure_restores_old_prepared_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    prepared = root / "agent-workbench" / "ref"
    old_tree = _tree(prepared)
    source.write_bytes(b"two")
    original = workspace.os.rename

    def fail_candidate(source_path: str | os.PathLike[str], destination_path: str | os.PathLike[str]) -> None:
        source_value, destination_value = Path(source_path), Path(destination_path)
        if source_value.name == "ref" and "candidate" in source_value.parts and destination_value == prepared:
            raise OSError("injected")
        original(source_path, destination_path)

    monkeypatch.setattr(workspace.os, "rename", fail_candidate)
    with pytest.raises(workspace.WorkspaceError, match="refresh_publish_failed") as captured:
        workspace.prepare(root)
    assert captured.value.data == {"published": False}
    assert _tree(prepared) == old_tree
    assert not any(path.name.startswith(".ref-old-") for path in prepared.parent.iterdir())


def test_workspace_explicit_outdate_rename_failure_restores_outer_and_inner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    monkeypatch.setattr(
        workspace, "_utc_now", lambda: datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
    )
    before = _tree(root)
    prepared = root / "agent-workbench" / "ref"
    original = workspace.os.rename

    def fail_candidate(source_path: str | os.PathLike[str], destination_path: str | os.PathLike[str]) -> None:
        source_value, destination_value = Path(source_path), Path(destination_path)
        if source_value.name == "ref" and "candidate" in source_value.parts and destination_value == prepared:
            raise OSError("injected")
        original(source_path, destination_path)

    monkeypatch.setattr(workspace.os, "rename", fail_candidate)
    with pytest.raises(workspace.WorkspaceError, match="refresh_publish_failed") as captured:
        workspace.prepare(root, ("memo.txt",))
    assert captured.value.data == {"published": False}
    assert _tree(root) == before


def test_workspace_refresh_preserves_backup_when_cleanup_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    (root / "ref").mkdir(parents=True)
    source = root / "ref" / "memo.txt"
    source.write_bytes(b"one")
    _fake_providers(monkeypatch)
    workspace.prepare(root)
    source.write_bytes(b"two")
    original = workspace._same_identity

    def changed(path: Path, expected: workspace.Identity) -> bool:
        if path.name.startswith(".ref-old-"):
            return False
        return original(path, expected)

    monkeypatch.setattr(workspace, "_same_identity", changed)
    with pytest.raises(workspace.WorkspaceError, match="refresh_cleanup_identity_changed") as captured:
        workspace.prepare(root)
    assert captured.value.data["published"] is True
    residue = Path(captured.value.data["residue"][0])
    assert residue.is_dir()
    assert (root / "agent-workbench" / "ref" / "memo.txt" / "src" / "memo.txt").read_bytes() == b"two"


def test_workspace_nonwaiting_lock_reports_busy_without_workspace_mutation(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace.prepare(root)
    before = _tree(root)
    with workspace._writer_lock(root):
        with pytest.raises(workspace.WorkspaceError, match="workspace_busy") as captured:
            workspace.status(root)
    assert captured.value.status == "busy" and _tree(root) == before


def test_workspace_status_and_validate_create_no_workspace_or_lock_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "missing-workspace"
    lock_root = tmp_path / "lock-root"
    lock_root.mkdir()
    monkeypatch.setattr(workspace.tempfile, "gettempdir", lambda: str(lock_root))
    assert workspace.status(root)["data"] == {"state": "uninitialized", "recognized": False}
    with pytest.raises(workspace.WorkspaceError, match="workspace_uninitialized"):
        workspace.validate(root)
    assert not root.exists() and list(lock_root.iterdir()) == []


def test_workspace_stage_is_created_as_a_same_volume_sibling(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    stage = workspace._new_stage(root)
    try:
        parent_identity = workspace._identity(root.parent, directory=True)
        assert parent_identity is not None
        assert stage.root.parent == root.parent
        assert stage.identity.device == parent_identity.device
    finally:
        assert workspace._cleanup_stage(stage) == []
    assert not stage.root.exists()


def test_workspace_cleanup_does_not_follow_directory_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep")
    owned = tmp_path / "owned"
    owned.mkdir()
    link = owned / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host cannot create directory symlinks: {exc}")
    workspace._delete_no_follow(owned)
    assert not owned.exists()
    assert sentinel.read_bytes() == b"keep"
