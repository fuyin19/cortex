from __future__ import annotations

from contextlib import contextmanager
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
ROLE_SKILLS = (
    "cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage",
    "cortex-notes-ingest", "cortex-notes-build", "cortex-notes-manage",
)
ALL_SKILLS = ("cortex", *ROLE_SKILLS)


def _write_json(path: Path, value: object) -> Path:
    path.write_bytes((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return path


def _tools(tmp_path: Path, partitions: tuple[str, ...] = core.TOOL_PARTITIONS) -> Path:
    root = tmp_path / "tools"
    root.mkdir()
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


def _candidate(root: Path, bundle: str, tmp_path: Path, *tags: str, descriptions: dict[str, str] | None = None) -> Path:
    current = core.bundle_config_show(root, bundle, "tags")["data"]["value"]
    value = json.loads(json.dumps(current))
    group = value["groups"][0]
    for item in group["tags"]:
        if descriptions and item["tag"] in descriptions:
            item["description"] = descriptions[item["tag"]]
    for tag in tags:
        group["tags"].append({"tag": tag, "description": (descriptions or {}).get(tag, tag)})
    suffix = "-".join(tags) if tags else "same"
    return _write_json(tmp_path / f"{bundle}-{suffix}-candidate.json", value)


def _add(root: Path, tools: Path | None, tmp_path: Path, bundle: str, partition: str | None, index: int) -> dict[str, object]:
    body = tmp_path / f"body-{index}.md"
    body.write_text(f"# 漢字 {index}\n", encoding="utf-8")
    return core.note_add(
        root, tools, bundle, partition, f"漢字 idea {index}", body,
        f"2026-08-{index + 1:02d}T01:02:03.{index:06d}+08:00",
    )["data"]


def test_notes_v2_profiles_are_exact_canonical_and_note_profile_bytes_match(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    note_bytes = []
    for bundle, _description in core.REGISTRY_ORDER:
        bundle_path = root / bundle
        assert "bundle.json" not in {item.name for item in bundle_path.iterdir()}
        assert {item.name for item in (bundle_path / "profiles").iterdir()} == {
            "note-schema.json", "tags.json", "layout.json",
        }
        note_bytes.append((bundle_path / "profiles/note-schema.json").read_bytes())
        shown = core.bundle_show(root, bundle)["data"]
        assert shown["profiles"]["note"] == core.NOTE_PROFILE
        assert shown["profiles"]["layout"]["version"] == 1
        assert shown["profiles"]["tags"]["version"] == 2
    assert note_bytes[0] == note_bytes[1] == note_bytes[2] == core._json_bytes(core.NOTE_PROFILE)
    assert core.bundle_config_show(root, "daily-notes", "tags")["data"]["value"] == {"version": 2, "groups": []}


def test_notes_profile_driven_lifecycle_across_all_layouts(tmp_path: Path) -> None:
    root, tools = _initialized(tmp_path)
    cases = [
        ("daily-notes", None),
        ("tools-feedback", core.TOOL_PARTITIONS[0]),
        ("ideas", core.IDEA_PARTITIONS[0]),
    ]
    for index, (bundle, partition) in enumerate(cases):
        added = _add(root, tools, tmp_path, bundle, partition, index)
        actual_partition, note, digest = added["partition"], added["note"], added["tree_sha256"]
        unit = root / bundle / str(actual_partition) / str(note)
        metadata_before = (unit / "note.json").read_bytes()
        shown = core.note_show(root, bundle, str(actual_partition), str(note), False)["data"]
        assert shown["tree_sha256"] == digest and "漢字" in shown["body"]
        replacement = tmp_path / f"replacement-{index}.md"
        replacement.write_text("updated\n", encoding="utf-8")
        edited = core.note_edit(root, bundle, str(actual_partition), str(note), False, replacement, str(digest))["data"]
        assert (unit / "note.json").read_bytes() == metadata_before
        archived = core.note_archive(root, bundle, str(actual_partition), str(note), edited["tree_sha256"])["data"]
        assert core.note_delete(root, bundle, str(actual_partition), str(note), True,
                                archived["tree_sha256"], "yes")["data"] == {"deleted": True}
    assert core.validate(root)["status"] == "ok"


def test_notes_daily_derives_once_rejects_conflict_and_preserves_profiles(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    profiles = {path.name: path.read_bytes() for path in (root / "daily-notes/profiles").iterdir()}
    body = tmp_path / "daily.md"
    body.write_text("daily", encoding="utf-8")
    added = core.note_add(root, None, "daily-notes", None, "Daily", body,
                          "2026-08-23T12:00:00.000001+08:00")["data"]
    assert added["partition"] == "20260823"
    assert (root / "daily-notes/20260823/archive/.gitkeep").read_bytes() == b""
    assert profiles == {path.name: path.read_bytes() for path in (root / "daily-notes/profiles").iterdir()}
    with pytest.raises(core.NotesError, match="daily_partition_mismatch"):
        core.note_add(root, None, "daily-notes", "20260824", "Bad", body,
                      "2026-08-23T12:00:00.000002+08:00")
    with pytest.raises(core.NotesError, match="timestamp_invalid"):
        core.note_add(root, None, "daily-notes", None, "Bad", body, "2026-08-23T12:00:00Z")


def test_notes_config_show_set_append_description_and_immutable_profiles(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    note_before = (root / "ideas/profiles/note-schema.json").read_bytes()
    layout_before = (root / "ideas/profiles/layout.json").read_bytes()
    candidate = _candidate(
        root, "ideas", tmp_path, "future-proof",
        descriptions={core.IDEA_PARTITIONS[0]: "Revised", "future-proof": "Future proof"},
    )
    changed = core.bundle_config_set(root, "ideas", "tags", candidate)["data"]
    assert changed["completed_steps"] == ["partition:future-proof", "profiles/tags.json"]
    assert changed["failed_step"] is None and changed["residual_partitions"] == []
    assert changed["profile_updated"] is True
    assert (root / "ideas/future-proof/archive/.gitkeep").is_file()
    assert (root / "ideas/profiles/note-schema.json").read_bytes() == note_before
    assert (root / "ideas/profiles/layout.json").read_bytes() == layout_before
    retried = core.bundle_config_set(root, "ideas", "tags", candidate)["data"]
    assert retried["completed_steps"] == [] and retried["profile_updated"] is False
    for profile in ("note", "layout"):
        with pytest.raises(core.NotesError, match="profile_immutable"):
            core.bundle_config_set(root, "ideas", profile, candidate)


def test_notes_config_rejects_contraction_movement_irrelevant_and_unsafe_before_write(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    profile = root / "ideas/profiles/tags.json"
    before = profile.read_bytes()
    base = json.loads(before)
    candidates = []
    contracted = json.loads(json.dumps(base)); contracted["groups"][0]["tags"].pop(); candidates.append(contracted)
    moved = json.loads(json.dumps(base)); moved["groups"][0]["tags"].reverse(); candidates.append(moved)
    irrelevant = json.loads(json.dumps(base)); irrelevant["groups"].append({"name": "other", "tags": [{"tag": "other", "description": "x"}]}); candidates.append(irrelevant)
    unsafe = json.loads(json.dumps(base)); unsafe["groups"][0]["tags"].append({"tag": "../bad", "description": "x"}); candidates.append(unsafe)
    collision = json.loads(json.dumps(base)); collision["groups"][0]["tags"].append({"tag": core.IDEA_PARTITIONS[0].upper(), "description": "x"}); candidates.append(collision)
    for index, value in enumerate(candidates):
        operand = _write_json(tmp_path / f"invalid-{index}.json", value)
        with pytest.raises(core.NotesError):
            core.bundle_config_set(root, "ideas", "tags", operand)
        assert profile.read_bytes() == before
        assert {item.name for item in (root / "ideas").iterdir()} == {
            "profiles", *core.IDEA_PARTITIONS,
        }


def test_notes_tools_root_is_conditional_and_stale_tools_are_manageable(tmp_path: Path) -> None:
    root, tools = _initialized(tmp_path)
    candidate = _candidate(root, "tools-feedback", tmp_path, "new-tool")
    with pytest.raises(core.NotesError, match="tools_root_required"):
        core.bundle_config_set(root, "tools-feedback", "tags", candidate)
    (tools / "new-tool/.git").mkdir(parents=True)
    core.bundle_config_set(root, "tools-feedback", "tags", candidate, tools)
    body = tmp_path / "body.md"
    body.write_text("body", encoding="utf-8")
    shutil.rmtree(tools / core.TOOL_PARTITIONS[0] / ".git")
    assert core.bundle_show(root, "tools-feedback")["status"] == "ok"
    with pytest.raises(core.NotesError, match="tool_repository_missing"):
        core.note_add(root, tools, "tools-feedback", core.TOOL_PARTITIONS[0], "stale", body,
                      "2026-08-23T00:00:00.000000+08:00")
    assert core.note_add(root, None, "ideas", core.IDEA_PARTITIONS[0], "idea", body,
                         "2026-08-23T00:00:00.000001+08:00")["status"] == "ok"


def test_notes_set_failure_residue_is_deterministic_and_retry_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _tools_root = _initialized(tmp_path)
    candidate = _candidate(root, "ideas", tmp_path, "third", "fourth")
    original = core.os.replace
    monkeypatch.setattr(core.os, "replace", lambda _source, _destination: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(core.NotesError, match="tags_profile_replace_failed") as captured:
        core.bundle_config_set(root, "ideas", "tags", candidate)
    assert captured.value.data == {
        "completed_steps": ["partition:third", "partition:fourth"],
        "failed_step": "profiles/tags.json",
        "residual_partitions": ["third", "fourth"],
        "profile_updated": False,
    }
    with pytest.raises(core.NotesError, match="unregistered_partition_residue") as normal:
        core.bundle_show(root, "ideas")
    assert normal.value.data == {"residual_partitions": ["fourth", "third"]}
    monkeypatch.setattr(core.os, "replace", original)
    resumed = core.bundle_config_set(root, "ideas", "tags", candidate)["data"]
    assert resumed["completed_steps"] == ["partition:third", "partition:fourth", "profiles/tags.json"]
    assert resumed["residual_partitions"] == [] and resumed["profile_updated"] is True
    assert core.bundle_config_set(root, "ideas", "tags", candidate)["data"]["completed_steps"] == []


def test_notes_only_candidate_named_canonical_empty_residue_is_resumable(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    unrelated = _candidate(root, "ideas", tmp_path, "different")
    containing = _candidate(root, "ideas", tmp_path, "orphan")
    residue = root / "ideas/orphan/archive"
    residue.mkdir(parents=True)
    (residue / ".gitkeep").write_bytes(b"")
    with pytest.raises(core.NotesError, match="unregistered_partition_residue"):
        core.bundle_config_set(root, "ideas", "tags", unrelated)
    (residue / "junk").write_bytes(b"x")
    with pytest.raises(core.NotesError, match="unregistered_partition_not_resumable"):
        core.bundle_config_set(root, "ideas", "tags", containing)


def test_notes_lock_precedes_authoritative_reload_and_candidate_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _tools_root = _initialized(tmp_path)
    candidate = _candidate(root, "ideas", tmp_path, "locked")
    held = False
    original_lock, original_read = core._lock, core._read_profiles

    @contextmanager
    def tracked_lock(path: Path):
        nonlocal held
        with original_lock(path):
            held = True
            try: yield
            finally: held = False

    def tracked_read(path: Path, bundle: str):
        assert held
        return original_read(path, bundle)

    monkeypatch.setattr(core, "_lock", tracked_lock)
    monkeypatch.setattr(core, "_read_profiles", tracked_read)
    core.bundle_config_set(root, "ideas", "tags", candidate)


def test_notes_legacy_bundle_and_noncanonical_or_extra_profiles_reject(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    (root / "ideas/bundle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(core.NotesError, match="legacy_bundle_json_rejected"):
        core.bundle_show(root, "ideas")
    (root / "ideas/bundle.json").unlink()
    layout = root / "ideas/profiles/layout.json"
    value = json.loads(layout.read_text("utf-8")); value["policy"] = "python:run"
    _write_json(layout, value)
    with pytest.raises(core.NotesError, match="layout_profile_invalid"):
        core.bundle_show(root, "ideas")


def test_notes_paths_links_wrong_nodes_and_reserved_names_fail_closed(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    tags = json.loads((root / "ideas/profiles/tags.json").read_text("utf-8"))
    for invalid in ("archive", "profiles", ".git", "bundle.json", ".partition-owned", "bad/name", "CON", "trail."):
        value = json.loads(json.dumps(tags)); value["groups"][0]["tags"].append({"tag": invalid, "description": "x"})
        operand = _write_json(tmp_path / (str(len(invalid)) + "-invalid.json"), value)
        with pytest.raises(core.NotesError):
            core.bundle_config_set(root, "ideas", "tags", operand)
    external = tmp_path / "external"; external.mkdir()
    linked = root / "ideas/linked"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        assert "FILE_ATTRIBUTE_REPARSE_POINT" in (ROOT / "notes_runtime/src/cortex_notes/core.py").read_text("utf-8")
    else:
        with pytest.raises(core.NotesError, match="linked_or_reparse_path"):
            core.bundle_show(root, "ideas")


def test_notes_stale_digest_confirmation_collision_and_partial_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, tools = _initialized(tmp_path)
    added = _add(root, tools, tmp_path, "ideas", core.IDEA_PARTITIONS[0], 0)
    partition, note, digest = str(added["partition"]), str(added["note"]), str(added["tree_sha256"])
    body = root / "ideas" / partition / note / "note.md"; body.write_text("external", encoding="utf-8")
    replacement = tmp_path / "replacement.md"; replacement.write_text("new", encoding="utf-8")
    with pytest.raises(core.NotesError, match="stale_note_state"):
        core.note_edit(root, "ideas", partition, note, False, replacement, digest)
    fresh = core.note_show(root, "ideas", partition, note, False)["data"]["tree_sha256"]
    with pytest.raises(core.NotesError, match="delete_confirmation_required"):
        core.note_delete(root, "ideas", partition, note, False, fresh, "no")
    original = Path.unlink; calls = 0
    def fail_second(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("injected")
        original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(core.NotesError, match="note_delete_partial") as captured:
        core.note_delete(root, "ideas", partition, note, False, fresh, "yes")
    assert captured.value.data == {"residue": ["note.json"]}


def test_notes_cli_routes_root_requirement_and_removed_partition_add(tmp_path: Path) -> None:
    root, _tools_root = _initialized(tmp_path)
    command = [sys.executable, "-I", "-c", (
        "import sys;sys.path.insert(0," + repr(str(ROOT / "notes_runtime/src")) + ");"
        "from cortex_notes.cli import main;raise SystemExit(main(sys.argv[1:]))"
    )]
    shown = subprocess.run([*command, "--json", "--root", str(root), "bundle", "config", "show",
                            "--bundle", "ideas", "--profile", "layout"], capture_output=True, text=True, check=False)
    value = json.loads(shown.stdout)
    assert shown.returncode == 0 and value["command"] == "notes.bundle.config.show"
    missing = subprocess.run([*command, "--json", "registry", "show"], capture_output=True, text=True, check=False)
    assert missing.returncode == 2 and json.loads(missing.stdout)["issues"] == [{"code": "root_required"}]
    removed = subprocess.run([*command, "--json", "--root", str(root), "bundle", "partition-add"],
                             capture_output=True, text=True, check=False)
    assert removed.returncode == 2


def test_notes_runtime_packaging_router_and_explicit_only_metadata(tmp_path: Path) -> None:
    assert sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir() and any(path.iterdir())) == sorted(ALL_SKILLS)
    payloads = []
    for name in NOTES_SKILLS:
        skill = ROOT / "skills" / name
        payloads.append({relative: (skill / relative).read_bytes() for relative in (
            "scripts/run_notes.py", "scripts/run_notes.cmd", "scripts/runtime-manifest.json",
            "scripts/vendor/cortex_notes-2.0.0-py3-none-any.whl",
        )})
    assert payloads[0] == payloads[1] == payloads[2]
    assert not (ROOT / "skills/cortex/scripts").exists()
    for name in ALL_SKILLS:
        skill = ROOT / "skills" / name
        metadata = (skill / "agents/openai.yaml").read_text("utf-8")
        assert metadata == "policy:\n  allow_implicit_invocation: false\n"
        text = (skill / "SKILL.md").read_text("utf-8")
        assert "explicit" in text.casefold() and "generic" in text.casefold() and "insufficient" in text.casefold()
    env = dict(os.environ); env["CORTEX_PYTHON"] = os.path.abspath(sys.executable); env["PIP_NO_INDEX"] = "1"
    checked = subprocess.run([sys.executable, str(ROOT / "tools/package_notes_runtime.py"), "--check"], cwd=ROOT,
                             env=env, capture_output=True, text=True, check=False)
    assert checked.returncode == 0 and checked.stderr == "" and "sha256=" in checked.stdout
    runner = ROOT / "skills/cortex-notes-manage/scripts/run_notes.py"
    version = subprocess.run([sys.executable, "-I", str(runner), "--version"], cwd=runner.parent, env=env,
                             capture_output=True, text=True, check=False)
    assert version.returncode == 0 and version.stdout == "cortex-notes 2.0.0\n"
    copied = tmp_path / "skill"; shutil.copytree(ROOT / "skills/cortex-notes-manage", copied)
    wheel = copied / "scripts/vendor/cortex_notes-2.0.0-py3-none-any.whl"; wheel.write_bytes(wheel.read_bytes() + b"tamper")
    failed = subprocess.run([sys.executable, "-I", str(copied / "scripts/run_notes.py"), "--version"], cwd=copied,
                            env=env, capture_output=True, text=True, check=False)
    assert failed.returncode == 70 and "wheel_digest_mismatch" in failed.stderr


def test_notes_surface_is_closed_and_kb_runtime_sources_remain_separate() -> None:
    surface = json.loads((ROOT / "fixtures/capabilities/cortex-notes-surface.json").read_text("utf-8"))
    assert surface["version"] == "2.0.0"
    assert "notes.bundle.partition.add" not in surface["routes"]
    assert surface["profiles"] == ["note-schema.json", "tags.json", "layout.json"]
    assert surface["skill_taxonomy"]["router"] == "cortex"
    assert surface["skill_taxonomy"]["invocation_policy"] == "explicit-only"
    assert surface["skill_taxonomy"]["read_owner"] == "cortex-notes-manage"
    assert set(surface["skill_taxonomy"]["write_owners"].values()) == {
        "cortex-notes-build", "cortex-notes-ingest", "cortex-notes-manage",
    }
    combined = "\n".join((ROOT / path).read_text("utf-8").casefold() for path in (
        "notes_runtime/src/cortex_notes/core.py", "notes_runtime/src/cortex_notes/cli.py",
        "skills/cortex-notes-ingest/SKILL.md", "skills/cortex-notes-build/SKILL.md",
        "skills/cortex-notes-manage/SKILL.md",
    ))
    for forbidden in ("import sqlite", "import requests", "import socket"):
        assert forbidden not in combined
    assert not any("cortex_notes" in path.read_text("utf-8") for path in (ROOT / "src/cortex").glob("*.py"))
    assert json.loads((ROOT / "package.json").read_text("utf-8"))["version"] == "8.0.0"
