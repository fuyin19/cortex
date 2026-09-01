from __future__ import annotations

import json
from pathlib import Path

from cortex.constants import PUBLIC_ROUTES, VERSION


ROOT = Path(__file__).parents[1]
CANONICAL = ("cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage")
RUNTIMES = CANONICAL
NOTES = ("cortex-notes-ingest", "cortex-notes-build", "cortex-notes-manage")
ROLES = (*CANONICAL, *NOTES)
ROUTER = "cortex"
WORKSPACE = "cortex-collaborative-workspace"
WRITE_OWNERS = {
    "align.apply": "cortex-kb-manage",
    "manage.init": "cortex-kb-build",
    "manage.config.set": "cortex-kb-build",
    "registry.set": "cortex-kb-build",
    "record.add": "cortex-kb-ingest",
    "record.edit": "cortex-kb-manage",
    "record.delete": "cortex-kb-manage",
}


def _skill(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text("utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    opening, raw, _body = text.split("---", 2)
    assert opening == ""
    result: dict[str, str] = {}
    for line in raw.strip().splitlines():
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def test_taxonomy_sc001_canonical_discovery_and_exact_role_matrix() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    taxonomy = fixture["skill_taxonomy"]
    assert taxonomy["canonical"] == list(CANONICAL)
    assert taxonomy["write_owners"] == WRITE_OWNERS
    assert taxonomy["read_owner"] == "cortex-kb-manage"
    assert taxonomy["enforcement"] == "skill-contract-only"
    for name in CANONICAL:
        metadata = _frontmatter(_skill(name))
        assert metadata["name"] == name
        assert "disable-model-invocation" not in metadata
        assert metadata["description"]

    ingest, build, manage = (_skill(name) for name in CANONICAL)
    assert "only for `record.add`" in ingest and "Never initialize" in ingest
    assert "only for `manage.init`, `manage.config.set`, and `registry.set`" in build
    assert "Never add, edit, show, or delete records" in build
    for route in ("registry.show", "registry.validate", "registry.resolve", "manage.status", "manage.validate",
                  "manage.config.show", "record.show", "record.edit", "record.delete"):
        assert route in manage
    for forbidden in ("`manage.init`", "`manage.config.set`", "`registry.set`", "`record.add`"):
        assert forbidden in manage


def test_taxonomy_sc002_legacy_aliases_are_removed() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    assert fixture["skill_taxonomy"]["aliases"] == {}
    assert not (ROOT / "skills" / "cortex-build").exists()
    assert not (ROOT / "skills" / "cortex-manage").exists()


def test_taxonomy_sc003_build_has_one_explicit_new_or_resumed_session() -> None:
    build = _skill("cortex-kb-build")
    for required in (
        "exactly one active build session", "explicit lexical absolute `workspace`", "new or resumed Bundle",
        "explicit lexical absolute `kb_root`", "exact `bundle_id`", "complete desired Registry 1 object",
        "Every command and operand is explicit and complete", "**new**", "**resumed, empty configured**",
        "**resumed, empty null sentinel**", "**resumed, populated**",
    ):
        assert required in build


def test_taxonomy_sc004_keyed_monotonic_and_prewrite_contraction_rejection() -> None:
    build = _skill("cortex-kb-build")
    for retained in (
        "Retain every existing group", "every existing tag's group membership",
        "Retain every existing `id` to exact `path` mapping", "may edit descriptions only",
        "Reject every contraction or reassignment before the first write",
    ):
        assert retained in build
    assert "must not remove, rename, move" in build
    assert "must not remove an id or reassign its path" in build


def test_taxonomy_sc005_profile_modes_and_conditional_order_are_exact() -> None:
    build = _skill("cortex-kb-build")
    assert "For a **populated** Bundle, Tag 2 and Layout 5 must remain byte-identical" in build
    assert "keep `max_component_length` the same or increase it" in build
    configured = build.index("For **empty configured** with a `max_component_length` increase")
    layout_before_tags = build.index("set the complete Layout 5 candidate before the complete Tag 2 candidate", configured)
    sentinel = build.index("For **empty null sentinel**", layout_before_tags)
    tags_before_layout = build.index("set the complete Tag 2 candidate first, then set Layout 5", sentinel)
    assert configured < layout_before_tags < sentinel < tags_before_layout
    assert "this tags-before-layout order applies even when the maximum increases" in build
    assert "existing candidate Tag 2 group that contains at least one tag" in build


def test_taxonomy_sc006_first_failure_stops_and_reports_late_residue() -> None:
    build = _skill("cortex-kb-build")
    assert "At the first non-`ok` Result or bootstrap/non-Result failure, stop immediately" in build
    assert "no later write, delete, cleanup mutation, rollback, compensating action, or retry" in build
    for field in ("`completed_steps`", "`failed_step`", "`residual_path`", "`orphan`", "`result`"):
        assert field in build
    assert "late `registry.set` failure" in build and "never delete it" in build


def test_taxonomy_sc007_core_runtime_is_invariant_and_plugin_is_v9() -> None:
    assert VERSION == "8.0.0"
    assert tuple(PUBLIC_ROUTES) == (
        "align.plan", "align.apply",
        "registry.show", "registry.validate", "registry.resolve", "registry.set",
        "manage.init", "manage.status", "manage.validate", "manage.config.show", "manage.config.set",
        "record.add", "record.edit", "record.show", "record.delete",
    )
    package = json.loads((ROOT / "package.json").read_text("utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert package == {"name": "cortex-record-kb", "version": "8.0.0", "description": "Minimal single-writer record knowledge base."}
    assert plugin["version"] == "10.0.0" and "Collaborative Workspace" in plugin["description"]
    assert (ROOT / "tools" / "migrate_layout.py").is_file()


def test_taxonomy_runtime_and_batch_owner_lists_are_ordered_and_exact() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    tool = (ROOT / "tools" / "package_skill_runtime.py").read_text("utf-8")
    assert fixture["skill_runtime"]["skills"] == list(RUNTIMES)
    assert fixture["batch_helper"]["skills"] == ["cortex-kb-ingest"]
    assert "RUNTIME_SKILLS = (" in tool and "BATCH_SKILLS = (\"cortex-kb-ingest\",)" in tool
    helpers = {name: (ROOT / "skills" / name / "scripts" / "batch_record_add.py") for name in RUNTIMES}
    assert helpers["cortex-kb-ingest"].is_file()
    assert all(not path.exists() for name, path in helpers.items() if name != "cortex-kb-ingest")


def test_taxonomy_sc008_router_matrix_is_instruction_only_and_fail_closed() -> None:
    router = _skill(ROUTER)
    assert not (ROOT / "skills" / ROUTER / "scripts").exists()
    for domain in ("KB", "Notes", "Collaborative Workspace"):
        assert domain in router
    for action in ("build", "ingest", "manage"):
        assert action in router
    for role in ROLES:
        assert f"../{role}/SKILL.md" in router
    assert f"../{WORKSPACE}/SKILL.md" in router
    for required in (
        "exactly one domain", "exactly one action", "read and follow exactly one sibling skill",
        "ask for clarification", "required existing state is missing", "fail closed",
        "no scripts, runtime, domain operations, CLI forms, or confirmation logic",
    ):
        assert required in router


def test_taxonomy_sc009_all_eight_are_explicit_only_with_minimal_metadata() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    taxonomy = fixture["skill_taxonomy"]
    assert taxonomy["router"] == ROUTER
    assert taxonomy["canonical_roles"] == list(ROLES)
    assert taxonomy["domain_skills"] == [WORKSPACE]
    assert taxonomy["invocation_policy"] == {name: "explicit-only" for name in (ROUTER, *ROLES, WORKSPACE)}
    assert taxonomy["router_runtime"] is False
    for name in (ROUTER, *ROLES, WORKSPACE):
        skill = _skill(name)
        metadata = _frontmatter(skill)
        assert "Explicit invocation only" in metadata["description"]
        assert "generic" in skill.casefold() and "insufficient" in skill.casefold()
        assert (ROOT / "skills" / name / "agents" / "openai.yaml").read_text("utf-8") == (
            "policy:\n  allow_implicit_invocation: false\n"
        )
    assert (ROOT / "CLAUDE.md").read_text("utf-8") == "@AGENTS.md\n"
    agents = (ROOT / "AGENTS.md").read_text("utf-8")
    assert "one instruction-only non-role router" in agents and "exactly six canonical KB/Notes roles" in agents
    assert "one independent `cortex-collaborative-workspace` domain skill" in agents
