from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cortex.constants import PUBLIC_ROUTES, VERSION


ROOT = Path(__file__).parents[1]
CANONICAL = ("cortex-kb-ingest", "cortex-kb-build", "cortex-kb-manage")
RUNTIMES = (*CANONICAL, "cortex-build", "cortex-manage")
WRITE_OWNERS = {
    "manage.init": "cortex-kb-build",
    "manage.config.set": "cortex-kb-build",
    "registry.set": "cortex-kb-build",
    "record.add": "cortex-kb-ingest",
    "record.edit": "cortex-kb-manage",
    "record.delete": "cortex-kb-manage",
}
LEGACY_BODY_SHA256 = {
    "cortex-build": "57e64ad3648aa86aa03ca87d25812b0703e240307ff02bcf577babc21e0d59d9",
    "cortex-manage": "04055c6cd5a598a40d8c11ebdd292112b4764de3f4c82449736216c642eaf346",
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


def _body_bytes(name: str) -> bytes:
    return (ROOT / "skills" / name / "SKILL.md").read_bytes().split(b"---", 2)[2]


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


def test_taxonomy_sc002_legacy_aliases_are_dual_explicit_only_and_frozen() -> None:
    expected = {"cortex-build": "cortex-kb-ingest", "cortex-manage": "cortex-kb-manage"}
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    assert fixture["skill_taxonomy"]["aliases"] == expected
    for name, replacement in expected.items():
        metadata = _frontmatter(_skill(name))
        assert metadata["disable-model-invocation"] == "true"
        assert "Deprecated" in metadata["description"] and "invoke explicitly only" in metadata["description"]
        assert replacement in metadata["description"]
        agent_policy = (ROOT / "skills" / name / "agents" / "openai.yaml").read_text("utf-8")
        assert "allow_implicit_invocation: false" in agent_policy
        assert replacement in agent_policy
        assert fixture["skill_taxonomy"]["invocation_policy"][name] == "explicit-only"


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
    assert "For a **populated** Bundle, Tag 2 and Layout 4 must remain byte-identical" in build
    assert "keep `max_component_length` the same or increase it" in build
    configured = build.index("For **empty configured** with a `max_component_length` increase")
    layout_before_tags = build.index("set the complete Layout 4 candidate before the complete Tag 2 candidate", configured)
    sentinel = build.index("For **empty null sentinel**", layout_before_tags)
    tags_before_layout = build.index("set the complete Tag 2 candidate first, then set Layout 4", sentinel)
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


def test_taxonomy_sc007_core_runtime_and_legacy_bodies_are_invariant() -> None:
    assert VERSION == "7.0.0"
    assert tuple(PUBLIC_ROUTES) == (
        "registry.show", "registry.validate", "registry.resolve", "registry.set",
        "manage.init", "manage.status", "manage.validate", "manage.config.show", "manage.config.set",
        "record.add", "record.edit", "record.show", "record.delete",
    )
    package = json.loads((ROOT / "package.json").read_text("utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert package == {"name": "cortex-record-kb", "version": "7.0.0", "description": "Minimal single-writer record knowledge base."}
    assert plugin["version"] == "7.0.0" and "three canonical skills" in plugin["description"]
    assert (ROOT / "tools" / "migrate_legacy_layout3.py").is_file()
    for name, digest in LEGACY_BODY_SHA256.items():
        assert hashlib.sha256(_body_bytes(name)).hexdigest() == digest


def test_taxonomy_runtime_and_batch_owner_lists_are_ordered_and_exact() -> None:
    fixture = json.loads((ROOT / "fixtures" / "capabilities" / "cortex7-surface.json").read_text("utf-8"))
    tool = (ROOT / "tools" / "package_skill_runtime.py").read_text("utf-8")
    assert fixture["skill_runtime"]["skills"] == list(RUNTIMES)
    assert fixture["batch_helper"]["skills"] == ["cortex-kb-ingest", "cortex-build"]
    assert "RUNTIME_SKILLS = (" in tool and "BATCH_SKILLS = (\"cortex-kb-ingest\", \"cortex-build\")" in tool
    helpers = {name: (ROOT / "skills" / name / "scripts" / "batch_record_add.py") for name in RUNTIMES}
    assert helpers["cortex-kb-ingest"].read_bytes() == helpers["cortex-build"].read_bytes()
    assert all(not path.exists() for name, path in helpers.items() if name not in {"cortex-kb-ingest", "cortex-build"})
