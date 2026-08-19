from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import cortex.native as native_module
import cortex.service as service_module
from cortex.cli import main
from cortex.constants import DEFAULT_LAYOUT, DEFAULT_TAGS, PUBLIC_ROUTES, RECORD_SCHEMA, VERSION
from cortex.jsonio import json_bytes
from cortex.locking import workspace_lock
from cortex.native import is_reparse_metadata
from cortex.errors import CortexError, io_error


def invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(["--json", *args])
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert set(result) == {"status", "exit_code", "command", "data", "issues"}
    assert result["exit_code"] == code
    return code, result


def write_json(path: Path, value: object) -> Path:
    path.write_bytes(json_bytes(value))
    return path


def snapshot(root: Path) -> list[tuple[str, str, bytes | None]]:
    if not root.exists():
        return []
    output: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        output.append((relative, "dir", None) if path.is_dir() else (relative, "file", path.read_bytes()))
    return output


def init_kb(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    kb = tmp_path / "kb"
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "init")
    assert code == 0 and result["status"] == "ok"
    return kb


def tags_profile(*, projects: tuple[str, ...] = ("project-a", "project-b"), extras: tuple[str, ...] = ("listed",)) -> dict:
    groups = []
    if projects:
        groups.append({"name": "project", "tags": [{"tag": tag, "description": f"{tag} description"} for tag in projects]})
    if extras:
        groups.append({"name": "listing-standard", "tags": [{"tag": tag, "description": tag} for tag in extras]})
    return {"version": 2, "groups": groups}


def layout_profile(**changes: object) -> dict:
    return dict(DEFAULT_LAYOUT, partition_by="project", **changes)


def set_profile(kb: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], profile: str, value: dict) -> tuple[int, dict]:
    operand = write_json(tmp_path / f"{profile}-{len(list(tmp_path.glob(profile + '-*')))}.json", value)
    return invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", profile, "--file", str(operand))


def configure(kb: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *, projects: tuple[str, ...] = ("project-a", "project-b")) -> None:
    assert set_profile(kb, tmp_path, capsys, "tags", tags_profile(projects=projects))[0] == 0
    assert set_profile(kb, tmp_path, capsys, "layout", layout_profile())[0] == 0


def add_record(
    kb: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    title: str = "Alpha Record",
    project: str = "project-a",
    tags: list[str] | None = None,
    source_name: str = "source.bin",
    source_bytes: bytes = b"source\x00bytes",
    conversion: Path | None = None,
) -> tuple[dict, Path]:
    source = tmp_path / source_name
    source.write_bytes(source_bytes)
    metadata = {"title": title, "timestamp": "2026-08-19T10:20:30+08:00", "tags": tags if tags is not None else [project]}
    operand = write_json(tmp_path / f"metadata-{len(list(tmp_path.glob('metadata-*')))}.json", metadata)
    args = ["--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(operand)]
    if conversion is not None:
        args.extend(["--conversion", str(conversion)])
    code, result = invoke(capsys, *args)
    assert code == 0, result
    return result, kb.joinpath(*result["data"]["record"].split("/"))


def test_sc_001_init_is_profiles_only_and_unconfigured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    assert {path.relative_to(kb).as_posix() for path in kb.rglob("*")} == {"profiles", "profiles/record-schema.json", "profiles/tags.json", "profiles/layout.json"}
    assert (kb / "profiles" / "record-schema.json").read_bytes() == json_bytes(RECORD_SCHEMA)
    assert json.loads((kb / "profiles" / "tags.json").read_text("utf-8")) == DEFAULT_TAGS
    assert json.loads((kb / "profiles" / "layout.json").read_text("utf-8")) == DEFAULT_LAYOUT
    code, status = invoke(capsys, "--workspace", str(kb), "manage", "status")
    assert code == 0 and status["data"] == {"version": VERSION, "valid": True, "count": 0}


def test_sc_002_closed_surface_and_version(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert len(PUBLIC_ROUTES) == 11
    code, result = invoke(capsys, "--workspace", str(tmp_path / "x"), "build", "ingest")
    assert code == 2 and result["status"] == "usage_error"
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "cortex 5.1.0"


def test_sc_003_unconfigured_add_rejected_without_mutation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "metadata.json", {"title": "A", "tags": []})
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 3 and result["issues"][0]["code"] == "bundle_not_operational" and snapshot(kb) == before


def test_sc_004_partitioned_add_exact_unit_and_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    result, unit = add_record(kb, tmp_path, capsys, tags=["listed", "project-a"])
    assert result["data"] == {"record": "project-a/alpha-record", "path": "project-a/alpha-record"}
    assert {path.name for path in unit.iterdir()} == {"record.json", "original"}
    assert (unit / "original" / "source.bin").read_bytes() == b"source\x00bytes"
    assert not (kb / "records").exists() and not (kb / "unstructured").exists()


@pytest.mark.parametrize("tags,code", [(["listed"], "partition_tag_count"), (["project-a", "project-b"], "partition_tag_count"), (["missing", "project-a"], "unregistered_tag"), (["project-a", "project-a"], "duplicate_record_tag")])
def test_sc_005_invalid_record_tags_do_not_mutate(tmp_path: Path, capsys: pytest.CaptureFixture[str], tags: list[str], code: str) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "metadata.json", {"title": "A", "tags": tags})
    before = snapshot(kb)
    status, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert status == 3 and result["issues"][0]["code"] == code and snapshot(kb) == before


@pytest.mark.parametrize(
    "profile,expected",
    [
        ({"version": 2, "groups": [{"name": "", "tags": []}]}, "invalid_group_name"),
        ({"version": 2, "groups": [{"name": "x", "tags": []}]}, "invalid_group_tags"),
        ({"version": 2, "groups": [{"name": "x", "tags": [{"tag": "a", "description": ""}]}, {"name": "x", "tags": [{"tag": "b", "description": ""}]}]}, "duplicate_group_name"),
        ({"version": 2, "groups": [{"name": "x", "tags": [{"tag": "a", "description": ""}]}, {"name": "y", "tags": [{"tag": "a", "description": ""}]}]}, "duplicate_tag"),
    ],
)
def test_sc_006_tag_profile_strict_negative(tmp_path: Path, capsys: pytest.CaptureFixture[str], profile: dict, expected: str) -> None:
    kb = init_kb(tmp_path, capsys)
    code, result = set_profile(kb, tmp_path, capsys, "tags", profile)
    assert code == 3 and any(item["code"] == expected for item in result["issues"])


def test_sc_007_layout_link_and_partition_names_cross_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    assert set_profile(kb, tmp_path, capsys, "layout", layout_profile())[0] == 3
    assert set_profile(kb, tmp_path, capsys, "tags", tags_profile(projects=("A", "a")))[0] == 0
    code, result = set_profile(kb, tmp_path, capsys, "layout", layout_profile())
    assert code == 3 and any(item["code"] == "partition_casefold_collision" for item in result["issues"])
    assert set_profile(kb, tmp_path, capsys, "tags", tags_profile(projects=("profiles",)))[0] == 0
    code, result = set_profile(kb, tmp_path, capsys, "layout", layout_profile())
    assert code == 3 and any(item["code"] == "reserved_partition_name" for item in result["issues"])


def test_sc_008_validation_catches_partition_shape_and_metadata_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    _, unit = add_record(kb, tmp_path, capsys)
    record = json.loads((unit / "record.json").read_text("utf-8"))
    record["tags"] = ["project-b"]
    (unit / "record.json").write_bytes(json_bytes(record))
    (kb / "project-b").mkdir()
    (kb / "rogue").mkdir()
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "validate")
    codes = {item["code"] for item in result["issues"]}
    assert code == 3 and {"partition_path_mismatch", "empty_partition", "unregistered_partition"} <= codes


def test_sc_009_edit_path_stable_and_partition_change_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    _, unit = add_record(kb, tmp_path, capsys)
    edit = write_json(tmp_path / "edit.json", {"title": "Different", "tags": ["project-a"]})
    code, result = invoke(capsys, "--workspace", str(kb), "record", "edit", "--record", "project-a/alpha-record", "--metadata", str(edit))
    assert code == 0 and result["data"]["record"] == "project-a/alpha-record" and unit.is_dir()
    move = write_json(tmp_path / "move.json", {"title": "Different", "tags": ["project-b"]})
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "record", "edit", "--record", "project-a/alpha-record", "--metadata", str(move))
    assert code == 3 and result["issues"][0]["code"] == "partition_change_forbidden" and snapshot(kb) == before


@pytest.mark.parametrize("operand", ["alpha-record", "/project-a/alpha-record", "project-a\\alpha-record", "project-a/../alpha", "a/b/c"])
def test_sc_010_edit_operand_is_exact_two_component_posix(tmp_path: Path, capsys: pytest.CaptureFixture[str], operand: str) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    edit = write_json(tmp_path / "edit.json", {"title": "A", "tags": ["project-a"]})
    code, result = invoke(capsys, "--workspace", str(kb), "record", "edit", "--record", operand, "--metadata", str(edit))
    assert code == 3 and result["issues"][0]["code"] == "invalid_record_operand"


def test_sc_011_config_cross_validation_and_description_only_update(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    add_record(kb, tmp_path, capsys)
    changed = tags_profile()
    changed["groups"][0]["tags"][0]["description"] = "changed only"
    assert set_profile(kb, tmp_path, capsys, "tags", changed)[0] == 0
    before = snapshot(kb)
    assert set_profile(kb, tmp_path, capsys, "tags", tags_profile(projects=("project-b",)))[0] == 3
    assert snapshot(kb) == before
    assert set_profile(kb, tmp_path, capsys, "layout", dict(DEFAULT_LAYOUT))[0] == 3
    assert snapshot(kb) == before


def test_sc_012_suffix_scope_unicode_limit_and_reject(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    assert set_profile(kb, tmp_path, capsys, "layout", layout_profile(max_component_length=16))[0] == 0
    _, first = add_record(kb, tmp_path, capsys, title=" CON ", source_name="one")
    _, second = add_record(kb, tmp_path, capsys, title="CON", source_name="two")
    _, other = add_record(kb, tmp_path, capsys, title="CON", project="project-b", source_name="three")
    _, unicode_unit = add_record(kb, tmp_path, capsys, title="界" * 20, source_name="four")
    assert first.name == "_con" and second.name == "_con-2" and other.name == "_con"
    assert len(unicode_unit.name.encode("utf-8")) <= 16
    assert set_profile(kb, tmp_path, capsys, "layout", layout_profile(max_component_length=16, duplicate_name_strategy="reject"))[0] == 0
    source = tmp_path / "five"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "duplicate.json", {"title": "con", "tags": ["project-b"]})
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 3 and result["issues"][0]["code"] == "duplicate_record_name"


def test_sc_013_source_and_conversion_custody(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    conversion = tmp_path / "conversion"
    (conversion / "empty").mkdir(parents=True)
    (conversion / "nested").mkdir()
    (conversion / "nested" / "record.json").write_bytes(b"opaque\x00\xff")
    _, unit = add_record(kb, tmp_path, capsys, conversion=conversion)
    root = unit / "representations" / "markdown-conversion"
    assert (root / "empty").is_dir() and (root / "nested" / "record.json").read_bytes() == b"opaque\x00\xff"


def test_sc_014_reparse_and_reserved_source_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    source = tmp_path / ".cortex-input"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "metadata.json", {"title": "A", "tags": ["project-a"]})
    assert invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))[1]["issues"][0]["code"] == "reserved_staging_name"
    synthetic = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    assert is_reparse_metadata(synthetic)


def test_sc_015_one_lock_busy_for_all_mutations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    _, unit = add_record(kb, tmp_path, capsys)
    source = tmp_path / "busy-source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "busy.json", {"title": "Busy", "tags": ["project-a"]})
    tags = write_json(tmp_path / "busy-tags.json", tags_profile())
    layout = write_json(tmp_path / "busy-layout.json", layout_profile())
    commands = [("record", "add", "--source", str(source), "--metadata", str(metadata)), ("record", "edit", "--record", "project-a/alpha-record", "--metadata", str(metadata)), ("manage", "config", "set", "--profile", "tags", "--file", str(tags)), ("manage", "config", "set", "--profile", "layout", "--file", str(layout))]
    with workspace_lock(kb):
        for tail in commands:
            code, result = invoke(capsys, "--workspace", str(kb), *tail)
            assert code == 5 and result["status"] == "busy"
    assert unit.is_dir()


@pytest.mark.parametrize("payload,code", [(b"\xef\xbb\xbf{}", "json_bom"), (b'{"title":"A","title":"B","tags":[]}', "duplicate_json_key"), (b"[]", "invalid_json_top_level"), (b"\xff", "invalid_utf8")])
def test_sc_016_strict_json_nonmutating(tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: bytes, code: str) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = tmp_path / "bad.json"
    metadata.write_bytes(payload)
    before = snapshot(kb)
    status, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert status == 3 and result["issues"][0]["code"] == code and snapshot(kb) == before


def test_sc_017_stdin_profile_and_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    kb = init_kb(tmp_path, capsys)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json_bytes(tags_profile())), encoding="utf-8"))
    assert invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "tags", "--file", "-")[0] == 0
    assert set_profile(kb, tmp_path, capsys, "layout", layout_profile())[0] == 0
    source = tmp_path / "source"
    source.write_bytes(b"x")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json_bytes({"title": "Stream", "tags": ["project-a"]})), encoding="utf-8"))
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", "-")
    assert code == 0 and result["data"]["record"] == "project-a/stream"


def test_sc_018_read_side_effect_free_and_end_to_end_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    add_record(kb, tmp_path, capsys)
    add_record(kb, tmp_path, capsys, project="project-b", title="Beta", source_name="other")
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "validate")
    assert code == 0 and result["data"] == {"version": VERSION, "valid": True, "count": 2}
    assert snapshot(kb) == before and list(kb.glob(".cortex*")) == []


def test_sc_019_add_stage_failure_cleans_new_and_existing_partition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "first.json", {"title": "First", "tags": ["project-a"]})

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise io_error("injected", "copy_failed")

    monkeypatch.setattr(service_module, "copy_regular", fail_copy)
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 6 and result["issues"][0]["code"] == "copy_failed" and snapshot(kb) == before
    assert not (kb / "project-a").exists() and list(kb.glob(".cortex-*")) == []

    monkeypatch.undo()
    add_record(kb, tmp_path, capsys)
    monkeypatch.setattr(service_module, "copy_regular", fail_copy)
    metadata = write_json(tmp_path / "second.json", {"title": "Second", "tags": ["project-a"]})
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 6 and result["issues"][0]["code"] == "copy_failed" and snapshot(kb) == before
    assert list((kb / "project-a").glob(".cortex-*")) == []


def test_sc_020_atomic_file_failures_clean_own_temporary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    add_record(kb, tmp_path, capsys)
    edit = write_json(tmp_path / "edit.json", {"title": "Edited", "tags": ["project-a"]})
    tags = write_json(tmp_path / "tags-change.json", tags_profile())
    layout = write_json(tmp_path / "layout-change.json", layout_profile())
    before = snapshot(kb)

    def fail_replace(_source: str, _destination: str) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(service_module.os, "replace", fail_replace)
    commands = [
        ("record", "edit", "--record", "project-a/alpha-record", "--metadata", str(edit)),
        ("manage", "config", "set", "--profile", "tags", "--file", str(tags)),
        ("manage", "config", "set", "--profile", "layout", "--file", str(layout)),
    ]
    for tail in commands:
        code, result = invoke(capsys, "--workspace", str(kb), *tail)
        assert code == 6 and result["issues"][0]["code"] == "replace_failed"
        assert snapshot(kb) == before
        assert list(kb.rglob(".cortex-*.tmp")) == []


def test_sc_021_global_knowledge_and_surfaces_are_consistent() -> None:
    root = Path(__file__).parents[1]
    global_knowledge = (root / "docs" / "global-knowledge.md").read_text("utf-8")
    assert "record.json" in global_knowledge and "original/<one-source-file>" in global_knowledge
    assert "partition_by" in global_knowledge and "no mandatory `records/`" in global_knowledge
    for relative in ("README.md", "AGENTS.md", "docs/record-kb-architecture.md"):
        assert "global-knowledge.md" in (root / relative).read_text("utf-8")
    for relative in ("skills/cortex-build/SKILL.md", "skills/cortex-manage/SKILL.md"):
        text = (root / relative).read_text("utf-8")
        assert "--bundle-id" in text and "write" in text
        assert "docs/global-knowledge.md" not in text
    capability = json.loads((root / "fixtures" / "capabilities" / "cortex5-surface.json").read_text("utf-8"))
    assert capability["version"] == VERSION and capability["routes"] == list(PUBLIC_ROUTES)
    assert capability["tag_profile_version"] == 2 and capability["layout_profile_version"] == 2
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert plugin["version"] == VERSION


def test_windows_publish_retries_two_access_denials_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    attempts: list[tuple[str, str]] = []
    sleeps: list[float] = []

    monkeypatch.setattr(native_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(native_module, "exists", lambda _path: False)
    monkeypatch.setattr(native_module.time, "sleep", sleeps.append)

    def transient_rename(left: str, right: str) -> None:
        attempts.append((left, right))
        if len(attempts) < 3:
            raise PermissionError("transient access denied")

    monkeypatch.setattr(native_module.os, "rename", transient_rename)
    native_module.rename_no_replace(source, destination)

    assert len(attempts) == 3
    assert sleeps == [0.05, 0.10]


def test_windows_publish_stops_if_destination_appears_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    attempts = 0
    existence_checks = iter((False, True))
    sleeps: list[float] = []

    monkeypatch.setattr(native_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(native_module, "exists", lambda _path: next(existence_checks))
    monkeypatch.setattr(native_module.time, "sleep", sleeps.append)

    def denied_rename(_left: str, _right: str) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("transient access denied")

    monkeypatch.setattr(native_module.os, "rename", denied_rename)
    with pytest.raises(FileExistsError):
        native_module.rename_no_replace(source, destination)

    assert attempts == 1
    assert sleeps == [0.05]


def test_windows_publish_does_not_retry_unrelated_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    attempts = 0
    sleeps: list[float] = []

    monkeypatch.setattr(native_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(native_module, "exists", lambda _path: False)
    monkeypatch.setattr(native_module.time, "sleep", sleeps.append)

    def unrelated_failure(_left: str, _right: str) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("unrelated failure")

    monkeypatch.setattr(native_module.os, "rename", unrelated_failure)
    with pytest.raises(CortexError) as exc:
        native_module.rename_no_replace(source, destination)

    assert exc.value.code == "publish_failed"
    assert attempts == 1
    assert sleeps == []


def test_sc_022_escaped_surrogate_text_is_rejected_without_temporary_residue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kb = init_kb(tmp_path, capsys)
    operand = tmp_path / "surrogate-tags.json"
    operand.write_bytes(b'{"version":2,"groups":[{"name":"project","tags":[{"tag":"safe","description":"\\ud800"}]}]}')
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "tags", "--file", str(operand))
    assert code == 3 and result["issues"][0]["code"] == "invalid_tag_description"
    assert snapshot(kb) == before and list((kb / "profiles").glob(".cortex-*")) == []


def test_sc_023_reordered_record_fields_are_noncanonical_and_validation_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kb = init_kb(tmp_path, capsys)
    configure(kb, tmp_path, capsys)
    _, unit = add_record(kb, tmp_path, capsys)
    reordered = {
        "tags": ["project-a"],
        "title": "Alpha Record",
        "timestamp": "2026-08-19T10:20:30+08:00",
    }
    (unit / "record.json").write_bytes(json_bytes(reordered))
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "validate")
    assert code == 3 and any(item["code"] == "noncanonical_record_json" for item in result["issues"])
    assert snapshot(kb) == before
