from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import unicodedata
from contextlib import contextmanager
from pathlib import Path

import pytest

from cortex.cli import main
from cortex.constants import DEFAULT_LAYOUT, PUBLIC_ROUTES, RECORD_SCHEMA, VERSION
from cortex.jsonio import json_bytes
from cortex.naming import tag_title_date_name
from cortex.tree import inventory_unit
from cortex.locking import workspace_lock


def invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(["--json", *args]); out = capsys.readouterr(); assert out.err == ""
    result = json.loads(out.out); assert result["exit_code"] == code
    return code, result


def write_json(path: Path, value: object) -> Path:
    path.write_bytes(json_bytes(value)); return path


def tags() -> dict:
    return {"version": 2, "groups": [
        {"name": "project", "tags": [{"tag": "project-summer", "description": "Summer"}, {"tag": "Project-X", "description": "X"}]},
        {"name": "topic", "tags": [{"tag": "research", "description": "Research"}, {"tag": "legal", "description": "Legal"}]},
    ]}


def layout(maximum: int = 96) -> dict:
    return {"version": 3, "unit_name_tag_group": "project", "unit_name_strategy": "tag-title-date", "max_component_length": maximum, "duplicate_name_strategy": "reject"}


def configured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    bundle = tmp_path / "bundle"; assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    for name, value in (("tags", tags()), ("layout", layout())):
        operand = write_json(tmp_path / f"{name}.json", value)
        assert invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", name, "--file", str(operand))[0] == 0
    return bundle


def metadata(path: Path, title: str = "Investment  Memo", tags_value: list[str] | None = None) -> Path:
    return write_json(path, {"title": title, "timestamp": "2026-08-20T12:30:00+08:00", "tags": tags_value or ["project-summer", "research"]})


def conversion(tmp_path: Path, source_name: str = "source.pdf", content: bytes = b"source") -> tuple[Path, Path]:
    source = tmp_path / source_name; source.write_bytes(content)
    root = tmp_path / "conversion"; root.mkdir(); (root / "source.md").write_bytes(b"# converted\n"); (root / "source.json").write_bytes(b'{"opaque":true}\n')
    (root / "src").mkdir(); (root / "src" / source.name).write_bytes(content)
    (root / "assets").mkdir(); (root / "assets" / "image.bin").write_bytes(b"asset")
    return source, root


def add_full(bundle: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], title: str = "Investment  Memo") -> tuple[str, dict]:
    source, conv = conversion(tmp_path)
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conv), "--metadata", str(metadata(tmp_path / "metadata.json", title)))
    assert code == 0, result
    return result["data"]["record"], result


def test_sc001_sc003_full_and_markdown_only_exact_shapes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys)
    assert unit == "project-summer-investment-memo-20260820"
    assert sorted(p.name for p in (bundle / unit).iterdir()) == ["assets", "record.json", "source.json", "source.md", "src"]
    assert (bundle / unit / "src" / "source.pdf").read_bytes() == b"source"
    md = tmp_path / "note.md"; md.write_bytes(b"note")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(md), "--metadata", str(metadata(tmp_path / "m2.json", "Note")))
    assert code == 0 and sorted(p.name for p in (bundle / result["data"]["record"]).iterdir()) == ["note.md", "record.json"]


def test_sc002_sc004_invalid_full_source_only_and_duplicate_are_no_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); source, conv = conversion(tmp_path)
    (conv / "src" / source.name).write_bytes(b"different")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conv), "--metadata", str(metadata(tmp_path / "m.json")))
    assert code == 3 and result["issues"][0]["code"] == "conversion_source_mismatch" and len(list(bundle.iterdir())) == 1
    binary = tmp_path / "x.bin"; binary.write_bytes(b"x")
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(binary), "--metadata", str(metadata(tmp_path / "m2.json")))[1]["issues"][0]["code"] == "invalid_source_only"


def test_full_add_reserves_record_json_before_staging(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys)
    source = tmp_path / "source.pdf"; source.write_bytes(b"source")
    conversion_root = tmp_path / "reserved-conversion"; conversion_root.mkdir()
    (conversion_root / "record.md").write_bytes(b"markdown")
    (conversion_root / "record.json").write_bytes(b'{"converter":true}\n')
    (conversion_root / "src").mkdir(); (conversion_root / "src" / source.name).write_bytes(source.read_bytes())
    before = {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    code, result = invoke(
        capsys,
        "--workspace", str(bundle), "record", "add",
        "--source", str(source), "--conversion", str(conversion_root),
        "--metadata", str(metadata(tmp_path / "reserved-metadata.json")),
    )
    assert code == 3 and result["issues"][0]["code"] == "reserved_record_metadata"
    assert before == {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    assert not list(bundle.glob(".cortex-add-*"))


def test_sc005_sc006_unicode_truncation_casefold_duplicate_and_versions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert VERSION == "6.0.0" and "record.show" in PUBLIC_ROUTES and "record.delete" in PUBLIC_ROUTES
    assert DEFAULT_LAYOUT["version"] == 3 and unicodedata.unidata_version == "14.0.0"
    assert tag_title_date_name("Project-X", " A\tB---C/ D ", "2026-08-20T00:00:00Z", 96) == "Project-X-a-b-c-d-20260820"
    name = tag_title_date_name("project-summer", "界" * 50, "2026-08-20T00:00:00Z", 40)
    assert len(name.encode()) <= 40 and name.endswith("-20260820")
    bundle = configured(tmp_path, capsys); add_full(bundle, tmp_path, capsys)
    source2, conv2 = conversion(tmp_path / "again") if False else (None, None)
    # Exact duplicate is rejected before a second unit or suffix exists.
    source = tmp_path / "source2.pdf"; source.write_bytes(b"source")
    conv = tmp_path / "conversion2"; conv.mkdir(); (conv / "source.md").write_text("x"); (conv / "source.json").write_text("{}"); (conv / "src").mkdir(); (conv / "src" / source.name).write_bytes(b"source")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conv), "--metadata", str(metadata(tmp_path / "dup.json")))
    assert code == 3 and result["issues"][0]["code"] == "duplicate_record_name"
    collision_bundle = tmp_path / "collision-bundle"; assert invoke(capsys, "--workspace", str(collision_bundle), "manage", "init")[0] == 0
    collision_tags = tags(); collision_tags["groups"][0]["tags"].append({"tag": "project-x", "description": "folded collision"})
    assert invoke(capsys, "--workspace", str(collision_bundle), "manage", "config", "set", "--profile", "tags", "--file", str(write_json(tmp_path / "collision-tags.json", collision_tags)))[0] == 0
    collision_code, collision_result = invoke(capsys, "--workspace", str(collision_bundle), "manage", "config", "set", "--profile", "layout", "--file", str(write_json(tmp_path / "collision-layout.json", layout())))
    assert collision_code == 3 and any(item["code"] == "naming_tag_casefold_collision" for item in collision_result["issues"])


def test_sc006_naming_fails_closed_outside_ucd14(monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.naming as naming_module
    monkeypatch.setattr(naming_module.unicodedata, "unidata_version", "15.0.0")
    with pytest.raises(Exception) as caught:
        tag_title_date_name("project-summer", "Title", "2026-08-20T00:00:00Z", 96)
    assert getattr(caught.value, "code", None) == "unsupported_unicode_database"


def test_sc007_null_and_legacy_layout_reject_before_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "b"; assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    md = tmp_path / "a.md"; md.write_text("a"); before = {p.name for p in bundle.iterdir()}
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(md), "--metadata", str(metadata(tmp_path / "m.json", tags_value=[])))
    assert code == 3 and any(i["code"] == "bundle_not_operational" for i in result["issues"]) and {p.name for p in bundle.iterdir()} == before
    legacy = {"version": 2, "partition_by": None, "partition_name_strategy": "tag", "unit_name_strategy": "title-slug", "max_component_length": 96, "duplicate_name_strategy": "reject"}
    code, result = invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", "layout", "--file", str(write_json(tmp_path / "old.json", legacy)))
    assert code == 3 and any(i["code"] == "invalid_profile_version" for i in result["issues"])


def test_sc010_sc011_identity_rejected_and_non_naming_edit_preserves_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys)
    unit_path = bundle / unit
    payload_before = {
        path.relative_to(unit_path).as_posix(): path.read_bytes()
        for path in unit_path.rglob("*")
        if path.is_file() and path.name != "record.json"
    }
    payload_hashes_before = {name: hashlib.sha256(value).hexdigest() for name, value in payload_before.items()}
    changed = metadata(tmp_path / "edit.json", tags_value=["project-summer", "legal"])
    assert invoke(capsys, "--workspace", str(bundle), "record", "edit", "--record", unit, "--metadata", str(changed))[0] == 0
    payload_after = {
        path.relative_to(unit_path).as_posix(): path.read_bytes()
        for path in unit_path.rglob("*")
        if path.is_file() and path.name != "record.json"
    }
    assert payload_after == payload_before
    assert {name: hashlib.sha256(value).hexdigest() for name, value in payload_after.items()} == payload_hashes_before
    identity_attempts = [
        {"title": "Changed", "timestamp": "2026-08-20T12:30:00+08:00", "tags": ["project-summer", "legal"]},
        {"title": "Investment  Memo", "timestamp": "2026-08-21T12:30:00+08:00", "tags": ["project-summer", "legal"]},
        {"title": "Investment  Memo", "timestamp": "2026-08-20T12:30:00+08:00", "tags": ["Project-X", "legal"]},
    ]
    accepted_record = (unit_path / "record.json").read_bytes()
    for index, candidate in enumerate(identity_attempts):
        bad = write_json(tmp_path / f"identity-{index}.json", candidate)
        code, result = invoke(capsys, "--workspace", str(bundle), "record", "edit", "--record", unit, "--metadata", str(bad))
        assert code == 3 and result["issues"][0]["code"] == "record_identity_change_forbidden"
        assert (unit_path / "record.json").read_bytes() == accepted_record
    code, result = invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", "layout", "--file", str(write_json(tmp_path / "layout2.json", layout(100))))
    assert code == 3 and result["issues"][0]["code"] == "layout_change_forbidden"


def independent_digest(unit: Path) -> str:
    name = unit.name.encode(); h = hashlib.sha256(b"CORTEX_UNIT_TREE_V1\0" + len(name).to_bytes(8, "big") + name)
    paths = sorted([p for p in unit.rglob("*")], key=lambda p: p.relative_to(unit).as_posix().encode())
    for path in paths:
        raw = path.relative_to(unit).as_posix().encode()
        if path.is_dir(): h.update(b"D" + len(raw).to_bytes(8, "big") + raw)
        else:
            content = path.read_bytes(); h.update(b"F" + len(raw).to_bytes(8, "big") + raw + len(content).to_bytes(8, "big") + content)
    return h.hexdigest()


def test_sc012_sc016_token_show_and_exact_delete_semantics(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys)
    note = tmp_path / "second.md"; note.write_bytes(b"second")
    second_code, second_result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(note), "--metadata", str(metadata(tmp_path / "second-metadata.json", "Second")))
    assert second_code == 0; second_unit = second_result["data"]["record"]
    code, shown = invoke(capsys, "--workspace", str(bundle), "record", "show", "--record", unit)
    assert code == 0 and shown["data"]["tree_sha256"] == independent_digest(bundle / unit)
    raw_record = (bundle / unit / "record.json").read_bytes()
    assert shown["data"]["record_json_sha256"] == hashlib.sha256(raw_record).hexdigest()
    assert shown["data"]["metadata"] == json.loads(raw_record)
    bad = invoke(capsys, "--workspace", str(bundle), "record", "show", "--record", f"x/{unit}")[1]
    assert bad["issues"][0]["code"] == "invalid_record_operand"
    malformed_code, malformed = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", "NOT-A-DIGEST")
    assert malformed_code == 3 and malformed["issues"][0]["code"] == "invalid_expected_tree_sha256" and (bundle / unit).exists()
    mismatch_code, mismatch = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", "0" * 64)
    assert mismatch_code == 3 and mismatch["issues"][0]["code"] == "tree_digest_mismatch" and (bundle / unit).exists()
    stale = shown["data"]["tree_sha256"]; (bundle / unit / "source.md").write_bytes(b"changed")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", stale)
    assert code == 3 and result["issues"][0]["code"] == "tree_digest_mismatch" and (bundle / unit).exists()
    current = inventory_unit(bundle / unit, unit).sha256
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", current)
    assert code == 0, result
    assert not (bundle / unit).exists() and (bundle / second_unit).is_dir() and not list(bundle.glob(".cortex-*"))


def test_sc018_show_and_delete_share_nonblocking_writer_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys); token = inventory_unit(bundle / unit, unit).sha256
    with workspace_lock(bundle):
        for command in (("show",), ("delete", "--expected-tree-sha256", token)):
            code, result = invoke(capsys, "--workspace", str(bundle), "record", command[0], "--record", unit, *command[1:])
            assert code == 5 and result["status"] == "busy"
    assert (bundle / unit).is_dir()


def test_sc017_show_revalidates_after_writer_lock_is_acquired(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.service as service_module
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys); unit_path = bundle / unit
    real_writer_lock = service_module.writer_lock
    @contextmanager
    def mutate_after_lock(path: Path):
        with real_writer_lock(path) as stream:
            (unit_path / "record.json").write_bytes(b"{}\n")
            yield stream
    monkeypatch.setattr(service_module, "writer_lock", mutate_after_lock)
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "show", "--record", unit)
    assert code == 3 and result["status"] == "validation_error" and result["data"] == {}
    assert any(item["code"] in {"missing_field", "record_name_mismatch"} for item in result["issues"])


def test_sc020_sc021_after_start_delete_failure_reports_partial_without_recovery(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.service as service_module
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys); token = inventory_unit(bundle / unit, unit).sha256
    real_unlink = service_module.os.unlink; calls = 0
    def fail_second(path: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2: raise PermissionError("injected after start")
        real_unlink(path)
    monkeypatch.setattr(service_module.os, "unlink", fail_second)
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", token)
    assert code == 6 and result["issues"][0]["code"] == "delete_incomplete"
    assert result["data"]["record"] == unit and result["data"]["partial"] is True
    assert result["data"]["first_failed_relative_path"] not in {None, ""} and result["data"]["remaining_entry_count"] >= 1
    root_names = [path.name.casefold() for path in bundle.iterdir()]
    assert not any(word in name for name in root_names for word in ("trash", "journal", "tombstone", "recovery"))
    assert not list(bundle.glob(".cortex-delete-*"))


def test_sc019_unsafe_link_rejects_without_touching_external_sentinel(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys)
    sentinel = tmp_path / "external.txt"; sentinel.write_bytes(b"sentinel")
    link = bundle / unit / "assets" / "escape"
    try:
        os.symlink(sentinel, link)
    except (OSError, NotImplementedError):
        import cortex.validation as validation_module
        link.write_bytes(b"simulated-reparse")
        real_check = validation_module.is_reparse_metadata
        monkeypatch.setattr(validation_module, "is_reparse_metadata", lambda meta: meta.st_size == len(b"simulated-reparse") or real_check(meta))
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "show", "--record", unit)
    assert code == 3 and any(i["code"] == "reparse_path" for i in result["issues"])
    assert sentinel.read_bytes() == b"sentinel"


def test_sc019_nonregular_unit_entry_rejected_with_external_sentinel_intact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys)
    sentinel = tmp_path / "external-nonregular.bin"; sentinel.write_bytes(b"outside")
    shutil.rmtree(bundle / unit); (bundle / unit).write_bytes(b"not-a-unit-directory")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "show", "--record", unit)
    assert code == 3 and any(item["code"] == "real_directory_required" for item in result["issues"])
    assert sentinel.read_bytes() == b"outside"


def test_sc019_unsafe_conversion_component_rejects_with_external_sentinel_intact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = configured(tmp_path, capsys); source, conversion_root = conversion(tmp_path)
    sentinel = tmp_path / "external-unsafe.bin"; sentinel.write_bytes(b"outside")
    (conversion_root / "assets" / ".cortex-unsafe").write_bytes(b"unsafe")
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conversion_root), "--metadata", str(metadata(tmp_path / "unsafe-metadata.json")))
    assert code == 3 and any(item["code"] == "reserved_staging_name" for item in result["issues"])
    assert sentinel.read_bytes() == b"outside" and len(list(bundle.iterdir())) == 1


def test_sc020_secondary_residue_unreadable_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.service as service_module
    bundle = configured(tmp_path, capsys); unit, _ = add_full(bundle, tmp_path, capsys); token = inventory_unit(bundle / unit, unit).sha256
    monkeypatch.setattr(service_module.os, "unlink", lambda _path: (_ for _ in ()).throw(PermissionError("primary")))
    monkeypatch.setattr(service_module, "_residue_count", lambda _path: (_ for _ in ()).throw(OSError("secondary")))
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--record", unit, "--expected-tree-sha256", token)
    assert code == 6 and [i["code"] for i in result["issues"]] == ["delete_incomplete", "residue_unreadable"]
    assert result["data"]["remaining_entry_count"] is None


def _load_migration():
    path = Path(__file__).parents[1] / "tools" / "migrate_legacy_layout3.py"
    spec = importlib.util.spec_from_file_location("migration", path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


def test_migration_script_binds_repository_cortex_over_ambient_package(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    fake_root = tmp_path / "ambient"; fake_package = fake_root / "cortex"; fake_package.mkdir(parents=True)
    imported = tmp_path / "ambient-imported"
    (fake_package / "__init__.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(imported)) + ").write_text('ambient')\n",
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment["PYTHONPATH"] = str(fake_root)
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "migrate_legacy_layout3.py"), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "{plan,build}" in completed.stdout and not imported.exists()


def legacy_unit(root: Path, title: str, *, full: bool) -> None:
    unit = root / title; unit.mkdir(parents=True); (unit / "record.json").write_bytes(json_bytes({"title": title, "timestamp": "2026-08-20T00:00:00Z", "tags": ["project-summer", "research"]}))
    (unit / "original").mkdir(); suffix = ".pdf" if full else ".md"; (unit / "original" / f"source{suffix}").write_bytes(b"bytes")
    if full:
        conv = unit / "representations" / "markdown-conversion"; conv.mkdir(parents=True); (conv / "source.md").write_bytes(b"md"); (conv / "source.json").write_bytes(b"json"); (conv / "src").mkdir(); (conv / "src" / "source.pdf").write_bytes(b"bytes")


class _FixedUuid:
    def __init__(self, value: str) -> None:
        self.hex = value


def test_migration_stage_collision_never_removes_preexisting_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    tags_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    _body, _raw, digest = migration.plan_legacy(source, tags_path, layout_path)
    collision_hex = "1" * 32; success_hex = "2" * 32
    collision = tmp_path / f".cortex-mig-{collision_hex}"; collision.mkdir(); sentinel = collision / "sentinel.bin"; sentinel.write_bytes(b"preexisting-stage")
    values = iter([_FixedUuid(collision_hex), _FixedUuid(success_hex)])
    monkeypatch.setattr(migration.uuid, "uuid4", lambda: next(values))
    output = tmp_path / "built"
    assert migration.build_legacy(source, output, tags_path, layout_path, digest)["output"] == str(output)
    assert sentinel.read_bytes() == b"preexisting-stage" and sorted(path.name for path in collision.iterdir()) == ["sentinel.bin"]
    monkeypatch.setattr(migration.uuid, "uuid4", lambda: _FixedUuid(collision_hex))
    blocked_output = tmp_path / "blocked"
    with pytest.raises(Exception) as caught:
        migration.build_legacy(source, blocked_output, tags_path, layout_path, digest)
    assert getattr(caught.value, "code", None) == "migration_stage_collision"
    assert not blocked_output.exists() and sentinel.read_bytes() == b"preexisting-stage"
    assert not (tmp_path / f".cortex-mig-{success_hex}").exists()


def _replace_with_link_or_simulated_reparse(
    migration: object,
    target: Path,
    external: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    try:
        os.symlink(external, target, target_is_directory=external.is_dir())
        return
    except (OSError, NotImplementedError):
        if external.is_dir():
            target.mkdir()
        else:
            target.write_bytes(b"simulated-migration-reparse")
        stamp = 1_000_000_000_123_456_700
        os.utime(target, ns=(stamp, stamp))
        target_meta = os.lstat(target)
        signature = (stat.S_IFMT(target_meta.st_mode), target_meta.st_size, target_meta.st_mtime_ns)
        real_check = migration.is_reparse_metadata
        import cortex.native as native_module
        native_real_check = native_module.is_reparse_metadata
        monkeypatch.setattr(
            migration,
            "is_reparse_metadata",
            lambda meta: (stat.S_IFMT(meta.st_mode), meta.st_size, meta.st_mtime_ns) == signature or real_check(meta),
        )
        monkeypatch.setattr(
            native_module,
            "is_reparse_metadata",
            lambda meta: (stat.S_IFMT(meta.st_mode), meta.st_size, meta.st_mtime_ns) == signature or native_real_check(meta),
        )


@pytest.mark.parametrize("operand", ["tags", "layout"])
def test_profile_operand_leaf_reparse_rejected_before_external_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operand: str,
) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    tags_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    target = tags_path if operand == "tags" else layout_path
    external = tmp_path / f"external-{operand}.json"; external.write_bytes(target.read_bytes()); before = external.read_bytes()
    _replace_with_link_or_simulated_reparse(migration, target, external, monkeypatch)
    with pytest.raises(Exception) as caught:
        migration.plan_legacy(source, tags_path, layout_path)
    assert getattr(caught.value, "code", None) == "reparse_path" and external.read_bytes() == before


def test_profile_operand_reparse_ancestor_rejected_before_external_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    external = tmp_path / "external-profiles"; external.mkdir(); external_tags = write_json(external / "tags.json", tags()); before = external_tags.read_bytes()
    linked = tmp_path / "linked-profiles"; linked.mkdir()
    _replace_with_link_or_simulated_reparse(migration, linked, external, monkeypatch)
    layout_path = write_json(tmp_path / "layout.json", layout())
    with pytest.raises(Exception) as caught:
        migration.plan_legacy(source, linked / "tags.json", layout_path)
    assert getattr(caught.value, "code", None) == "reparse_path" and external_tags.read_bytes() == before


@pytest.mark.parametrize(
    "kind,expected_code",
    [("nonregular", "profile_operand_not_ordinary"), ("unreadable", "profile_operand_unreadable")],
)
def test_profile_operand_failures_use_validation_cli_envelope_without_traceback(tmp_path: Path, kind: str, expected_code: str) -> None:
    root = Path(__file__).parents[1]; source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    layout_path = write_json(tmp_path / "layout.json", layout())
    tags_operand = tmp_path / "tags-directory" if kind == "nonregular" else tmp_path / "missing-tags.json"
    if kind == "nonregular": tags_operand.mkdir()
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "migrate_legacy_layout3.py"), "plan", "--source", str(source), "--tags", str(tags_operand), "--layout", str(layout_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 3 and completed.stderr == "" and payload["status"] == "validation_error"
    assert payload["issues"][0]["code"] == expected_code and "Traceback" not in completed.stdout


@pytest.mark.parametrize("location", ["record.json", "original", "representations"])
def test_migration_reparse_boundaries_never_read_external_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=True)
    external = tmp_path / f"external-{location.replace('.', '-')}"
    if location == "record.json":
        external.write_bytes(json_bytes({"title": "Outside", "timestamp": "2026-08-20T00:00:00Z", "tags": ["project-summer", "research"]}))
    else:
        external.mkdir(); (external / "sentinel.bin").write_bytes(b"outside-sentinel")
    before = {path.relative_to(external).as_posix(): path.read_bytes() for path in external.rglob("*") if path.is_file()} if external.is_dir() else {".": external.read_bytes()}
    target = source / "Alpha" / location
    _replace_with_link_or_simulated_reparse(migration, target, external, monkeypatch)
    reads: list[str] = []
    real_sha = migration._sha; real_read_json = migration._read_json
    def outside(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        root = external.resolve()
        return resolved == root or root in resolved.parents
    def guarded_sha(path: Path) -> str:
        if outside(path): reads.append(str(path)); raise AssertionError("external sentinel read")
        return real_sha(path)
    def guarded_json(path: Path):
        if outside(path): reads.append(str(path)); raise AssertionError("external sentinel read")
        return real_read_json(path)
    monkeypatch.setattr(migration, "_sha", guarded_sha); monkeypatch.setattr(migration, "_read_json", guarded_json)
    tags_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    body1, raw1, digest1 = migration.plan_legacy(source, tags_path, layout_path)
    body2, raw2, digest2 = migration.plan_legacy(source, tags_path, layout_path)
    assert raw1 == raw2 and digest1 == digest2 and body1 == body2
    assert any(item["code"] == "reparse_path" for item in body1["issues"]) and not body1["mappings"]
    assert reads == []
    after = {path.relative_to(external).as_posix(): path.read_bytes() for path in external.rglob("*") if path.is_file()} if external.is_dir() else {".": external.read_bytes()}
    assert after == before


def test_sc022_sc025_sc027_migration_plan_and_full_markdown_lifts(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=True); legacy_unit(source, "Beta", full=False)
    tag_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    body1, raw1, digest1 = migration.plan_legacy(source, tag_path, layout_path); body2, raw2, digest2 = migration.plan_legacy(source, tag_path, layout_path)
    assert raw1 == raw2 and digest1 == digest2 and "digest" not in body1 and body1["counts"] == {"total": 2, "full": 1, "markdown_only": 1}
    assert body1["profiles"] == {"record": RECORD_SCHEMA, "tags": tags(), "layout": layout()}
    assert set(body1["config_digests"]) == {"record_schema_sha256", "tags_sha256", "layout_sha256"}
    assert before == {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(Exception): migration.build_legacy(source, tmp_path / "bad", tag_path, layout_path, "0" * 64)
    output = tmp_path / "built"; result = migration.build_legacy(source, output, tag_path, layout_path, digest1)
    assert result["counts"]["total"] == 2 and (output / "profiles").is_dir() and not any(p.name in {"original", "representations"} for p in output.rglob("*"))
    full_mapping = next(item for item in body1["mappings"] if item["kind"] == "full")
    markdown_mapping = next(item for item in body1["mappings"] if item["kind"] == "markdown-only")
    assert sorted(path.name for path in (output / full_mapping["target_unit"]).iterdir()) == ["record.json", "source.json", "source.md", "src"]
    assert sorted(path.name for path in (output / markdown_mapping["target_unit"]).iterdir()) == ["record.json", "source.md"]
    assert before == {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_sc023_migration_aggregates_errors_and_creates_no_output(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir()
    (source / "bad-a").mkdir(); (source / "bad-b").mkdir()
    tag_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    body, raw, digest = migration.plan_legacy(source, tag_path, layout_path)
    repeated_body, repeated_raw, repeated_digest = migration.plan_legacy(source, tag_path, layout_path)
    assert (body, raw, digest) == (repeated_body, repeated_raw, repeated_digest)
    assert len(body["issues"]) == 2 and [i["path"] for i in body["issues"]] == ["bad-a", "bad-b"]
    assert hashlib.sha256(raw).hexdigest() == digest and not (tmp_path / "output").exists()
    with pytest.raises(Exception): migration.build_legacy(source, tmp_path / "output", tag_path, layout_path, digest)
    assert not (tmp_path / "output").exists()


def test_sc026_migration_mismatch_blocks_before_output(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=True)
    (source / "Alpha" / "representations" / "markdown-conversion" / "src" / "source.pdf").write_bytes(b"mismatch")
    tags_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    body, _raw, digest = migration.plan_legacy(source, tags_path, layout_path)
    assert any(item["code"] == "legacy_source_mismatch" for item in body["issues"]) and not body["mappings"]
    output = tmp_path / "output"
    with pytest.raises(Exception): migration.build_legacy(source, output, tags_path, layout_path, digest)
    assert not output.exists() and not list(tmp_path.glob(".cortex-mig-*"))


def test_migration_reserves_record_json_in_converter_payload_before_output(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=True)
    conversion_root = source / "Alpha" / "representations" / "markdown-conversion"
    (conversion_root / "source.md").rename(conversion_root / "record.md")
    (conversion_root / "source.json").rename(conversion_root / "record.json")
    tags_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    before = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    body, _raw, digest = migration.plan_legacy(source, tags_path, layout_path)
    assert any(item["code"] == "reserved_record_metadata" for item in body["issues"]) and not body["mappings"]
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools" / "migrate_legacy_layout3.py"),
            "plan", "--source", str(source), "--tags", str(tags_path), "--layout", str(layout_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    cli_result = json.loads(completed.stdout)
    assert completed.returncode == 3 and cli_result["status"] == "validation_error"
    assert any(item["code"] == "reserved_record_metadata" for item in cli_result["issues"])
    output = tmp_path / "output"
    with pytest.raises(Exception) as caught:
        migration.build_legacy(source, output, tags_path, layout_path, digest)
    assert getattr(caught.value, "code", None) == "migration_plan_invalid" and getattr(caught.value, "status", None).value == "validation_error"
    assert not output.exists() and not list(tmp_path.glob(".cortex-mig-*"))
    assert before == {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}


def test_sc023_migration_requires_operational_layout_before_output(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    tag_path = write_json(tmp_path / "tags.json", tags()); null_layout = dict(layout()); null_layout["unit_name_tag_group"] = None
    layout_path = write_json(tmp_path / "layout.json", null_layout)
    with pytest.raises(Exception) as caught:
        migration.plan_legacy(source, tag_path, layout_path)
    assert getattr(caught.value, "code", None) == "bundle_not_operational"
    output = tmp_path / "output"
    with pytest.raises(Exception): migration.build_legacy(source, output, tag_path, layout_path, "0" * 64)
    assert not output.exists()


def test_sc024_migration_rejects_source_output_overlap(tmp_path: Path) -> None:
    migration = _load_migration(); source = tmp_path / "legacy"; source.mkdir(); legacy_unit(source, "Alpha", full=False)
    tag_path = write_json(tmp_path / "tags.json", tags()); layout_path = write_json(tmp_path / "layout.json", layout())
    _body, _raw, digest = migration.plan_legacy(source, tag_path, layout_path)
    with pytest.raises(Exception): migration.build_legacy(source, source / "nested-output", tag_path, layout_path, digest)
    assert not (source / "nested-output").exists()


def test_sc028_sc029_public_surface_and_exact_pilot_gate() -> None:
    root = Path(__file__).parents[1]
    assert not any("migrate" in route for route in PUBLIC_ROUTES)
    pyproject = (root / "pyproject.toml").read_text("utf-8"); assert 'version = "6.0.0"' in pyproject and 'requires-python = ">=3.11,<3.12"' in pyproject
    assert "[project.scripts]" not in pyproject and "cortex =" not in pyproject and "migrat" not in pyproject.lower()
    combined = "\n".join((root / name).read_text("utf-8") for name in ["README.md", "docs/global-knowledge.md", "docs/record-kb-architecture.md", "docs/verification-matrix.md", "skills/cortex-build/SKILL.md", "skills/cortex-manage/SKILL.md"])
    for text in ("exactly 27", "25 full", "2 Markdown-only", "record delete", "tree_sha256", "6.0.0", "project-summer"):
        assert text in combined


def test_sc030_sc031_surface_fixture_skills_and_runtime_gate_agree() -> None:
    root = Path(__file__).parents[1]; fixture = json.loads((root / "fixtures/capabilities/cortex6-surface.json").read_text("utf-8"))
    assert fixture["version"] == VERSION and fixture["routes"] == list(PUBLIC_ROUTES) and fixture["public_migration_route"] is False
    assert fixture["global_command"] is False and fixture["skill_runtime"]["payloads_byte_identical"] is True
    for skill in ("cortex-build", "cortex-manage"):
        text = (root / "skills" / skill / "SKILL.md").read_text("utf-8")
        assert "cortex 6.0.0" in text and "ABSOLUTE-PYTHON-3.11" in text and "scripts/run_cortex.py" in text
        assert "do not fall back" in text.lower() and "CORTEX-ABSOLUTE-EXECUTABLE" not in text


def test_sc032_one_to_one_evalspec_mapping_has_exact_frozen_semantics() -> None:
    expected = {
        "sc001": "Full unit has the exact flat shape and preserves accepted payload names and bytes.",
        "sc002": "Malformed full conversion is rejected without mutation.",
        "sc003": "Markdown-only input produces the exact two-file unit.",
        "sc004": "Non-Markdown source-only input is rejected without mutation.",
        "sc005": "Direct unit naming follows exact tag-title-date semantics.",
        "sc006": "Unicode normalization, case folding, whole-codepoint truncation, and maximum-byte handling are deterministic under Python 3.11/UCD 14.0.0.",
        "sc007": "Exact and case-fold duplicate unit names are rejected without a suffix.",
        "sc008": "Layout 2 and legacy partition behavior are rejected without fallback.",
        "sc009": "Distribution, Python range, product version, and profile versions equal the Cortex 6 contract.",
        "sc010": "Edits to title, full timestamp, or selected naming tag are rejected.",
        "sc011": "A non-naming tag edit succeeds while every payload byte and payload SHA-256 remains unchanged.",
        "sc012": "The unit-tree token is independently reproducible from the normative grammar.",
        "sc013": "The unit-tree token changes when an authorized path or byte changes.",
        "sc014": "`record show` accepts only one exact safe unit component and returns metadata from the hashed `record.json`.",
        "sc015": "A matching tree token deletes exactly the selected single record.",
        "sc016": "Malformed, mismatched, and stale tree tokens reject without deletion.",
        "sc017": "Show/delete acquire the writer lock before Registry refresh and Bundle/unit revalidation.",
        "sc018": "Lock contention for show/delete returns `busy/5` without mutation.",
        "sc019": "Unsafe components, links/reparse points, and nonregular entries reject while an external sentinel remains unchanged.",
        "sc020": "An after-start delete failure reports primary `delete_incomplete`, and residue-scan failure adds secondary `residue_unreadable` with the exact partial fields.",
        "sc021": "Delete creates no stage, trash, journal, tombstone, or recovery artifact.",
        "sc022": "Migration planning is deterministic and source-read-only and embeds explicit canonical Record 1, Tag 2, and Layout 3 profiles.",
        "sc023": "Migration aggregates a deterministic error set before creating output.",
        "sc024": "Migration build publishes only a separate absent output and leaves every source byte unchanged.",
        "sc025": "Equality-gated full legacy units lift to the exact flat Cortex 6 shape.",
        "sc026": "Original/conversion-source mismatch blocks migration before output.",
        "sc027": "Markdown-only legacy units lift to the exact Cortex 6 Markdown-only shape.",
        "sc028": "No public migration route or installed migration entry point exists.",
        "sc029": "The project-summer pilot gate requires exactly 27 total, 25 full, and 2 Markdown-only records and stops on reproducible drift.",
        "sc030": "Both repository skills carry one exact pinned Cortex 6.0.0 runtime and forbid global-command fallback.",
        "sc031": "Implementation, AGENTS, documentation, both skills, and the capability fixture agree on the complete contract.",
        "sc032": "Full pytest, compileall, and one-to-one sc001-sc032 semantic mapping gates pass.",
    }
    matrix = (Path(__file__).parents[1] / "docs/verification-matrix.md").read_text("utf-8")
    actual: dict[str, str] = {}
    for line in matrix.splitlines():
        if line.startswith("| sc"):
            cells = [cell.strip() for cell in line.split("|")]
            actual[cells[1]] = cells[2]
    assert actual == expected
