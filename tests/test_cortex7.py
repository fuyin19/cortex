from __future__ import annotations
import hashlib, importlib.util, json, os, shutil, stat
from contextlib import contextmanager
from pathlib import Path
import pytest
from cortex.cli import main
from cortex.errors import CortexError, Status
from cortex.constants import PUBLIC_ROUTES, VERSION
from cortex.jsonio import json_bytes
from cortex.tree import inventory_unit
from cortex.validation import validate_workspace

PARTITION = "project-alpha"
def tags(*extra: str) -> dict:
    return {"version": 2, "groups": [{"name": "project", "tags": [{"tag": x, "description": x} for x in (PARTITION, "project-beta")]}, {"name": "kind", "tags": [{"tag": x, "description": x} for x in ("research", *extra)]}]}
def layout(group: str | None = "project", maximum: int = 96) -> dict:
    return {"version": 5, "partition_tag_group": group, "partition_name_strategy": "tag", "unit_name_strategy": "tag-title-date", "max_component_length": maximum, "duplicate_name_strategy": "reject"}
def write(path: Path, value: dict) -> Path: path.write_bytes(json_bytes(value)); return path
def invoke(capsys, *args: str):
    code = main(["--json", *args]); out = json.loads(capsys.readouterr().out); return code, out
def configured(tmp_path: Path, capsys) -> Path:
    bundle = tmp_path / "bundle"; assert invoke(capsys, "--workspace", str(bundle), "manage", "init")[0] == 0
    for name, value in (("tags", tags()), ("layout", layout())):
        assert invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", name, "--file", str(write(tmp_path / f"{name}.json", value)))[0] == 0
    return bundle
def add_md(bundle: Path, tmp_path: Path, capsys, *, project: str = PARTITION, title: str = "Alpha memo", filename: str = "memo.md"):
    source = tmp_path / filename; source.write_bytes(b"# exact\n")
    meta = write(tmp_path / f"{project}-{title[:2]}.json", {"title": title, "timestamp": "2026-08-21T00:00:00Z", "tags": [project, "research"]})
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--metadata", str(meta)); assert code == 0
    return result["data"]["partition"], result["data"]["record"]

def test_sc001_sc003_sc005_layout4_add_exact_shape_new_and_existing_partition(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); partition, first = add_md(bundle, tmp_path, capsys)
    _, second = add_md(bundle, tmp_path, capsys, title="Second memo", filename="second.md")
    assert partition == PARTITION and first == "project-alpha-alpha-memo-20260821"
    assert sorted(x.name for x in bundle.iterdir()) == ["profiles", PARTITION]
    assert sorted(x.name for x in (bundle / PARTITION).iterdir()) == [first, second]
    assert sorted(x.name for x in (bundle / PARTITION / first).iterdir()) == ["AGENTS.md", "CLAUDE.md", "assets", "memo.md", "record.json", "src"]
    assert (bundle / PARTITION / first / "assets/.keep").read_bytes() == b""
    assert (bundle / PARTITION / first / "src/.keep").read_bytes() == b""
    assert validate_workspace(bundle).valid and validate_workspace(bundle).count == 2

def test_sc001_sc002_full_conversion_bytes_stems_assets_and_zero_mutation(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); source = tmp_path / "memo.pdf"; source.write_bytes(b"exact-source")
    conversion = tmp_path / "conversion"; conversion.mkdir(); (conversion / "memo.md").write_bytes(b"exact-md"); (conversion / "memo.json").write_bytes(b"exact-json"); (conversion / "src").mkdir(); (conversion / "src/memo.pdf").write_bytes(source.read_bytes()); (conversion / "assets/opaque").mkdir(parents=True); (conversion / "assets/opaque/data.bin").write_bytes(b"opaque")
    metadata = write(tmp_path / "full.json", {"title": "Full memo", "timestamp": "2026-08-21T00:00:00Z", "tags": [PARTITION]})
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conversion), "--metadata", str(metadata)); assert code == 0
    unit = bundle / result["data"]["partition"] / result["data"]["record"]
    for relative in ("memo.md", "memo.json", "src/memo.pdf", "assets/opaque/data.bin"): assert (unit / relative).read_bytes() == (conversion / relative).read_bytes()
    before = {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    malformed = tmp_path / "malformed"; malformed.mkdir(); (malformed / "memo.md").write_bytes(b"md"); (malformed / "other.json").write_bytes(b"{}\n"); (malformed / "src").mkdir(); (malformed / "src/memo.pdf").write_bytes(source.read_bytes())
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(malformed), "--metadata", str(metadata))[0] == 3
    hidden = tmp_path / "hidden"; shutil.copytree(conversion, hidden); (hidden / "assets/.cortex").mkdir(); (hidden / "assets/.cortex/secret").write_bytes(b"bad")
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(hidden), "--metadata", str(metadata))[1]["issues"][0]["code"] == "reserved_cortex_name"
    wrong_stem = tmp_path / "wrong-stem"; shutil.copytree(conversion, wrong_stem); (wrong_stem / "src/memo.pdf").rename(wrong_stem / "src/other.pdf")
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(wrong_stem), "--metadata", str(metadata))[1]["issues"][0]["code"] == "conversion_source_mismatch"
    assert before == {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    (unit / "assets/.cortex").mkdir(); (unit / "assets/.cortex/secret").write_bytes(b"bad")
    with pytest.raises(Exception) as inventory_error: inventory_unit(unit, PARTITION, unit.name)
    assert getattr(inventory_error.value, "code", None) == "reserved_cortex_name"

def test_sc002_sc004_sc007_add_rejections_are_no_write(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); add_md(bundle, tmp_path, capsys); before = sorted(str(x.relative_to(bundle)) for x in bundle.rglob("*"))
    source = tmp_path / "bad"; source.write_bytes(b"no extension"); meta = write(tmp_path / "bad.json", {"title": "Bad", "timestamp": "2026-08-21T00:00:00Z", "tags": [PARTITION]})
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--metadata", str(meta))[0] == 3
    duplicate = tmp_path / "dupe.md"; duplicate.write_bytes(b"changed")
    meta = write(tmp_path / "dupe.json", {"title": "Alpha memo", "timestamp": "2026-08-21T00:00:00Z", "tags": [PARTITION, "research"]})
    assert invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(duplicate), "--metadata", str(meta))[1]["issues"][0]["code"] == "duplicate_record_name"
    assert before == sorted(str(x.relative_to(bundle)) for x in bundle.rglob("*"))

def test_sc006_sc008_sc009_profile_contract_and_empty_operability(tmp_path, capsys):
    assert VERSION == "8.1.1"
    bundle = tmp_path / "b"; invoke(capsys, "--workspace", str(bundle), "manage", "init")
    assert json.loads((bundle / "profiles/layout.json").read_text())["version"] == 5
    old = {"version": 3, "unit_name_tag_group": "project", "unit_name_strategy": "tag-title-date", "max_component_length": 96, "duplicate_name_strategy": "reject"}
    code, result = invoke(capsys, "--workspace", str(bundle), "manage", "config", "set", "--profile", "layout", "--file", str(write(tmp_path / "old.json", old)))
    assert code == 3 and result["issues"][0]["code"] in {"unknown_field", "missing_field"}

def test_sc006_naming_runtime_fails_closed_on_python_or_ucd(monkeypatch):
    import cortex.naming as naming
    monkeypatch.setattr(naming.sys, "version_info", (3, 10, 0))
    with pytest.raises(Exception) as python_error: naming.require_naming_runtime()
    assert getattr(python_error.value, "code", None) == "unsupported_python_version"
    monkeypatch.setattr(naming.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(naming.unicodedata, "unidata_version", "15.0.0")
    with pytest.raises(Exception) as ucd_error: naming.require_naming_runtime()
    assert getattr(ucd_error.value, "code", None) == "unsupported_unicode_database"
    assert naming.truncate_utf8("a😀b", 5) == "a😀" and naming.truncate_utf8("a😀b", 4) == "a"

@pytest.mark.parametrize("changed_field", ["title", "timestamp", "partition_tag"])
def test_sc010_sc011_edit_requires_pair_and_preserves_payload(tmp_path, capsys, changed_field):
    bundle = configured(tmp_path, capsys); partition, unit = add_md(bundle, tmp_path, capsys); payload = (bundle / partition / unit / "memo.md").read_bytes()
    changed = write(tmp_path / "edit.json", {"title": "Alpha memo", "timestamp": "2026-08-21T00:00:00Z", "tags": [partition]})
    assert invoke(capsys, "--workspace", str(bundle), "record", "edit", "--partition", partition, "--record", unit, "--metadata", str(changed))[0] == 0
    identity_value = {"title": "Alpha memo", "timestamp": "2026-08-21T00:00:00Z", "tags": [partition]}
    if changed_field == "title": identity_value["title"] = "Changed"
    elif changed_field == "timestamp": identity_value["timestamp"] = "2026-08-22T00:00:00Z"
    else: identity_value["tags"] = ["project-beta"]
    identity = write(tmp_path / "identity.json", identity_value)
    assert invoke(capsys, "--workspace", str(bundle), "record", "edit", "--partition", partition, "--record", unit, "--metadata", str(identity))[1]["issues"][0]["code"] == "record_identity_change_forbidden"
    assert (bundle / partition / unit / "memo.md").read_bytes() == payload

def independent(partition: str, unit: str, root: Path) -> str:
    def u64(n: int): return n.to_bytes(8, "big")
    digest = hashlib.sha256(b"CORTEX_UNIT_TREE_V2\0"); p = partition.encode(); u = unit.encode(); digest.update(u64(len(p)) + p + u64(len(u)) + u)
    manifest = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix(); manifest.append((rel, path))
    for rel, path in sorted(manifest, key=lambda x: x[0].encode()):
        raw = rel.encode()
        if path.is_dir(): digest.update(b"D" + u64(len(raw)) + raw)
        else: digest.update(b"F" + u64(len(raw)) + raw + u64(path.stat().st_size) + path.read_bytes())
    return digest.hexdigest()

def test_sc012_sc013_sc014_v2_digest_and_exact_show(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); partition, unit = add_md(bundle, tmp_path, capsys); root = bundle / partition / unit
    code, shown = invoke(capsys, "--workspace", str(bundle), "record", "show", "--partition", partition, "--record", unit)
    assert code == 0 and shown["data"]["tree_sha256"] == independent(partition, unit, root) == inventory_unit(root, partition, unit).sha256
    before = shown["data"]["tree_sha256"]; (root / "memo.md").write_bytes(b"different")
    assert inventory_unit(root, partition, unit).sha256 != before
    for bad in ("../x", "x/y", ""):
        assert invoke(capsys, "--workspace", str(bundle), "record", "show", "--partition", bad, "--record", unit)[0] in {2, 3}

def test_sc015_sc016_delete_precondition_and_last_partition_cleanup(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); partition, unit = add_md(bundle, tmp_path, capsys); root = bundle / partition / unit; token = inventory_unit(root, partition, unit).sha256
    assert invoke(capsys, "--workspace", str(bundle), "record", "delete", "--partition", partition, "--record", unit, "--expected-tree-sha256", "0" * 64)[1]["issues"][0]["code"] == "tree_digest_mismatch"
    assert invoke(capsys, "--workspace", str(bundle), "record", "delete", "--partition", partition, "--record", unit, "--expected-tree-sha256", token)[0] == 0
    assert not (bundle / partition).exists() and validate_workspace(bundle).valid

@pytest.mark.parametrize("residue_failure", [False, True])
def test_sc020_sc021_after_start_delete_failure_reports_exact_partial_without_recovery(tmp_path, capsys, monkeypatch, residue_failure):
    import cortex.service as service
    bundle = configured(tmp_path, capsys); partition, unit_name = add_md(bundle, tmp_path, capsys); unit_path = bundle / partition / unit_name; token = inventory_unit(unit_path, partition, unit_name).sha256
    real_unlink = service.os.unlink; calls = 0
    def fail_second(path):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("injected after-start failure")
        return real_unlink(path)
    monkeypatch.setattr(service.os, "unlink", fail_second)
    if residue_failure: monkeypatch.setattr(service, "_residue_count", lambda _path: (_ for _ in ()).throw(OSError("injected residue failure")))
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--partition", partition, "--record", unit_name, "--expected-tree-sha256", token)
    assert code == 6 and result["issues"][0]["code"] == "delete_incomplete" and result["data"]["partition"] == partition and result["data"]["record"] == unit_name
    assert set(result["data"]) == {"partition", "record", "partial", "first_failed_relative_path", "remaining_entry_count"}
    assert set(result["issues"][0]) >= {"code", "message", "path", "details"} and "injected after-start failure" in result["issues"][0]["details"]["os_error"]
    assert result["data"]["partial"] is True and result["data"]["first_failed_relative_path"] not in {None, ""}
    if residue_failure:
        assert [item["code"] for item in result["issues"]] == ["delete_incomplete", "residue_unreadable"] and result["data"]["remaining_entry_count"] is None
    else: assert result["data"]["remaining_entry_count"] >= 1
    forbidden = ("stage", "trash", "journal", "tombstone", "recovery")
    assert not any(any(word in path.name.casefold() for word in forbidden) or path.name.casefold().startswith(".cortex-") for path in bundle.rglob("*"))

def test_sc020_partition_scan_failure_after_unit_removal_reports_partial(tmp_path, capsys, monkeypatch):
    import cortex.service as service
    bundle = configured(tmp_path, capsys); partition, unit_name = add_md(bundle, tmp_path, capsys); partition_path = bundle / partition; unit_path = partition_path / unit_name; token = inventory_unit(unit_path, partition, unit_name).sha256
    real_scan = service.checked_scandir
    def fail_partition_scan(path):
        if Path(path) == partition_path: raise service.io_error("injected partition scan failure", "directory_unreadable", path=str(path))
        return real_scan(path)
    monkeypatch.setattr(service, "checked_scandir", fail_partition_scan)
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "delete", "--partition", partition, "--record", unit_name, "--expected-tree-sha256", token)
    assert code == 6 and result["issues"][0]["code"] == "delete_incomplete"
    assert result["data"] == {"partition": partition, "record": unit_name, "partial": True, "first_failed_relative_path": ".", "remaining_entry_count": 0}
    assert partition_path.is_dir() and not unit_path.exists() and not list(partition_path.iterdir())

def test_sc017_sc018_registered_authorization_uses_root_lock(tmp_path, capsys):
    from cortex.locking import writer_lock
    root = tmp_path / "kb"; root.mkdir(); bundle = configured(root, capsys); bundle.rename(root / "bundle-id"); bundle = root / "bundle-id"
    (root / "registry.json").write_bytes(json_bytes({"version": 1, "bundles": [{"id": "bundle-id", "path": "bundle-id", "description": "d"}]})); (root / ".cortex.lock").write_bytes(b"")
    partition, unit = add_md(bundle, tmp_path, capsys)
    with writer_lock(root / ".cortex.lock"):
        code, result = invoke(capsys, "--kb-root", str(root), "--bundle-id", "bundle-id", "record", "show", "--partition", partition, "--record", unit)
    assert code == 5 and result["status"] == "busy"

def test_sc017_registered_route_locks_before_bundle_validation(tmp_path, capsys):
    from cortex.locking import writer_lock
    root = tmp_path / "kb"; root.mkdir(); bundle = configured(root, capsys); bundle.rename(root / "bundle-id"); bundle = root / "bundle-id"
    (root / "registry.json").write_bytes(json_bytes({"version": 1, "bundles": [{"id": "bundle-id", "path": "bundle-id", "description": "d"}]})); (root / ".cortex.lock").write_bytes(b"")
    partition, unit = add_md(bundle, tmp_path, capsys); transient = bundle / partition / ".cortex-add-visible"; transient.mkdir()
    with writer_lock(root / ".cortex.lock"):
        code, result = invoke(capsys, "--kb-root", str(root), "--bundle-id", "bundle-id", "record", "show", "--partition", partition, "--record", unit)
    assert code == 5 and result["status"] == "busy" and result["issues"][0]["code"] == "workspace_busy"
    transient.rmdir()
    assert invoke(capsys, "--kb-root", str(root), "--bundle-id", "bundle-id", "record", "show", "--partition", partition, "--record", unit)[0] == 0

def test_sc019_sc020_sc021_wrong_pair_and_no_recovery_artifact(tmp_path, capsys):
    bundle = configured(tmp_path, capsys); partition, unit = add_md(bundle, tmp_path, capsys)
    code, result = invoke(capsys, "--workspace", str(bundle), "record", "show", "--partition", "project-beta", "--record", unit)
    assert code == 3 and result["issues"][0]["code"] == "record_not_found"
    assert not any(x.name.startswith(".cortex-") for x in bundle.rglob("*"))

def test_sc019_bundle_and_unit_nonregular_or_reparse_fail_closed(tmp_path, capsys, monkeypatch):
    import cortex.validation as validation
    bundle_file = tmp_path / "not-a-bundle"; bundle_file.write_bytes(b"x")
    assert [item["code"] for item in validate_workspace(bundle_file).issues] == ["workspace_not_initialized"]
    ordinary = tmp_path / "ordinary"; ordinary.mkdir(); bundle = configured(ordinary, capsys); partition, unit = add_md(bundle, ordinary, capsys); unit_path = bundle / partition / unit
    shutil.rmtree(unit_path); unit_path.write_bytes(b"not-a-directory")
    assert any(item["code"] == "real_directory_required" and item.get("path") == f"{partition}/{unit}" for item in validate_workspace(bundle).issues)

    reparse = tmp_path / "reparse"; reparse.mkdir(); reparse_bundle = configured(reparse, capsys); reparse_partition, reparse_unit = add_md(reparse_bundle, reparse, capsys); reparse_unit_path = reparse_bundle / reparse_partition / reparse_unit
    real_meta = validation._meta
    class ReparseMeta:
        st_mode = stat.S_IFLNK
    monkeypatch.setattr(validation, "_meta", lambda path, label, issues: ReparseMeta() if label == "." else real_meta(path, label, issues))
    assert [item["code"] for item in validation.validate_workspace(reparse_bundle).issues] == ["workspace_not_initialized"]
    monkeypatch.setattr(validation, "_meta", lambda path, label, issues: ReparseMeta() if label == f"{reparse_partition}/{reparse_unit}" else real_meta(path, label, issues))
    assert any(item["code"] == "real_directory_required" and item.get("path") == f"{reparse_partition}/{reparse_unit}" for item in validation.validate_workspace(reparse_bundle).issues)

def test_sc007_partition_and_unit_casefold_collisions_are_rejected(tmp_path, capsys, monkeypatch):
    import cortex.validation as validation
    bundle = configured(tmp_path, capsys); partition, unit = add_md(bundle, tmp_path, capsys)
    real_scan = validation._scan
    class Alias:
        def __init__(self, entry, name): self._entry = entry; self.name = name; self.path = entry.path
        def stat(self, *, follow_symlinks=True): return self._entry.stat(follow_symlinks=follow_symlinks)
        def is_dir(self, *, follow_symlinks=True): return self._entry.is_dir(follow_symlinks=follow_symlinks)
    def partition_collision(path, label, issues):
        entries = real_scan(path, label, issues)
        if Path(path) == bundle:
            original = next(entry for entry in entries if entry.name == partition)
            entries.append(Alias(original, partition.upper()))
        return entries
    monkeypatch.setattr(validation, "_scan", partition_collision)
    assert any(item["code"] == "partition_casefold_collision" for item in validation.validate_workspace(bundle).issues)
    def unit_collision(path, label, issues):
        entries = real_scan(path, label, issues)
        if label == partition:
            original = next(entry for entry in entries if entry.name == unit)
            entries.append(Alias(original, unit.upper()))
        return entries
    monkeypatch.setattr(validation, "_scan", unit_collision)
    assert any(item["code"] == "record_casefold_collision" for item in validation.validate_workspace(bundle).issues)

def test_sc028_sc030_sc031_public_surface_and_fixture():
    root = Path(__file__).parents[1]; fixture = json.loads((root / "fixtures/capabilities/cortex7-surface.json").read_text())
    assert not any("migrat" in route for route in PUBLIC_ROUTES); assert fixture["version"] == VERSION and fixture["profile_versions"]["layout"] == 5
    assert fixture["record_operand"] == {"partition": "<exact-partition-tag>", "record": "<exact-unit-name>"}

def test_sc030_verification_matrix_is_exact_and_complete():
    text = (Path(__file__).parents[1] / "docs/verification-matrix.md").read_text("utf-8")
    ids = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| sc-")]
    assert ids == [f"sc-{number:03d}" for number in range(1, 31)]
    for required in ("candidate under KB root", "candidate under source repo", "same-volume staging", "plan/build only", "no cutover"): assert required in text


@pytest.mark.parametrize("fatal_code", ("core_protocol_error", "core_runner_process_failed"))
def test_batch_core_transport_failure_aborts_before_later_item(tmp_path, monkeypatch, fatal_code):
    import cortex.cli as cli

    job = tmp_path / "job.json"
    job.write_text(json.dumps({"items": [
        {"id": "first", "metadata": {}, "source": str(tmp_path / "first.md")},
        {"id": "later", "metadata": {}, "source": str(tmp_path / "later.md")},
    ]}), encoding="utf-8")
    calls = []

    class FakeCoreRunner:
        @staticmethod
        def from_config(): return object()

    class FakeService:
        def __init__(self, *_args, **_kwargs): pass
        @contextmanager
        def batch_add_context(self): yield
        def record_add(self, *_args):
            calls.append("add")
            raise CortexError("fatal core transport", status=Status.IO_ERROR, code=fatal_code)

    monkeypatch.setattr(cli, "CoreRunner", FakeCoreRunner)
    monkeypatch.setattr(cli, "CortexService", FakeService)
    with pytest.raises(CortexError, match="fatal core transport"):
        cli._private_batch(["--workspace", str(tmp_path / "bundle"), "--_record-add-batch", str(job)])
    assert calls == ["add"]
