from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from cortex.cli import main
from cortex.constants import DEFAULT_LAYOUT, DEFAULT_TAGS, PUBLIC_ROUTES, RECORD_SCHEMA, VERSION
from cortex.jsonio import json_bytes
from cortex.locking import workspace_lock
from cortex.native import is_reparse_metadata


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


def init_kb(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    kb = tmp_path / "kb"
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "init")
    assert code == 0 and result["status"] == "ok"
    return kb


def set_tags(kb: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], tags: list[str]) -> None:
    profile = {"version": 1, "tags": [{"tag": tag, "description": f"{tag} description"} for tag in tags]}
    operand = write_json(tmp_path / f"tags-{len(tags)}.json", profile)
    code, _ = invoke(
        capsys,
        "--workspace",
        str(kb),
        "manage",
        "config",
        "set",
        "--profile",
        "tags",
        "--file",
        str(operand),
    )
    assert code == 0


def add_record(
    kb: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    title: str = "Alpha Record",
    timestamp: str | None = "2026-08-19T10:20:30+08:00",
    tags: list[str] | None = None,
    source_name: str = "source.bin",
    source_bytes: bytes = b"source\x00bytes",
    conversion: Path | None = None,
) -> tuple[dict, Path]:
    source = tmp_path / source_name
    source.write_bytes(source_bytes)
    metadata: dict[str, object] = {"title": title, "tags": tags or []}
    if timestamp is not None:
        metadata["timestamp"] = timestamp
    metadata_path = write_json(tmp_path / f"metadata-{len(list(tmp_path.glob('metadata-*')))}.json", metadata)
    args = [
        "--workspace",
        str(kb),
        "record",
        "add",
        "--source",
        str(source),
        "--metadata",
        str(metadata_path),
    ]
    if conversion is not None:
        args.extend(["--conversion", str(conversion)])
    code, result = invoke(capsys, *args)
    assert code == 0, result
    folder = result["data"]["record"]
    return result, kb / "records" / folder


def snapshot(root: Path) -> list[tuple[str, str, bytes | None]]:
    if not root.exists():
        return []
    output: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            output.append((relative, "dir", None))
        else:
            output.append((relative, "file", path.read_bytes()))
    return output


def test_sc_001_init_exact_tree_and_profiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    assert {path.relative_to(kb).as_posix() for path in kb.rglob("*")} == {
        "profiles",
        "profiles/record-schema.json",
        "profiles/tags.json",
        "profiles/layout.json",
        "records",
    }
    assert (kb / "profiles" / "record-schema.json").read_bytes() == json_bytes(RECORD_SCHEMA)
    assert json.loads((kb / "profiles" / "tags.json").read_text("utf-8")) == DEFAULT_TAGS
    assert json.loads((kb / "profiles" / "layout.json").read_text("utf-8")) == DEFAULT_LAYOUT
    assert not (kb / ".cortex").exists()


def test_sc_002_closed_cli_and_version(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert PUBLIC_ROUTES == (
        "manage.init",
        "manage.status",
        "manage.validate",
        "manage.config.show",
        "manage.config.set",
        "record.add",
        "record.edit",
    )
    code, result = invoke(capsys, "--workspace", str(tmp_path / "x"), "build", "ingest")
    assert code == 2 and result["status"] == "usage_error"
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"cortex {VERSION}"


def test_sc_003_flat_record_shape_and_exact_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    _, record_dir = add_record(kb, tmp_path, capsys)
    assert {path.name for path in record_dir.iterdir()} == {"record.json", "original"}
    value = {"title": "Alpha Record", "timestamp": "2026-08-19T10:20:30+08:00", "tags": []}
    assert (record_dir / "record.json").read_bytes() == json_bytes(value)
    assert (record_dir / "original" / "source.bin").read_bytes() == b"source\x00bytes"


@pytest.mark.parametrize(
    "metadata,code",
    [
        ({"title": "", "tags": []}, "invalid_title"),
        ({"title": "A", "tags": ["missing"]}, "unregistered_tag"),
        ({"title": "A", "tags": [], "extra": 1}, "unknown_field"),
        ({"title": "A", "tags": [], "timestamp": "2026-08-19"}, "invalid_timestamp"),
        ({"title": "A", "tags": ["x", "x"]}, "duplicate_record_tag"),
    ],
)
def test_sc_004_three_field_validation_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], metadata: dict, code: str
) -> None:
    kb = init_kb(tmp_path, capsys)
    if "x" in metadata.get("tags", []):
        set_tags(kb, tmp_path, capsys, ["x"])
    source = tmp_path / "input"
    source.write_bytes(b"x")
    operand = write_json(tmp_path / "bad.json", metadata)
    before = snapshot(kb)
    status, result = invoke(
        capsys,
        "--workspace",
        str(kb),
        "record",
        "add",
        "--source",
        str(source),
        "--metadata",
        str(operand),
    )
    assert status == 3 and result["issues"][0]["code"] == code
    assert snapshot(kb) == before


def test_sc_005_edit_title_does_not_move_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    _, record_dir = add_record(kb, tmp_path, capsys)
    patch = write_json(
        tmp_path / "edit.json",
        {"title": "Completely Different", "timestamp": "2026-08-19T10:20:30+08:00", "tags": []},
    )
    code, result = invoke(
        capsys,
        "--workspace",
        str(kb),
        "record",
        "edit",
        "--record",
        record_dir.name,
        "--metadata",
        str(patch),
    )
    assert code == 0 and result["data"]["record"] == record_dir.name
    assert record_dir.is_dir() and not (kb / "records" / "completely-different").exists()
    assert json.loads((record_dir / "record.json").read_text("utf-8"))["title"] == "Completely Different"


def test_sc_006_timestamp_preserved_or_generated_utc(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    _, first = add_record(kb, tmp_path, capsys, title="Exact", timestamp="2026-08-19T01:02:03.400-00:00")
    assert json.loads((first / "record.json").read_text("utf-8"))["timestamp"] == "2026-08-19T01:02:03.400-00:00"
    _, second = add_record(kb, tmp_path, capsys, title="Generated", timestamp=None, source_name="other")
    generated = json.loads((second / "record.json").read_text("utf-8"))["timestamp"]
    assert len(generated) == 27 and generated.endswith("Z") and generated[19] == "."


def test_sc_007_metadata_only_edit_preserves_custody(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    _, record_dir = add_record(kb, tmp_path, capsys)
    original = (record_dir / "original" / "source.bin").read_bytes()
    patch = write_json(
        tmp_path / "tags-edit.json",
        {"title": "Alpha Record", "tags": []},
    )
    code, _ = invoke(capsys, "--workspace", str(kb), "record", "edit", "--record", record_dir.name, "--metadata", str(patch))
    assert code == 0 and (record_dir / "original" / "source.bin").read_bytes() == original
    assert json.loads((record_dir / "record.json").read_text("utf-8"))["timestamp"].endswith("Z")


def test_sc_008_three_profiles_show_set_and_orphan_guard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    set_tags(kb, tmp_path, capsys, ["A", "a"])
    code, shown = invoke(capsys, "--workspace", str(kb), "manage", "config", "show", "--profile", "tags")
    assert code == 0 and [item["tag"] for item in shown["data"]["value"]["tags"]] == ["A", "a"]
    _, _ = add_record(kb, tmp_path, capsys, tags=["A"])
    empty = write_json(tmp_path / "empty-tags.json", DEFAULT_TAGS)
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "tags", "--file", str(empty))
    assert code == 3 and result["issues"][0]["code"] == "orphaned_tag_reference" and snapshot(kb) == before


def test_sc_009_slug_suffix_casefold_and_utf8_cap(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    layout = dict(DEFAULT_LAYOUT, max_component_length=16)
    path = write_json(tmp_path / "layout.json", layout)
    assert invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "layout", "--file", str(path))[0] == 0
    _, first = add_record(kb, tmp_path, capsys, title="  CON  ", source_name="one")
    _, second = add_record(kb, tmp_path, capsys, title="CON", source_name="two")
    _, third = add_record(kb, tmp_path, capsys, title="界" * 20, source_name="three")
    assert first.name == "_con" and second.name == "_con-2"
    assert len(third.name.encode("utf-8")) <= 16 and not third.name.encode("utf-8").endswith(b"\xef\xbf\xbd")


def test_sc_010_duplicate_reject(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    layout = dict(DEFAULT_LAYOUT, duplicate_name_strategy="reject")
    path = write_json(tmp_path / "layout-reject.json", layout)
    assert invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "layout", "--file", str(path))[0] == 0
    add_record(kb, tmp_path, capsys, title="Same", source_name="one")
    source = tmp_path / "two"
    source.write_bytes(b"2")
    metadata = write_json(tmp_path / "same.json", {"title": "SAME", "tags": []})
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 3 and result["issues"][0]["code"] == "duplicate_record_name"


def test_sc_011_records_root_change_empty_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    layout = dict(DEFAULT_LAYOUT, records_root="items")
    path = write_json(tmp_path / "layout-items.json", layout)
    code, _ = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "layout", "--file", str(path))
    assert code == 0 and (kb / "items").is_dir() and not (kb / "records").exists()
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "record.json", {"title": "Item", "tags": []})
    assert invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))[0] == 0
    revert = write_json(tmp_path / "layout-records.json", DEFAULT_LAYOUT)
    before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "layout", "--file", str(revert))
    assert code == 3 and result["issues"][0]["code"] == "records_root_not_empty" and snapshot(kb) == before


def test_sc_012_source_and_conversion_file_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    conversion = tmp_path / "converted.md"
    conversion.write_bytes(b"\xffopaque markdown")
    _, record_dir = add_record(kb, tmp_path, capsys, conversion=conversion)
    assert (record_dir / "representations" / "markdown-conversion" / "converted.md").read_bytes() == b"\xffopaque markdown"


def test_sc_013_conversion_directory_paths_empty_dirs_and_opaque(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    conversion = tmp_path / "conversion"
    (conversion / "empty").mkdir(parents=True)
    (conversion / "nested").mkdir()
    (conversion / "nested" / "record.json").write_bytes(b"not JSON and intentionally opaque\x00")
    _, record_dir = add_record(kb, tmp_path, capsys, conversion=conversion)
    root = record_dir / "representations" / "markdown-conversion"
    assert (root / "empty").is_dir()
    assert (root / "nested" / "record.json").read_bytes() == b"not JSON and intentionally opaque\x00"


def test_sc_014_unsafe_source_basename_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    source = tmp_path / ".cortex-input"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "meta.json", {"title": "Safe", "tags": []})
    code, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert code == 3 and result["issues"][0]["code"] == "reserved_staging_name"


def test_sc_015_conversion_symlink_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    conversion = tmp_path / "conversion"
    conversion.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = conversion / "link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        synthetic = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
        assert is_reparse_metadata(synthetic)
        return
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = write_json(tmp_path / "metadata.json", {"title": "Safe", "tags": []})
    code, result = invoke(
        capsys,
        "--workspace",
        str(kb),
        "record",
        "add",
        "--source",
        str(source),
        "--conversion",
        str(conversion),
        "--metadata",
        str(metadata),
    )
    assert code == 3 and result["issues"][0]["code"] == "reparse_path"


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"\xef\xbb\xbf{}", "json_bom"),
        (b'{"title":"A","title":"B","tags":[]}', "duplicate_json_key"),
        (b'{"title":"A","tags":[]} trailing', "invalid_json"),
        (b"[]", "invalid_json_top_level"),
        (b"\xff", "invalid_utf8"),
    ],
)
def test_sc_016_strict_json_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: bytes, code: str) -> None:
    kb = init_kb(tmp_path, capsys)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    metadata = tmp_path / "metadata.json"
    metadata.write_bytes(payload)
    before = snapshot(kb)
    status, result = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", str(metadata))
    assert status == 3 and result["issues"][0]["code"] == code and snapshot(kb) == before


def test_sc_017_all_initialized_mutations_busy_under_one_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    _, record_dir = add_record(kb, tmp_path, capsys)
    source = tmp_path / "busy-source"
    source.write_bytes(b"x")
    add_metadata = write_json(tmp_path / "busy-add.json", {"title": "Busy", "tags": []})
    edit_metadata = write_json(tmp_path / "busy-edit.json", {"title": "Busy edit", "tags": []})
    tags = write_json(tmp_path / "busy-tags.json", DEFAULT_TAGS)
    layout = write_json(tmp_path / "busy-layout.json", DEFAULT_LAYOUT)
    commands = [
        ("record", "add", "--source", str(source), "--metadata", str(add_metadata)),
        ("record", "edit", "--record", record_dir.name, "--metadata", str(edit_metadata)),
        ("manage", "config", "set", "--profile", "tags", "--file", str(tags)),
        ("manage", "config", "set", "--profile", "layout", "--file", str(layout)),
    ]
    with workspace_lock(kb):
        for tail in commands:
            code, result = invoke(capsys, "--workspace", str(kb), *tail)
            assert code == 5 and result["status"] == "busy" and result["issues"][0]["code"] == "workspace_busy"


def test_sc_018_invalid_profile_replacement_is_nonmutating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    invalid = write_json(tmp_path / "invalid-layout.json", dict(DEFAULT_LAYOUT, max_component_length=15))
    before = snapshot(kb)
    code, _ = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "layout", "--file", str(invalid))
    assert code == 3 and snapshot(kb) == before


def test_sc_019_status_validate_side_effect_free_and_full_issues(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    before = snapshot(kb)
    code, status = invoke(capsys, "--workspace", str(kb), "manage", "status")
    assert code == 0 and status["data"] == {"version": VERSION, "valid": True, "count": 0}
    assert invoke(capsys, "--workspace", str(kb), "manage", "validate")[0] == 0
    assert snapshot(kb) == before
    (kb / "extra").write_bytes(b"x")
    (kb / "profiles" / "extra").write_bytes(b"x")
    invalid_before = snapshot(kb)
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "validate")
    assert code == 3 and len(result["issues"]) >= 2 and snapshot(kb) == invalid_before


def test_sc_020_stdin_metadata_and_config(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    kb = init_kb(tmp_path, capsys)
    tags_payload = json_bytes({"version": 1, "tags": [{"tag": "t", "description": "tag"}]})
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(tags_payload), encoding="utf-8"))
    code, _ = invoke(capsys, "--workspace", str(kb), "manage", "config", "set", "--profile", "tags", "--file", "-")
    assert code == 0
    source = tmp_path / "source"
    source.write_bytes(b"x")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json_bytes({"title": "Stream", "tags": ["t"]})), encoding="utf-8"))
    code, _ = invoke(capsys, "--workspace", str(kb), "record", "add", "--source", str(source), "--metadata", "-")
    assert code == 0


def test_sc_021_end_to_end_validate_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kb = init_kb(tmp_path, capsys)
    set_tags(kb, tmp_path, capsys, ["project"])
    conversion = tmp_path / "conversion-e2e"
    (conversion / "nested").mkdir(parents=True)
    (conversion / "nested" / "page.md").write_text("opaque", encoding="utf-8")
    _, record_dir = add_record(kb, tmp_path, capsys, title="E2E", tags=["project"], conversion=conversion)
    edit = write_json(
        tmp_path / "e2e-edit.json",
        {"title": "E2E renamed", "timestamp": "2026-08-19T12:00:00Z", "tags": ["project"]},
    )
    assert invoke(capsys, "--workspace", str(kb), "record", "edit", "--record", record_dir.name, "--metadata", str(edit))[0] == 0
    code, result = invoke(capsys, "--workspace", str(kb), "manage", "validate")
    assert code == 0 and result["data"] == {"version": VERSION, "valid": True, "count": 1}
    assert list(kb.glob(".cortex*")) == []
