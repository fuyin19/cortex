from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cortex_notes import core


ROOT = Path(__file__).parents[1]
NOTES_SKILLS = ("cortex-notes-ingest", "cortex-notes-build", "cortex-notes-manage")
ALL_SKILLS = ("cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage", *NOTES_SKILLS)


def _tools(tmp_path: Path, partitions: tuple[str, ...] = core.TOOL_PARTITIONS) -> Path:
    root = tmp_path / "tools"; root.mkdir()
    for name in partitions:
        (root / name / ".git").mkdir(parents=True)
    return root


def _initialized(tmp_path: Path) -> tuple[Path, Path]:
    root, tools = tmp_path / "notes", _tools(tmp_path)
    assert core.registry_init(root)["status"] == "ok"
    for bundle, _description in core.REGISTRY_ORDER:
        assert core.bundle_init(root, bundle, tools)["status"] == "ok"
    assert core.validate(root)["status"] == "ok"
    return root, tools


def _add(root: Path, tools: Path, tmp_path: Path, bundle: str, partition: str | None, index: int) -> dict[str, object]:
    body = tmp_path / f"body-{index}.md"; body.write_text(f"# 漢字 {index}\n", encoding="utf-8")
    return core.note_add(root, tools, bundle, partition, f"漢字 idea {index}", body, f"2026-08-{index + 1:02d}T01:02:03.{index:06d}+08:00")["data"]


def test_notes_exact_schemas_and_all_partition_lifecycles(tmp_path: Path) -> None:
    root, tools = _initialized(tmp_path)
    assert json.loads((root / "registry.json").read_text("utf-8")) == core.registry_value()
    cases = [("daily-notes", None), *(('tools-feedback', value) for value in core.TOOL_PARTITIONS), *(('ideas', value) for value in core.IDEA_PARTITIONS)]
    for index, (bundle, partition) in enumerate(cases):
        added = _add(root, tools, tmp_path, bundle, partition, index)
        actual_partition, note, digest = added["partition"], added["note"], added["tree_sha256"]
        unit = root / bundle / actual_partition / note
        assert sorted(path.name for path in unit.iterdir()) == ["note.json", "note.md"]
        metadata_before = (unit / "note.json").read_bytes()
        shown = core.note_show(root, bundle, actual_partition, note, False)["data"]
        assert shown["tree_sha256"] == digest and "漢字" in shown["body"]
        edited_body = tmp_path / f"edited-{index}.md"; edited_body.write_text("updated\n", encoding="utf-8")
        edited = core.note_edit(root, bundle, actual_partition, note, False, edited_body, digest)["data"]
        assert (unit / "note.json").read_bytes() == metadata_before
        archived = core.note_archive(root, bundle, actual_partition, note, edited["tree_sha256"])["data"]
        archived_unit = root / bundle / actual_partition / "archive" / note
        assert archived_unit.is_dir() and not unit.exists()
        assert core.note_delete(root, bundle, actual_partition, note, True, archived["tree_sha256"], "yes")["data"] == {"deleted": True}
        assert not archived_unit.exists()
    assert core.validate(root)["status"] == "ok"


def test_notes_timestamp_identity_stale_tools_and_monotonic_partition(tmp_path: Path) -> None:
    root, tools = _initialized(tmp_path)
    body = tmp_path / "body.md"; body.write_text("x", encoding="utf-8")
    with pytest.raises(core.NotesError, match="daily_partition_mismatch"):
        core.note_add(root, tools, "daily-notes", "20260824", "x", body, "2026-08-23T00:00:00.000000+08:00")
    with pytest.raises(core.NotesError, match="timestamp_invalid"):
        core.note_add(root, tools, "daily-notes", None, "x", body, "2026-08-23T00:00:00Z")
    stale = tools / core.TOOL_PARTITIONS[0]
    shutil.rmtree(stale / ".git")
    with pytest.raises(core.NotesError, match="tool_repository_missing"):
        core.note_add(root, tools, "tools-feedback", stale.name, "x", body, "2026-08-23T00:00:00.000000+08:00")
    new_tool = tools / "new-tool"; (new_tool / ".git").mkdir(parents=True)
    core.partition_add(root, "tools-feedback", "new-tool", tools)
    values = core.bundle_show(root, "tools-feedback")["data"]["partitions"]
    assert values == [*core.TOOL_PARTITIONS, "new-tool"]


def test_notes_missing_configured_partition_fails_validation_and_list(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    partition = core.IDEA_PARTITIONS[0]
    shutil.rmtree(root / "ideas" / partition)
    with pytest.raises(core.NotesError, match="configured_partition_missing"):
        core.validate(root, "ideas")
    with pytest.raises(core.NotesError, match="configured_partition_missing"):
        core.note_list(root, "ideas", partition, False)


def test_notes_missing_archive_fails_validation_and_list(tmp_path: Path) -> None:
    root, tools_root = _initialized(tmp_path)
    partition = core.TOOL_PARTITIONS[0]
    shutil.rmtree(root / "tools-feedback" / partition / "archive")
    with pytest.raises(core.NotesError, match="archive_missing"):
        core.validate(root, "tools-feedback")
    with pytest.raises(core.NotesError, match="archive_missing"):
        core.note_list(root, "tools-feedback", partition, False)
    daily = _add(root, tools_root, tmp_path, "daily-notes", None, 0)["partition"]
    shutil.rmtree(root / "daily-notes" / daily / "archive")
    with pytest.raises(core.NotesError, match="archive_missing"):
        core.validate(root, "daily-notes")
    with pytest.raises(core.NotesError, match="archive_missing"):
        core.note_list(root, "daily-notes", daily, False)


def test_notes_invalid_registry_blocks_selected_bundle_preflight(tmp_path: Path) -> None:
    root, tools_root = _initialized(tmp_path)
    registry = json.loads((root / "registry.json").read_text("utf-8"))
    registry["unexpected"] = True
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(core.NotesError, match="registry_schema_invalid"):
        core.bundle_show(root, "ideas")
    body = tmp_path / "invalid-registry.md"; body.write_text("blocked", encoding="utf-8")
    with pytest.raises(core.NotesError, match="registry_schema_invalid"):
        core.note_add(root, tools_root, "ideas", core.IDEA_PARTITIONS[0], "blocked", body, "2026-08-23T00:00:00.000000+08:00")


def test_notes_add_never_repairs_missing_allowlisted_partition_or_archive(tmp_path: Path) -> None:
    root, tools_root = _initialized(tmp_path)
    body = tmp_path / "blocked-add.md"; body.write_text("blocked", encoding="utf-8")
    missing_partition = root / "ideas" / core.IDEA_PARTITIONS[0]
    shutil.rmtree(missing_partition)
    with pytest.raises(core.NotesError, match="configured_partition_missing"):
        core.note_add(root, tools_root, "ideas", core.IDEA_PARTITIONS[0], "blocked", body, "2026-08-23T00:00:00.000000+08:00")
    assert not missing_partition.exists()

    archive = root / "tools-feedback" / core.TOOL_PARTITIONS[0] / "archive"
    shutil.rmtree(archive)
    with pytest.raises(core.NotesError, match="archive_missing"):
        core.note_add(root, tools_root, "tools-feedback", core.TOOL_PARTITIONS[0], "blocked", body, "2026-08-23T00:00:00.000000+08:00")
    assert not archive.exists()


def test_notes_list_checks_other_configured_partitions_first(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    intact, missing = core.IDEA_PARTITIONS
    shutil.rmtree(root / "ideas" / missing)
    with pytest.raises(core.NotesError, match="configured_partition_missing"):
        core.note_list(root, "ideas", intact, False)


@pytest.mark.parametrize("operation", ("edit", "archive", "delete"))
@pytest.mark.parametrize("damage", ("registry", "bundle"))
def test_notes_existing_mutations_fail_before_write_when_state_is_invalid(tmp_path: Path, operation: str, damage: str) -> None:
    root, tools_root = _initialized(tmp_path)
    added = _add(root, tools_root, tmp_path, "ideas", core.IDEA_PARTITIONS[0], 0)
    partition, note, digest = added["partition"], added["note"], added["tree_sha256"]
    unit = root / "ideas" / partition / note
    before = {path.name: path.read_bytes() for path in unit.iterdir()}
    damaged = root / "registry.json" if damage == "registry" else root / "ideas" / "bundle.json"
    value = json.loads(damaged.read_text("utf-8")); value["unexpected"] = True
    damaged.write_text(json.dumps(value), encoding="utf-8")
    replacement = tmp_path / "replacement.md"; replacement.write_text("must not apply", encoding="utf-8")
    expected = "registry_schema_invalid" if damage == "registry" else "bundle_schema_invalid"
    with pytest.raises(core.NotesError, match=expected):
        if operation == "edit":
            core.note_edit(root, "ideas", partition, note, False, replacement, digest)
        elif operation == "archive":
            core.note_archive(root, "ideas", partition, note, digest)
        else:
            core.note_delete(root, "ideas", partition, note, False, digest, "yes")
    assert unit.is_dir()
    assert {path.name: path.read_bytes() for path in unit.iterdir()} == before
    assert not (root / "ideas" / partition / "archive" / note).exists()


def test_notes_stale_digest_confirmation_and_metadata_fail_closed(tmp_path: Path) -> None:
    root, tools = _initialized(tmp_path)
    added = _add(root, tools, tmp_path, "ideas", core.IDEA_PARTITIONS[0], 0)
    bundle, partition, note = "ideas", added["partition"], added["note"]
    body = root / bundle / partition / note / "note.md"; body.write_text("external change", encoding="utf-8")
    replacement = tmp_path / "replacement.md"; replacement.write_text("replacement", encoding="utf-8")
    with pytest.raises(core.NotesError, match="stale_note_state"):
        core.note_edit(root, bundle, partition, note, False, replacement, added["tree_sha256"])
    fresh = core.note_show(root, bundle, partition, note, False)["data"]["tree_sha256"]
    with pytest.raises(core.NotesError, match="delete_confirmation_required"):
        core.note_delete(root, bundle, partition, note, False, fresh, "no")
    assert (root / bundle / partition / note).is_dir()
    metadata = root / bundle / partition / note / "note.json"
    value = json.loads(metadata.read_text("utf-8")); value["extra"] = True
    metadata.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(core.NotesError, match="note_schema_invalid"):
        core.note_show(root, bundle, partition, note, False)


def test_notes_archive_collision_and_delete_partial_residue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, tools = _initialized(tmp_path)
    added = _add(root, tools, tmp_path, "ideas", core.IDEA_PARTITIONS[0], 0)
    partition, note, digest = added["partition"], added["note"], added["tree_sha256"]
    collision = root / "ideas" / partition / "archive" / note
    collision.mkdir()
    with pytest.raises(core.NotesError, match="archive_destination_exists"):
        core.note_archive(root, "ideas", partition, note, digest)
    collision.rmdir()
    original = Path.unlink
    calls = 0
    def fail_second(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(core.NotesError, match="note_delete_partial") as captured:
        core.note_delete(root, "ideas", partition, note, False, digest, "yes")
    assert captured.value.data == {"residue": ["note.json"]}


def test_notes_links_case_collision_and_unsafe_components_fail_closed(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    ideas = root / "ideas"
    bundle_file = ideas / "bundle.json"
    bundle = json.loads(bundle_file.read_text("utf-8"))
    bundle["partition"]["values"].append("NEW-TOOLS-AND-FUNCTIONS")
    bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(core.NotesError, match="casefold_collision"):
        core.validate(root, "ideas")
    bundle_file.write_text(json.dumps(core.bundle_value("ideas")), encoding="utf-8")
    external = tmp_path / "external"; external.mkdir()
    linked = ideas / core.IDEA_PARTITIONS[0] / "linked"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        assert "FILE_ATTRIBUTE_REPARSE_POINT" in (ROOT / "notes_runtime/src/cortex_notes/core.py").read_text("utf-8")
    else:
        with pytest.raises(core.NotesError, match="linked_or_reparse_path"):
            core.validate(root, "ideas")
        linked.unlink()
    with pytest.raises(core.NotesError, match="unsafe_component"):
        core.partition_add(root, "tools-feedback", "bad/name", tmp_path / "tools")


def test_notes_runtime_packaging_integrity_and_exact_taxonomy(tmp_path: Path) -> None:
    assert sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir() and any(path.iterdir())) == sorted(ALL_SKILLS)
    payloads = []
    for name in NOTES_SKILLS:
        skill = ROOT / "skills" / name
        payloads.append({relative: (skill / relative).read_bytes() for relative in (
            "scripts/run_notes.py", "scripts/run_notes.cmd", "scripts/runtime-manifest.json",
            "scripts/vendor/cortex_notes-1.0.0-py3-none-any.whl",
        )})
    assert payloads[0] == payloads[1] == payloads[2]
    env = dict(os.environ); env["CORTEX_PYTHON"] = os.path.abspath(sys.executable); env["PIP_NO_INDEX"] = "1"
    check = subprocess.run([sys.executable, str(ROOT / "tools/package_notes_runtime.py"), "--check"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert check.returncode == 0 and check.stderr == "" and "sha256=" in check.stdout
    runner = ROOT / "skills/cortex-notes-manage/scripts/run_notes.py"
    version = subprocess.run([sys.executable, "-I", str(runner), "--version"], cwd=runner.parent, env=env, capture_output=True, text=True, check=False)
    assert version.returncode == 0 and version.stdout == "cortex-notes 1.0.0\n"
    copied = tmp_path / "skill"; shutil.copytree(ROOT / "skills/cortex-notes-manage", copied)
    wheel = copied / "scripts/vendor/cortex_notes-1.0.0-py3-none-any.whl"; wheel.write_bytes(wheel.read_bytes() + b"tamper")
    failed = subprocess.run([sys.executable, "-I", str(copied / "scripts/run_notes.py"), "--version"], cwd=copied, env=env, capture_output=True, text=True, check=False)
    assert failed.returncode == 70 and "wheel_digest_mismatch" in failed.stderr


def test_notes_cli_results_and_skill_confirmation_contract_are_exact(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    command = [sys.executable, "-I", "-c", (
        "import sys;sys.path.insert(0," + repr(str(ROOT / "notes_runtime/src")) + ");"
        "from cortex_notes.cli import main;raise SystemExit(main(sys.argv[1:]))"
    ), "--json", "--root", str(root), "registry", "show"]
    shown = subprocess.run(command, capture_output=True, text=True, check=False)
    value = json.loads(shown.stdout)
    assert shown.returncode == 0 and shown.stderr == "" and set(value) == {"status", "exit_code", "command", "data", "issues"}
    assert value["command"] == "notes.registry.show" and shown.stdout.isascii()
    manage = (ROOT / "skills/cortex-notes-manage/SKILL.md").read_text("utf-8")
    for required in ("ask for a natural-language confirmation", "exactly once", "unchanged target and digest", "otherwise make zero delete calls", "--confirmed yes"):
        assert required in manage


def test_notes_surface_is_minimal_and_kb_sources_are_frozen() -> None:
    surface = json.loads((ROOT / "fixtures/capabilities/cortex-notes-surface.json").read_text("utf-8"))
    combined = "\n".join((ROOT / path).read_text("utf-8").casefold() for path in (
        "notes_runtime/src/cortex_notes/core.py", "notes_runtime/src/cortex_notes/cli.py",
        "skills/cortex-notes-ingest/SKILL.md", "skills/cortex-notes-build/SKILL.md", "skills/cortex-notes-manage/SKILL.md",
    ))
    assert surface["excluded"] == ["database", "index", "search", "vector", "ui", "server", "listener", "network", "cloud-sync", "obsidian", "move", "restore", "trash", "tombstone"]
    assert "sqlite" not in combined and "requests" not in combined and "socket" not in combined
    assert not any("cortex_notes" in path.read_text("utf-8") for path in (ROOT / "src/cortex").glob("*.py"))
    assert json.loads((ROOT / "package.json").read_text("utf-8"))["version"] == "7.0.0"
