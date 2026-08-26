from __future__ import annotations
import importlib.util, json, os
from pathlib import Path
import pytest
from cortex.constants import PUBLIC_ROUTES, RECORD_SCHEMA
from cortex.jsonio import json_bytes
from cortex.validation import validate_workspace

OLD = {"version": 3, "unit_name_tag_group": "project", "unit_name_strategy": "tag-title-date", "max_component_length": 200, "duplicate_name_strategy": "reject"}
NEW = {"version": 4, "partition_tag_group": "project", "partition_name_strategy": "tag", "unit_name_strategy": "tag-title-date", "max_component_length": 200, "duplicate_name_strategy": "reject"}
LAYOUT5 = {**NEW, "version": 5}
def write(path: Path, value: dict) -> Path: path.write_bytes(json_bytes(value)); return path
def migration():
    path = Path(__file__).parents[1] / "tools/migrate_layout.py"; spec = importlib.util.spec_from_file_location(f"mig_{os.urandom(4).hex()}", path); module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module); return module
def source(root: Path) -> Path:
    root.mkdir(); p = root / "profiles"; p.mkdir(); write(p / "record-schema.json", RECORD_SCHEMA); write(p / "tags.json", {"version": 2, "groups": [{"name": "project", "tags": [{"tag": "a", "description": "a"}, {"tag": "b", "description": "b"}]}]}); write(p / "layout.json", OLD); return root
def registered_source(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "kb-repo"; repo.mkdir(); (repo / ".git").mkdir(); (repo / ".git/HEAD").write_text("ref: refs/heads/test\n", encoding="utf-8"); kb_root = repo / "kb"; kb_root.mkdir(); src = source(kb_root / "bundle")
    write(kb_root / "registry.json", {"version": 1, "bundles": [{"id": "bundle", "path": "bundle", "description": "migration source"}]}); (kb_root / ".cortex.lock").write_bytes(b"")
    return repo, kb_root, src
def unit(root: Path, name: str, project: str, *, full=False):
    u = root / name; u.mkdir(); write(u / "record.json", {"title": name.split("-")[1], "timestamp": "2026-08-21T00:00:00Z", "tags": [project]})
    if full:
        (u / "memo.md").write_bytes(b"md"); (u / "memo.json").write_bytes(b"json"); (u / "src").mkdir(); (u / "src/memo.pdf").write_bytes(b"pdf"); (u / "assets/opaque").mkdir(parents=True); (u / "assets/opaque/data.bin").write_bytes(b"opaque")
    else: (u / "note.md").write_bytes(b"md")
def empty_layout4(root: Path) -> Path:
    root.mkdir(); profiles = root / "profiles"; profiles.mkdir(); write(profiles / "record-schema.json", RECORD_SCHEMA); write(profiles / "tags.json", {"version": 2, "groups": []}); write(profiles / "layout.json", {"version": 4, "partition_tag_group": None, "partition_name_strategy": "tag", "unit_name_strategy": "tag-title-date", "max_component_length": 96, "duplicate_name_strategy": "reject"}); return root

def registered_layout4(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "kb-repo"; repo.mkdir(); (repo / ".git").mkdir(); (repo / ".git/HEAD").write_text("ref: refs/heads/test\n", encoding="utf-8")
    kb_root = repo / "kb"; kb_root.mkdir(); bundle = kb_root / "bundle"; bundle.mkdir(); profiles = bundle / "profiles"; profiles.mkdir()
    write(profiles / "record-schema.json", RECORD_SCHEMA); write(profiles / "tags.json", {"version": 2, "groups": [{"name": "project", "tags": [{"tag": "a", "description": "a"}, {"tag": "b", "description": "b"}]}]}); write(profiles / "layout.json", NEW)
    for partition, name, full in (("a", "a-one-20260821", False), ("b", "b-two-20260821", True)):
        (bundle / partition).mkdir(); unit(bundle / partition, name, partition, full=full)
    write(kb_root / "registry.json", {"version": 1, "bundles": [{"id": "bundle", "path": "bundle", "description": "migration source"}]}); (kb_root / ".cortex.lock").write_bytes(b"")
    return repo, kb_root, bundle

def test_sc022_sc023_plan_is_deterministic_source_read_only_and_aggregates(tmp_path):
    m = migration(); src = source(tmp_path / "source"); unit(src, "a-one-20260821", "a"); unit(src, "b-two-20260821", "b", full=True); target = write(tmp_path / "layout4.json", NEW)
    before = {p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}; first = m.plan_bundle(src, target); second = m.plan_bundle(src, target)
    assert first == second and first[0]["counts"] == {"partitions": 2, "total": 2, "full": 1, "markdown_only": 1}; assert before == {p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
    (src / "bad").mkdir(); body, _, _ = m.plan_bundle(src, target); assert body["issues"]

@pytest.mark.parametrize("defect,expected", [("noncanonical", "noncanonical_source_profile"), ("schema", "invalid_record_schema"), ("layout", "invalid_source_layout"), ("unit-name", "record_name_mismatch")])
def test_sc022_sc023_plan_rejects_profile_and_unit_name_defects_before_output(tmp_path, defect, expected):
    m = migration(); src = source(tmp_path / "source"); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); output = tmp_path / "candidate"
    if defect == "noncanonical": (src / "profiles/tags.json").write_text(json.dumps(json.loads((src / "profiles/tags.json").read_text()), indent=2), encoding="utf-8")
    elif defect == "schema": write(src / "profiles/record-schema.json", {**RECORD_SCHEMA, "version": 2})
    elif defect == "layout": write(src / "profiles/layout.json", {**OLD, "version": 2})
    else: (src / "a-one-20260821").rename(src / "wrong-name")
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}
    body1, raw1, digest1 = m.plan_bundle(src, target); body2, raw2, digest2 = m.plan_bundle(src, target)
    assert (body1, raw1, digest1) == (body2, raw2, digest2) and any(item["code"] == expected for item in body1["issues"])
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()} and not output.exists()

def test_sc023_plan_aggregates_profile_layout_and_name_defects_deterministically(tmp_path):
    m = migration(); src = source(tmp_path / "source"); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW)
    write(src / "profiles/record-schema.json", {**RECORD_SCHEMA, "version": 2}); invalid_layout = {**OLD, "version": 2}; (src / "profiles/layout.json").write_text(json.dumps(invalid_layout, indent=2), encoding="utf-8"); (src / "a-one-20260821").rename(src / "wrong-name")
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}; first = m.plan_bundle(src, target); second = m.plan_bundle(src, target)
    assert first == second
    codes = {item["code"] for item in first[0]["issues"]}
    assert {"invalid_record_schema", "invalid_source_layout", "noncanonical_source_profile", "record_name_mismatch"} <= codes and not (tmp_path / "candidate").exists()
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}

@pytest.mark.parametrize("fault", ["missing", "malformed", "reparse", "nonregular", "unreadable"])
def test_sc023_required_profile_faults_return_deterministic_invalid_plan(tmp_path, monkeypatch, fault):
    m = migration(); src = source(tmp_path / "source"); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); profile = src / "profiles/tags.json"
    if fault == "missing": profile.unlink()
    elif fault == "malformed": profile.write_bytes(b"{")
    elif fault == "nonregular": profile.unlink(); profile.mkdir()
    else:
        real_read = m._read_regular
        monkeypatch.setattr(m, "_read_regular", lambda path, code: (_ for _ in ()).throw(m.validation_error("injected", "reparse_path" if fault == "reparse" else "migration_profile_unreadable", path=str(path))) if Path(path) == profile else real_read(path, code))
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}; first = m.plan_bundle(src, target); second = m.plan_bundle(src, target)
    assert first == second and first[0]["issues"] and not (tmp_path / "candidate").exists()
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}

def test_sc023_profile_record_naming_and_payload_defects_aggregate_together(tmp_path):
    m = migration(); src = source(tmp_path / "source"); unit(src, "a-one-20260821", "a"); unit(src, "b-two-20260821", "b", full=True); target = tmp_path / "missing-target-layout.json"
    (src / "a-one-20260821").rename(src / "wrong-name"); (src / "b-two-20260821/record.json").write_bytes(b"{}"); (src / "b-two-20260821/assets/.cortex").mkdir(); (src / "b-two-20260821/assets/.cortex/secret").write_bytes(b"bad")
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}; first = m.plan_bundle(src, target); second = m.plan_bundle(src, target); assert first == second
    codes = {item["code"] for item in first[0]["issues"]}; assert {"migration_profile_invalid", "record_name_mismatch", "reserved_cortex_name"} <= codes and ("missing_field" in codes or "invalid_record_metadata" in codes)
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()} and not (tmp_path / "candidate").exists()

def test_sc024_sc025_sc026_sc027_build_preserves_exact_bytes_and_paths(tmp_path):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); unit(src, "b-two-20260821", "b", full=True); target = write(tmp_path / "layout4.json", NEW); body, _, digest = m.plan_bundle(src, target); output = tmp_path / "candidate"
    m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo); assert not m._registered_bundle_issues(output)
    for item in body["mappings"]:
        old = src / item["source_unit"]; new = output / item["partition"] / item["target_unit"]
        assert {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob("*") if p.is_file()} == {p.relative_to(new).as_posix(): p.read_bytes() for p in new.rglob("*") if p.is_file()}
    assert not any(p.name.startswith(".cortex-mig-") for p in tmp_path.iterdir())

def test_sc027_candidate_absence_uses_no_follow_check(tmp_path, monkeypatch):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); _, _, digest = m.plan_bundle(src, target); output = tmp_path / "dangling-reparse-candidate"
    real_exists = m.exists
    monkeypatch.setattr(m, "exists", lambda path: True if Path(path) == output else real_exists(path))
    with pytest.raises(Exception) as caught: m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(caught.value, "code", None) == "migration_output_exists" and not list(tmp_path.glob(".cortex-mig-*"))

def test_sc027_publication_race_never_replaces_existing_destination(tmp_path, monkeypatch):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); _, _, digest = m.plan_bundle(src, target); output = tmp_path / "candidate"
    def lose_race(stage, destination):
        assert Path(stage).name.startswith(".cortex-mig-") and Path(destination) == output
        output.mkdir(); (output / "owner.txt").write_bytes(b"other-writer")
        raise FileExistsError(str(output))
    monkeypatch.setattr(m, "rename_no_replace", lose_race)
    with pytest.raises(Exception) as caught: m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(caught.value, "code", None) == "migration_output_exists"
    assert (output / "owner.txt").read_bytes() == b"other-writer" and not list(tmp_path.glob(".cortex-mig-*"))

def test_sc026_conversion_source_stem_mismatch_blocks_before_output(tmp_path):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a", full=True); (src / "a-one-20260821/src/memo.pdf").rename(src / "a-one-20260821/src/other.pdf"); target = write(tmp_path / "layout4.json", NEW)
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}; body, _, digest = m.plan_bundle(src, target); assert any(item["code"] == "conversion_source_stem_mismatch" for item in body["issues"])
    output = tmp_path / "candidate"
    with pytest.raises(Exception) as caught: m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(caught.value, "code", None) == "migration_plan_invalid" and not output.exists()
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}

@pytest.mark.parametrize("fault", ["missing-sibling", "invalid-sibling", "orphan"])
def test_sc033_complete_initialized_root_gate_blocks_before_output(tmp_path, fault):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); registry = json.loads((kb_root / "registry.json").read_text())
    if fault in {"missing-sibling", "invalid-sibling"}:
        registry["bundles"].append({"id": "sibling", "path": "sibling", "description": "sibling"}); write(kb_root / "registry.json", registry)
        if fault == "invalid-sibling": source(kb_root / "sibling")
    else: empty_layout4(kb_root / "orphan")
    _, _, digest = m.plan_bundle(src, target); output = tmp_path / "candidate"
    with pytest.raises(Exception) as caught: m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(caught.value, "code", None) in {"migration_registered_sibling_invalid", "migration_orphan_bundle"} and not output.exists() and not list(tmp_path.glob(".cortex-mig-*"))

def test_sc033_valid_sibling_and_unrelated_entries_are_accepted(tmp_path):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); empty_layout4(kb_root / "sibling"); registry = json.loads((kb_root / "registry.json").read_text()); registry["bundles"].append({"id": "sibling", "path": "sibling", "description": "sibling"}); write(kb_root / "registry.json", registry); (kb_root / "notes.txt").write_bytes(b"ignored"); (kb_root / "ordinary-dir").mkdir()
    target = write(tmp_path / "layout4.json", NEW); _, _, digest = m.plan_bundle(src, target); output = tmp_path / "candidate"; m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert not m._registered_bundle_issues(output) and (kb_root / "notes.txt").read_bytes() == b"ignored"

def test_sc029_exact_ibd_fixture_gate():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/migrations/ibd-projects-layout4.json").read_text())
    assert fixture["expected"] == {"partitions": 30, "total": 395, "full": 25, "markdown_only": 370}

def test_sc033_candidate_and_stage_rejected_under_protected_roots(tmp_path, monkeypatch):
    m = migration(); repo, kb_root, src = registered_source(tmp_path); unit(src, "a-one-20260821", "a"); target = write(tmp_path / "layout4.json", NEW); _, _, digest = m.plan_bundle(src, target)
    with pytest.raises(Exception) as caught: m.build_bundle(src, kb_root / "candidate", target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(caught.value, "code", None) == "migration_candidate_location" and not (kb_root / "candidate").exists()
    protected = m._validated_boundaries(src, kb_root, repo)
    with pytest.raises(Exception) as stage_caught: m._destination(src, repo / ".cortex-mig-stage", protected)
    assert getattr(stage_caught.value, "code", None) == "migration_candidate_location"
    real_dir = m._real_dir
    monkeypatch.setattr(m, "_real_dir", lambda path, code: type("Meta", (), {"st_dev": 2 if Path(path) == src else 1})())
    with pytest.raises(Exception) as volume_caught: m._destination(src, tmp_path / "external-candidate", protected)
    assert getattr(volume_caught.value, "code", None) == "migration_volume_mismatch"
    monkeypatch.setattr(m, "_real_dir", real_dir)
    for kwargs in ({}, {"kb_root": kb_root, "kb_repo": tmp_path}, {"kb_root": tmp_path, "kb_repo": repo}):
        with pytest.raises(Exception): m.build_bundle(src, tmp_path / "never-created", target, digest, **kwargs)
        assert not (tmp_path / "never-created").exists()
    with pytest.raises(Exception) as repo_candidate: m.build_bundle(src, m._REPO / "forbidden-candidate", target, digest, kb_root=kb_root, kb_repo=repo)
    assert getattr(repo_candidate.value, "code", None) == "migration_candidate_location"

def test_sc034_no_cutover_route_or_function():
    m = migration(); assert not hasattr(m, "cutover_bundle")
    root = Path(__file__).parents[1]; help_text = (root / "tools/migrate_layout.py").read_text("utf-8"); assert "cutover-bundle" not in help_text and "os.replace" not in help_text
    assert [path.name for path in (root / "tools").glob("*migrat*.py")] == ["migrate_layout.py"]
    assert not any("migrat" in route or "cutover" in route for route in PUBLIC_ROUTES)

def test_layout4_to_layout5_adds_only_exact_envelope_state(tmp_path):
    m = migration(); repo, kb_root, src = registered_layout4(tmp_path); target = write(tmp_path / "layout5.json", LAYOUT5)
    before = {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}
    body, _, digest = m.plan_bundle(src, target)
    assert body["format"] == "cortex-layout4-to-layout5-plan-v1"
    assert body["counts"] == {"partitions": 2, "total": 2, "guide_files": 4, "assets_markers": 1, "src_markers": 1, "files_added": 6, "dirs_added": 2}
    assert before == {path.relative_to(src).as_posix(): path.read_bytes() for path in src.rglob("*") if path.is_file()}
    output = tmp_path / "layout5-candidate"
    m.build_bundle(src, output, target, digest, kb_root=kb_root, kb_repo=repo)
    assert validate_workspace(output).valid
    for relative, payload in before.items():
        if relative != "profiles/layout.json":
            assert (output / relative).read_bytes() == payload
    assert json.loads((output / "profiles/layout.json").read_text())["version"] == 5
