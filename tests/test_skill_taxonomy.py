from __future__ import annotations

import json
from pathlib import Path

from cortex.constants import PUBLIC_ROUTES, VERSION

ROOT = Path(__file__).parents[1]
ROLES = ("kb.ingest", "kb.build", "kb.manage", "notes.ingest", "notes.build", "notes.manage", "collaborative-workspace")


def _router() -> str:
    return (ROOT / "skills/cortex/SKILL.md").read_text("utf-8")


def test_single_discoverable_skill_and_internal_role_matrix() -> None:
    skill_files = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "skills").glob("*/SKILL.md"))
    assert skill_files == ["skills/cortex/SKILL.md"]
    taxonomy = json.loads((ROOT / "fixtures/capabilities/cortex7-surface.json").read_text("utf-8"))["skill_taxonomy"]
    assert taxonomy["router"] == "cortex" and taxonomy["exposed_skills"] == ["cortex"]
    assert taxonomy["internal_roles"] == list(ROLES)
    assert taxonomy["invocation_policy"] == {"cortex": "explicit-only"}
    assert taxonomy["router_runtime"] is True


def test_router_is_explicit_only_and_routes_all_seven_roles() -> None:
    router = _router()
    for required in ("explicitly invokes `cortex`", "exactly one domain", "exactly one action", "fail closed"):
        assert required in router
    for reference in ("kb-ingest", "kb-build", "kb-manage", "notes-ingest", "notes-build", "notes-manage", "collaborative-workspace"):
        assert f"references/{reference}.md" in router
    assert "sibling skill" not in router and "compatibility skill" not in router


def test_internal_adapters_are_private_complete_and_versioned() -> None:
    expected = {
        "kb": (("run_cortex.py", "run_cortex.cmd", "runtime-manifest.json"), "8.1.0"),
        "notes": (("run_notes.py", "run_notes.cmd", "runtime-manifest.json"), "2.1.0"),
        "collaborative-workspace": (("run_collaborative_workspace.py", "run_collaborative_workspace.cmd", "runtime-manifest.json"), "1.1.0"),
    }
    for name, (files, version) in expected.items():
        adapter = ROOT / "skills/cortex/scripts" / name
        assert not (adapter / "SKILL.md").exists()
        assert all((adapter / file).is_file() for file in files)
        assert json.loads((adapter / "runtime-manifest.json").read_text("utf-8"))["version"] == version
    assert (ROOT / "skills/cortex/scripts/kb/batch_record_add.py").is_file()


def test_removed_skill_names_have_no_paths() -> None:
    removed = ("cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage", "cortex-notes-ingest", "cortex-notes-build", "cortex-notes-manage", "cortex-collaborative-workspace")
    assert all(not (ROOT / "skills" / name).exists() for name in removed)
    references = "\n".join(path.read_text("utf-8") for path in (ROOT / "skills/cortex/references").glob("*.md"))
    assert all(name not in references for name in removed)


def test_runtime_and_plugin_versions_are_preserved_or_bumped() -> None:
    assert VERSION == "8.1.0" and len(PUBLIC_ROUTES) == 15
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text("utf-8"))
    assert plugin["version"] == "12.0.0"
    assert "One explicit-only Cortex skill" in plugin["description"]


def test_build_contract_preserves_existing_refactor_semantics() -> None:
    build = (ROOT / "skills/cortex/references/kb-build.md").read_text("utf-8")
    for required in ("exactly one active build session", "Retain every existing group and tag in exact relative order", "Reject every contraction or reassignment before the first write", "Layout 5 must remain byte-identical", "At the first non-`ok` Result or bootstrap/non-Result failure, stop immediately"):
        assert required in build
