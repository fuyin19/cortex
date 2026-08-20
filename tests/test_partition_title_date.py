from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import cortex.cli as cli_module
import cortex.service as service_module
from cortex.cli import main
from cortex.constants import DEFAULT_LAYOUT, PUBLIC_ROUTES, VERSION
from cortex.jsonio import json_bytes
from cortex.naming import partition_title_date_name, semantic_title, title_slug


ROOT = Path(__file__).parents[1]


def invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = main(["--json", *args])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.isascii()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert list(result) == ["status", "exit_code", "command", "data", "issues"]
    assert captured.out == json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n"
    assert result["exit_code"] == code
    return code, result


def write_json(path: Path, value: object) -> Path:
    path.write_bytes(json_bytes(value))
    return path


def layout(**changes: object) -> dict[str, Any]:
    return dict(
        DEFAULT_LAYOUT,
        partition_by="project",
        unit_name_strategy="partition-title-date",
        duplicate_name_strategy="reject",
        **changes,
    )


def tags(*names: str) -> dict[str, Any]:
    return {
        "version": 2,
        "groups": [
            {
                "name": "project",
                "tags": [{"tag": name, "description": name} for name in names],
            }
        ],
    }


def init_and_configure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    partition_names: tuple[str, ...] = ("research",),
    layout_value: dict[str, Any] | None = None,
) -> Path:
    bundle = tmp_path / "bundle"
    assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    tag_file = write_json(tmp_path / "tags.json", tags(*partition_names))
    assert invoke(
        capsys,
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "set",
        "--profile",
        "tags",
        "--file",
        str(tag_file),
    )[0] == 0
    layout_file = write_json(tmp_path / "layout.json", layout_value or layout())
    assert invoke(
        capsys,
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "set",
        "--profile",
        "layout",
        "--file",
        str(layout_file),
    )[0] == 0
    return bundle


def add(
    bundle: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    title: str,
    partition: str = "research",
    timestamp: str | None = "2026-08-20T23:59:58+08:00",
    selector: tuple[str, ...] | None = None,
    source_name: str = "source.bin",
) -> tuple[int, dict[str, Any]]:
    source = tmp_path / source_name
    source.write_bytes(b"opaque")
    metadata: dict[str, Any] = {"title": title, "tags": [partition]}
    if timestamp is not None:
        metadata["timestamp"] = timestamp
    metadata_file = write_json(tmp_path / f"metadata-{source_name}.json", metadata)
    selected = selector or ("--workspace", str(bundle))
    return invoke(
        capsys,
        *selected,
        "record",
        "add",
        "--source",
        str(source),
        "--metadata",
        str(metadata_file),
    )


def test_naming_goldens_keep_legacy_and_define_composite() -> None:
    vector = " Café／報告 Q2 -- Draft***Final—版 "
    middle = "café／報告-q2-draft-final—版"
    assert title_slug("Quarterly Results", 96) == "quarterly-results"
    assert title_slug(vector, 96) == middle
    assert title_slug("Café 報告", 96) == "café-報告"
    assert title_slug("abcdefghijklmnopq", 16) == "abcdefghijklmnop"
    assert title_slug(". -Alpha- . ", 96) == "alpha"
    assert title_slug("CON", 96) == "_con"
    assert semantic_title(vector) == middle
    assert partition_title_date_name("research", vector, "2026-08-20T00:01:02Z", 96) == (
        "research-café／報告-q2-draft-final—版-20260820"
    )
    assert partition_title_date_name("abcde", "CON", "2026-08-20T00:01:02Z", 16) == "abcde-c-20260820"


@pytest.mark.parametrize("strategy", [None, "unknown"])
def test_layout_unknown_and_null_strategy_keep_validation_behavior(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], strategy: str | None
) -> None:
    bundle = tmp_path / "bundle"
    assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    candidate = dict(DEFAULT_LAYOUT, unit_name_strategy=strategy)
    operand = write_json(tmp_path / "layout-invalid.json", candidate)
    code, result = invoke(
        capsys,
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "set",
        "--profile",
        "layout",
        "--file",
        str(operand),
    )
    assert code == 3
    assert result["issues"][0]["code"] == "invalid_unit_name_strategy"


def test_composite_requires_reject_and_cross_profile_title_capacity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "bundle"
    assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    invalid = dict(
        DEFAULT_LAYOUT,
        unit_name_strategy="partition-title-date",
        duplicate_name_strategy="numeric-suffix",
    )
    invalid_file = write_json(tmp_path / "invalid-layout.json", invalid)
    code, result = invoke(
        capsys,
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "set",
        "--profile",
        "layout",
        "--file",
        str(invalid_file),
    )
    assert code == 3 and result["issues"][0]["code"] == "invalid_duplicate_strategy"

    tag_file = write_json(tmp_path / "tags.json", tags("123456"))
    assert invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", "tags", "--file", str(tag_file))[0] == 0
    too_small = write_json(tmp_path / "too-small.json", layout(max_component_length=16))
    code, result = invoke(
        capsys,
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "set",
        "--profile",
        "layout",
        "--file",
        str(too_small),
    )
    assert code == 3 and any(item["code"] == "insufficient_unit_name_capacity" for item in result["issues"])


def test_composite_add_exact_name_and_whole_codepoint_truncation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = init_and_configure(
        tmp_path,
        capsys,
        partition_names=("abcde",),
        layout_value=layout(max_component_length=16),
    )
    code, result = add(bundle, tmp_path, capsys, title="CON", partition="abcde")
    assert code == 0
    assert result["data"] == {
        "record": "abcde/abcde-c-20260820",
        "path": "abcde/abcde-c-20260820",
    }
    assert (bundle / "abcde" / "abcde-c-20260820").is_dir()

    other = tmp_path / "other"
    other.mkdir()
    bundle2 = init_and_configure(
        other,
        capsys,
        partition_names=("abcde",),
        layout_value=layout(max_component_length=16),
    )
    before = sorted(path.relative_to(bundle2).as_posix() for path in bundle2.rglob("*"))
    code, result = add(bundle2, other, capsys, title="界", partition="abcde")
    assert code == 3 and result["issues"][0]["code"] == "insufficient_unit_name_capacity"
    assert sorted(path.relative_to(bundle2).as_posix() for path in bundle2.rglob("*")) == before
    assert not list(bundle2.rglob(".cortex-add-*"))


@pytest.mark.parametrize("timestamp", [None, "2026-08-20T10:20:30"])
def test_composite_add_requires_explicit_aware_timestamp_before_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], timestamp: str | None
) -> None:
    bundle = init_and_configure(tmp_path, capsys)
    code, result = add(bundle, tmp_path, capsys, title="A", timestamp=timestamp)
    assert code == 3 and result["issues"][0]["code"] == "invalid_timestamp"
    assert not list(bundle.rglob(".cortex-add-*"))
    assert not (bundle / "research").exists()


def test_duplicate_is_locked_and_rejected_before_staging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = init_and_configure(tmp_path, capsys)
    assert add(bundle, tmp_path, capsys, title="Same", source_name="one.bin")[0] == 0

    called = False

    def should_not_copy(_source: Path, _destination: Path) -> None:
        nonlocal called
        called = True
        raise AssertionError("duplicate reached staging")

    monkeypatch.setattr(service_module, "copy_regular", should_not_copy)
    code, result = add(bundle, tmp_path, capsys, title="Same", source_name="two.bin")
    assert code == 3 and result["issues"][0]["code"] == "duplicate_record_name"
    assert not called and not list(bundle.rglob(".cortex-add-*"))


def test_composite_final_collision_is_duplicate_and_legacy_path_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = init_and_configure(tmp_path, capsys)
    monkeypatch.setattr(service_module, "rename_no_replace", lambda *_args: (_ for _ in ()).throw(FileExistsError("seam")))
    code, result = add(bundle, tmp_path, capsys, title="Collision")
    assert code == 3 and result["issues"][0]["code"] == "duplicate_record_name"
    assert not list(bundle.rglob(".cortex-add-*"))

    monkeypatch.undo()
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy = init_and_configure(legacy_root, capsys, layout_value=dict(DEFAULT_LAYOUT, partition_by="project"))
    monkeypatch.setattr(service_module, "rename_no_replace", lambda *_args: (_ for _ in ()).throw(FileExistsError("seam")))
    code, result = add(legacy, legacy_root, capsys, title="Collision")
    assert code == 6 and result["issues"][0]["code"] == "record_stage_failed"
    assert not list(legacy.rglob(".cortex-add-*"))


def test_composite_record_edit_keeps_path_and_optional_timestamp_semantics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = init_and_configure(tmp_path, capsys)
    _, added = add(bundle, tmp_path, capsys, title="Original")
    record = added["data"]["record"]
    metadata = write_json(tmp_path / "edit.json", {"title": "Changed", "tags": ["research"]})
    code, result = invoke(
        capsys,
        "--workspace",
        str(bundle),
        "record",
        "edit",
        "--record",
        record,
        "--metadata",
        str(metadata),
    )
    assert code == 0 and result["data"]["record"] == record
    path = bundle.joinpath(*record.split("/"))
    assert path.is_dir()
    edited = json.loads((path / "record.json").read_text("utf-8"))
    assert edited["title"] == "Changed" and edited["timestamp"].endswith("Z")


def test_registered_composite_add_uses_existing_managed_routes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    bundle = root / "bundle"
    assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    registry = write_json(
        tmp_path / "registry.json",
        {"version": 1, "bundles": [{"id": "bundle", "path": "bundle", "description": "Bundle"}]},
    )
    assert invoke(capsys, "--kb-root", str(root), "registry", "set", "--file", str(registry))[0] == 0
    selector = ("--kb-root", str(root), "--bundle-id", "bundle")
    tag_file = write_json(tmp_path / "managed-tags.json", tags("research"))
    layout_file = write_json(tmp_path / "managed-layout.json", layout())
    assert invoke(capsys, *selector, "manage", "config", "set", "--profile", "tags", "--file", str(tag_file))[0] == 0
    assert invoke(capsys, *selector, "manage", "config", "set", "--profile", "layout", "--file", str(layout_file))[0] == 0
    code, result = add(bundle, tmp_path, capsys, title="Managed", selector=selector)
    assert code == 0 and result["data"]["record"] == "research/research-managed-20260820"


def _subprocess_environment(*, encoding: str = "utf-8") -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONIOENCODING"] = encoding
    return environment


def _add_command(bundle: Path, source: Path, metadata: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cortex",
        "--json",
        "--workspace",
        str(bundle),
        "record",
        "add",
        "--source",
        str(source),
        "--metadata",
        str(metadata),
    ]


def test_two_process_add_has_one_winner_retry_duplicate_and_no_orphan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = init_and_configure(tmp_path, capsys)
    source = tmp_path / "process-source.bin"
    source.write_bytes(b"process")
    metadata = write_json(
        tmp_path / "process-metadata.json",
        {"title": "Concurrent", "timestamp": "2026-08-20T12:00:00Z", "tags": ["research"]},
    )
    command = _add_command(bundle, source, metadata)
    first = subprocess.Popen(command, cwd=ROOT, env=_subprocess_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(command, cwd=ROOT, env=_subprocess_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    completed = [first.communicate(timeout=20), second.communicate(timeout=20)]
    codes = [first.returncode, second.returncode]
    results = [json.loads(stdout.decode("ascii")) for stdout, _stderr in completed]
    assert all(stderr == b"" for _stdout, stderr in completed)
    assert codes.count(0) == 1
    loser = results[codes.index(next(code for code in codes if code != 0))]
    assert (loser["status"], loser["exit_code"]) in {("busy", 5), ("validation_error", 3)}
    if loser["exit_code"] == 3:
        assert loser["issues"][0]["code"] == "duplicate_record_name"

    retry = subprocess.run(command, cwd=ROOT, env=_subprocess_environment(), capture_output=True, timeout=20)
    retry_result = json.loads(retry.stdout.decode("ascii"))
    assert retry.returncode == 3 and retry.stderr == b""
    assert retry_result["issues"][0]["code"] == "duplicate_record_name"
    partition = bundle / "research"
    assert [path.name for path in partition.iterdir()] == ["research-concurrent-20260820"]
    assert not list(bundle.rglob(".cortex-add-*"))


def test_cp936_json_boundary_is_ascii_and_unicode_round_trips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = init_and_configure(tmp_path, capsys, partition_names=("🚀",))
    command = [
        sys.executable,
        "-m",
        "cortex",
        "--json",
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "show",
        "--profile",
        "tags",
    ]
    completed = subprocess.run(command, cwd=ROOT, env=_subprocess_environment(encoding="cp936"), capture_output=True, timeout=20)
    assert completed.returncode == 0 and completed.stderr == b"" and completed.stdout.isascii()
    result = json.loads(completed.stdout.decode("ascii"))
    assert list(result) == ["status", "exit_code", "command", "data", "issues"]
    assert result["data"]["value"]["groups"][0]["tags"][0]["tag"] == "🚀"


def test_human_output_stays_on_stdout_and_backslash_replaces_for_cp936(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = init_and_configure(tmp_path, capsys, partition_names=("🚀",))
    command = [
        sys.executable,
        "-m",
        "cortex",
        "--workspace",
        str(bundle),
        "manage",
        "config",
        "show",
        "--profile",
        "tags",
    ]
    completed = subprocess.run(command, cwd=ROOT, env=_subprocess_environment(encoding="cp936"), capture_output=True, timeout=20)
    assert completed.returncode == 0 and completed.stderr == b""
    rendered = completed.stdout.decode("cp936")
    assert rendered.splitlines()[0] == "status: ok" and "\\U0001f680" in rendered

    failure = subprocess.run(
        [sys.executable, "-m", "cortex", "--workspace", str(tmp_path / "missing"), "manage", "validate"],
        cwd=ROOT,
        env=_subprocess_environment(encoding="cp936"),
        capture_output=True,
        timeout=20,
    )
    assert failure.returncode == 3 and failure.stderr == b""
    failure_text = failure.stdout.decode("cp936")
    assert failure_text.splitlines()[0] == "status: validation_error" and "error:" in failure_text


def test_json_render_failure_uses_encoding_safe_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NarrowStream:
        encoding = "cp936"

        def __init__(self) -> None:
            self.value = ""

        def write(self, value: str) -> int:
            value.encode(self.encoding, errors="strict")
            self.value += value
            return len(value)

    stdout = NarrowStream()
    stderr = NarrowStream()
    monkeypatch.setattr(cli_module.sys, "stdout", stdout)
    monkeypatch.setattr(cli_module.sys, "stderr", stderr)
    monkeypatch.setattr(cli_module.json, "dumps", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("🚀")))
    assert not cli_module._render({"status": "ok", "exit_code": 0, "command": "x", "data": {}, "issues": []}, True)
    assert stdout.value == ""
    assert "JSON output failure" in stderr.value and "\\U0001f680" in stderr.value


def test_skills_docs_fixture_and_closed_architecture_contracts() -> None:
    for relative in ("skills/cortex-build/SKILL.md", "skills/cortex-manage/SKILL.md"):
        text = (ROOT / relative).read_text("utf-8")
        assert "Canonicalize" in text and "absolute path" in text and "ordinary non-reparse executable" in text
        assert "exactly one `cortex 5.1.0` line" in text and "empty stderr" in text
        assert "do not fall back" in text and "re-resolve" in text
        assert "bare PATH command" in text and "`PYTHONPATH`" in text and "`python -m`" in text
        assert '"<CORTEX-ABSOLUTE-EXECUTABLE>" --json' in text
        assert "cortex --json" not in text
    build = (ROOT / "skills/cortex-build/SKILL.md").read_text("utf-8")
    manage = (ROOT / "skills/cortex-manage/SKILL.md").read_text("utf-8")
    assert "Inspect the complete Layout Profile" in build and "timezone-aware RFC3339" in build
    assert "`title-slug`" in manage and "`partition-title-date`" in manage and "only with `reject`" in manage

    capability = json.loads((ROOT / "fixtures/capabilities/cortex5-surface.json").read_text("utf-8"))
    assert capability["unit_name_strategies"] == ["title-slug", "partition-title-date"]
    assert capability["version"] == VERSION and capability["routes"] == list(PUBLIC_ROUTES)
    combined = "\n".join(
        (ROOT / relative).read_text("utf-8")
        for relative in ("README.md", "docs/global-knowledge.md", "docs/record-kb-architecture.md")
    )
    assert "insufficient_unit_name_capacity" in combined
    assert "exact partition tag" in combined
    assert "Record edit never renames" in combined or "Record edits never rename" in combined
    assert not any(route_word in PUBLIC_ROUTES for route_word in ("delete", "migrate", "flatten"))
