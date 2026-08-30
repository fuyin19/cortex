from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cortex.cli import main
from cortex.core_runner import CoreRunner
from cortex.jsonio import json_bytes


PARTITION = "project-alpha"
FAKE_CORE_RUNNER = Path(__file__).parent / "fixtures" / "core_protocol_runner.py"
REAL_CORE_RUNNER = os.environ.get("CORTEX_REAL_CORE_RUNNER")


@pytest.fixture(autouse=True)
def explicit_core_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "core-calls.jsonl"
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", str(FAKE_CORE_RUNNER.resolve()))
    monkeypatch.setenv("FAKE_CORE_LOG", str(log))
    return log


def _write(path: Path, value: dict) -> Path:
    path.write_bytes(json_bytes(value))
    return path


def _invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


def _bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    root = tmp_path / "bundle"
    assert _invoke(capsys, "--workspace", str(root), "manage", "init")[0] == 0
    tags = {"version": 2, "groups": [{"name": "project", "tags": [{"tag": PARTITION, "description": "project"}]}]}
    layout = {"version": 5, "partition_tag_group": "project", "partition_name_strategy": "tag", "unit_name_strategy": "tag-title-date", "max_component_length": 96, "duplicate_name_strategy": "reject"}
    for name, value in (("tags", tags), ("layout", layout)):
        operand = _write(tmp_path / f"{name}.json", value)
        assert _invoke(capsys, "--workspace", str(root), "manage", "config", "set", "--profile", name, "--file", str(operand))[0] == 0
    return root


def _metadata(tmp_path: Path, title: str) -> Path:
    return _write(tmp_path / f"{title}.json", {"title": title, "timestamp": "2026-08-26T00:00:00Z", "tags": [PARTITION]})


def _unit(bundle: Path, result: dict) -> Path:
    return bundle / result["data"]["partition"] / result["data"]["record"]


def _calls(log: Path) -> list[dict]:
    return [] if not log.exists() else [json.loads(line) for line in log.read_text("utf-8").splitlines()]


def test_explicit_runner_has_no_handshake_and_stage_complete_is_called_once(tmp_path, capsys, explicit_core_runner):
    bundle = _bundle(tmp_path, capsys)
    source = tmp_path / "memo.md"
    source.write_bytes(b"memo")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--metadata", str(_metadata(tmp_path, "Source")))
    assert code == 0 and (_unit(bundle, result) / "memo.md").read_bytes() == b"memo"
    calls = _calls(explicit_core_runner)
    assert not any(item["command"] == "capabilities" for item in calls)
    completed = [item for item in calls if item["command"] == "stage.complete"]
    assert len(completed) == 1
    assert completed[0]["request"]["private_root_files"] == ["record.json"]
    assert Path(completed[0]["request"]["path"]).is_absolute()


def test_conversion_uses_inspect_and_non_ok_result_is_preserved(tmp_path, capsys, monkeypatch, explicit_core_runner):
    bundle = _bundle(tmp_path, capsys)
    conversion = tmp_path / "conversion"
    conversion.mkdir()
    (conversion / "memo.md").write_bytes(b"memo")
    assert _invoke(capsys, "--workspace", str(bundle), "record", "add", "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Conversion")))[0] == 0
    assert [item["command"] for item in _calls(explicit_core_runner)].count("inspect") == 2
    monkeypatch.setenv("FAKE_CORE_FAIL", "inspect")
    code, rejected = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Rejected")))
    assert code == 3 and rejected["issues"][0]["code"] == "fake_core_rejected"


@pytest.mark.parametrize("record_kind", ["file", "directory"])
def test_conversion_rejects_private_record_metadata_before_copy(tmp_path, capsys, record_kind):
    bundle = _bundle(tmp_path, capsys)
    conversion = tmp_path / "conversion-with-record"
    conversion.mkdir()
    (conversion / "memo.md").write_bytes(b"memo")
    reserved = conversion / "record.json"
    if record_kind == "file":
        reserved.write_bytes(b"{}\n")
    else:
        reserved.mkdir()

    code, rejected = _invoke(
        capsys,
        "--workspace", str(bundle),
        "record", "add",
        "--conversion", str(conversion),
        "--metadata", str(_metadata(tmp_path, "Reserved")),
    )

    assert code == 3
    assert rejected["issues"][0]["code"] == "reserved_record_metadata"


@pytest.mark.parametrize(
    "payload",
    [
        {"abi": "anti-entropy-core.runner/v1", "status": "ok", "exit_code": 0, "command": "inspect", "data": {}, "issues": [], "extra": True},
        {"abi": "anti-entropy-core.runner/v1", "status": "ok", "exit_code": 3, "command": "inspect", "data": {}, "issues": []},
    ],
)
def test_core_runner_rejects_noncanonical_results(tmp_path, payload):
    runner_path = tmp_path / "bad-runner.py"
    runner_path.write_text(
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    runner = CoreRunner(runner_path)
    with pytest.raises(Exception) as exc_info:
        runner.inspect(tmp_path)
    assert getattr(exc_info.value, "code", None) == "core_protocol_error"


def test_cortex_retains_explicit_source_equality_rule(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    source = tmp_path / "memo.docx"
    source.write_bytes(b"source")
    conversion = tmp_path / "conversion"
    (conversion / "src").mkdir(parents=True)
    (conversion / "memo.md").write_bytes(b"memo")
    (conversion / "src/memo.docx").write_bytes(b"different")
    code, rejected = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Mismatch")))
    assert code == 3 and rejected["issues"][0]["code"] == "conversion_source_mismatch"


def test_runner_is_explicit_and_absolute(tmp_path, capsys, monkeypatch):
    root = tmp_path / "bundle"
    assert _invoke(capsys, "--workspace", str(root), "manage", "init")[0] == 0
    monkeypatch.delenv("ANTI_ENTROPY_CORE_RUNNER")
    code, result = _invoke(capsys, "--workspace", str(root), "manage", "status")
    assert code == 2 and result["issues"][0]["code"] == "core_runner_required"
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", "relative.py")
    code, result = _invoke(capsys, "--workspace", str(root), "manage", "status")
    assert code == 2 and result["issues"][0]["code"] == "core_runner_not_absolute"


def test_align_plan_apply_and_stale_fixture(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    source = tmp_path / "memo.md"
    source.write_bytes(b"memo")
    code, added = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--metadata", str(_metadata(tmp_path, "Align")))
    assert code == 0
    unit = _unit(bundle, added)
    (unit / ".core-invalid").write_bytes(b"")
    code, planned = _invoke(capsys, "--workspace", str(bundle), "align", "plan")
    expected = [f"{added['data']['partition']}/{added['data']['record']}"]
    assert code == 0 and planned["data"]["plan"]["repairs"] == expected
    plan_path = _write(tmp_path / "align-plan.json", planned["data"]["plan"])
    code, applied = _invoke(capsys, "align", "apply", "--plan", str(plan_path))
    assert code == 0 and applied["data"]["repaired"] == expected and not (unit / ".core-invalid").exists()

    (unit / ".core-invalid").write_bytes(b"")
    stale_plan = _invoke(capsys, "--workspace", str(bundle), "align", "plan")[1]["data"]["plan"]
    stale_path = _write(tmp_path / "stale-plan.json", stale_plan)
    (unit / "memo.md").write_bytes(b"drift")
    code, stale = _invoke(capsys, "align", "apply", "--plan", str(stale_path))
    assert code == 3 and stale["issues"][0]["code"] == "stale_align_plan"


@pytest.mark.skipif(REAL_CORE_RUNNER is None, reason="set CORTEX_REAL_CORE_RUNNER for cross-repo integration")
def test_real_core_runner_stage_complete_and_validate(tmp_path, monkeypatch):
    assert REAL_CORE_RUNNER is not None
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", REAL_CORE_RUNNER)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memo.md").write_bytes(b"memo")
    (stage / "record.json").write_bytes(b"{}\n")
    core = CoreRunner.from_config()
    core.stage_complete(stage, private_root_files=("record.json",))
    core.validate(stage, private_root_files=("record.json",))
