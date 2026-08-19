from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.cli import main
from cortex.constants import RECORD_SCHEMA, ROOT_LOCK_FILENAME, VERSION
from cortex.jsonio import json_bytes
from cortex.locking import writer_lock, workspace_lock


def invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(["--json", *args])
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert set(result) == {"status", "exit_code", "command", "data", "issues"}
    assert code == result["exit_code"]
    return code, result


def write_json(path: Path, value: object) -> Path:
    path.write_bytes(json_bytes(value))
    return path


def create_bundle(root: Path, name: str, capsys: pytest.CaptureFixture[str]) -> Path:
    bundle = root / name
    code, result = invoke(capsys, "--workspace", str(bundle), "manage", "init")
    assert code == 0, result
    return bundle


def registry_value(*entries: tuple[str, str, str]) -> dict:
    return {"version": 1, "bundles": [{"id": bundle_id, "path": path, "description": description} for bundle_id, path, description in entries]}


def set_registry(root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], value: dict, name: str = "registry-input.json") -> tuple[int, dict]:
    operand = write_json(tmp_path / name, value)
    return invoke(capsys, "--kb-root", str(root), "registry", "set", "--file", str(operand))


def test_sc_024_registry_create_show_validate_resolve_and_record_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    bundle = create_bundle(root, "alpha", capsys)
    value = registry_value(("alpha-bundle", "alpha", "Alpha"))
    code, result = set_registry(root, tmp_path, capsys, value)
    assert code == 0 and result["data"]["registry"] == value
    assert (root / ROOT_LOCK_FILENAME).read_bytes() == b""
    assert (root / "registry.json").read_bytes() == json_bytes(value)
    code, result = invoke(capsys, "--kb-root", str(root), "registry", "show")
    assert code == 0 and result["data"] == {"registry": value}
    code, result = invoke(capsys, "--kb-root", str(root), "registry", "validate")
    assert code == 0 and result["data"] == {"version": VERSION, "valid": True, "bundles": 1}
    code, result = invoke(capsys, "--kb-root", str(root), "registry", "resolve", "--bundle-id", "alpha-bundle")
    assert code == 0 and result["data"]["workspace"] == str(bundle)
    code, result = invoke(capsys, "--kb-root", str(root), "--bundle-id", "alpha-bundle", "manage", "config", "show", "--profile", "record")
    assert code == 0 and result["data"] == {"profile": "record", "value": RECORD_SCHEMA}


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"version": 1, "bundles": [{"id": "Upper", "path": "alpha", "description": "x"}]}, "invalid_bundle_id"),
        (registry_value(("a", "nested/path", "x")), "invalid_bundle_path"),
        (registry_value(("a", "alpha.migrating", "x")), "invalid_bundle_path"),
        (registry_value(("a", "alpha", "x"), ("a", "beta", "y")), "duplicate_bundle_id"),
        (registry_value(("a", "alpha", "x"), ("b", "alpha", "y")), "duplicate_bundle_path"),
    ],
)
def test_sc_025_invalid_registry_is_no_write(tmp_path: Path, capsys: pytest.CaptureFixture[str], value: dict, expected: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    before = sorted(path.name for path in root.iterdir())
    code, result = set_registry(root, tmp_path, capsys, value)
    assert code == 3 and any(item["code"] == expected for item in result["issues"])
    assert sorted(path.name for path in root.iterdir()) == before
    assert not (root / ROOT_LOCK_FILENAME).exists() and not (root / "registry.json").exists()


def test_sc_026_registry_transition_only_adds_pairs_or_changes_descriptions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    create_bundle(root, "beta", capsys)
    first = registry_value(("alpha-bundle", "alpha", "old"))
    assert set_registry(root, tmp_path, capsys, first)[0] == 3  # beta is an orphan
    both = registry_value(("alpha-bundle", "alpha", "old"), ("beta-bundle", "beta", "Beta"))
    assert set_registry(root, tmp_path, capsys, both)[0] == 0
    changed = registry_value(("alpha-bundle", "alpha", "new"), ("beta-bundle", "beta", "Beta"))
    assert set_registry(root, tmp_path, capsys, changed, "changed.json")[0] == 0
    before = (root / "registry.json").read_bytes()
    removed = registry_value(("alpha-bundle", "alpha", "new"))
    code, result = set_registry(root, tmp_path, capsys, removed, "removed.json")
    assert code == 3 and any(item["code"] == "bundle_removal_forbidden" for item in result["issues"])
    reassigned = registry_value(("alpha-bundle", "beta", "new"), ("beta-bundle", "alpha", "Beta"))
    code, result = set_registry(root, tmp_path, capsys, reassigned, "reassigned.json")
    assert code == 3 and any(item["code"] == "bundle_reassignment_forbidden" for item in result["issues"])
    assert (root / "registry.json").read_bytes() == before


def test_sc_027_orphan_invalid_target_unknown_and_selector_fail_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    create_bundle(root, "orphan", capsys)
    code, result = set_registry(root, tmp_path, capsys, registry_value(("alpha", "alpha", "A")))
    assert code == 3 and any(item["code"] == "orphan_bundle" for item in result["issues"])
    assert set_registry(root, tmp_path, capsys, registry_value(("alpha", "alpha", "A"), ("orphan", "orphan", "O")))[0] == 0
    code, result = invoke(capsys, "--kb-root", str(root), "registry", "resolve", "--bundle-id", "missing")
    assert code == 3 and result["issues"][0]["code"] == "unknown_bundle_id"
    code, result = invoke(capsys, "--kb-root", str(root), "manage", "status")
    assert code == 2 and result["issues"][0]["code"] == "bundle_selection_required"
    code, result = invoke(capsys, "--kb-root", str(root), "--bundle-id", "alpha", "manage", "init")
    assert code == 2 and result["issues"][0]["code"] == "invalid_selector"


def test_sc_028_registered_root_has_one_shared_busy_lock_and_standalone_keeps_schema_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    alpha = create_bundle(root, "alpha", capsys)
    beta = create_bundle(root, "beta", capsys)
    assert set_registry(root, tmp_path, capsys, registry_value(("alpha", "alpha", "A"), ("beta", "beta", "B")))[0] == 0
    with writer_lock(root / ROOT_LOCK_FILENAME):
        for selector in (("--kb-root", str(root), "--bundle-id", "alpha"), ("--workspace", str(alpha)), ("--workspace", str(beta))):
            code, result = invoke(capsys, *selector, "manage", "config", "set", "--profile", "tags", "--file", str(alpha / "profiles" / "tags.json"))
            assert code == 5 and result["status"] == "busy"

    standalone_root = tmp_path / "standalone-parent"
    standalone_root.mkdir()
    standalone = create_bundle(standalone_root, "bundle", capsys)
    with workspace_lock(standalone):
        code, result = invoke(capsys, "--workspace", str(standalone), "manage", "config", "set", "--profile", "tags", "--file", str(standalone / "profiles" / "tags.json"))
        assert code == 5 and result["status"] == "busy"


def test_sc_029_reads_are_lock_free_and_registry_replacement_is_atomic(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    value = registry_value(("alpha", "alpha", "old"))
    assert set_registry(root, tmp_path, capsys, value)[0] == 0
    with writer_lock(root / ROOT_LOCK_FILENAME):
        assert invoke(capsys, "--kb-root", str(root), "registry", "show")[0] == 0
        assert invoke(capsys, "--kb-root", str(root), "--bundle-id", "alpha", "manage", "status")[0] == 0

    import cortex.service as service_module

    before = (root / "registry.json").read_bytes()
    monkeypatch.setattr(service_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("injected")))
    changed = registry_value(("alpha", "alpha", "new"))
    code, result = set_registry(root, tmp_path, capsys, changed, "replacement.json")
    assert code == 6 and result["issues"][0]["code"] == "replace_failed"
    assert (root / "registry.json").read_bytes() == before
    assert list(root.glob(".cortex-*.tmp")) == []


def test_sc_030_static_contract_has_no_product_migration_or_extra_state(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    surface = json.loads((root / "fixtures" / "capabilities" / "cortex5-surface.json").read_text("utf-8"))
    assert not any(word in " ".join(surface["routes"]) for word in ("migrate", "rename", "delete", "move", "batch", "search"))
    assert not (root / "skills" / "record-build" / "SKILL.md").exists()
    assert not (root / "skills" / "record-manage" / "SKILL.md").exists()
    assert (root / "skills" / "cortex-build" / "SKILL.md").is_file()
    assert (root / "skills" / "cortex-manage" / "SKILL.md").is_file()


def test_sc_031_registry_requires_its_stable_zero_byte_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    assert set_registry(root, tmp_path, capsys, registry_value(("alpha", "alpha", "A")))[0] == 0
    (root / ROOT_LOCK_FILENAME).write_bytes(b"not-zero")
    code, result = invoke(capsys, "--kb-root", str(root), "registry", "validate")
    assert code == 3 and any(item["code"] == "invalid_root_lock" for item in result["issues"])


def test_sc_032_whole_file_set_can_add_a_new_orphan_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    create_bundle(root, "alpha", capsys)
    first = registry_value(("alpha", "alpha", "A"))
    assert set_registry(root, tmp_path, capsys, first)[0] == 0
    create_bundle(root, "beta", capsys)
    assert invoke(capsys, "--kb-root", str(root), "registry", "validate")[0] == 3
    expanded = registry_value(("alpha", "alpha", "A"), ("beta", "beta", "B"))
    code, result = set_registry(root, tmp_path, capsys, expanded, "expanded.json")
    assert code == 0 and result["data"]["registry"] == expanded
    assert invoke(capsys, "--kb-root", str(root), "registry", "validate")[0] == 0
