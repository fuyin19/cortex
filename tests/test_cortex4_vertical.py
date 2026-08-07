from __future__ import annotations

import io
import json
import os
import importlib
import pkgutil
import re
import shlex
import sys
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cortex.cli import main
from cortex.canonical import _native_path
from cortex.core4 import (
    SimulatedCrash, _owned_scratch, bundle_identity, bundle_lock, config_compatible, load_artifact, markdown_links, sanitize_body,
    persist_artifact, read_json_operand, state_paths, strict_json, tree_digest, validate_bundle,
)
from cortex.errors import CortexError
from cortex.native import native_path
from cortex.constants import FEATURE_IDS, PUBLIC_LEAF_ROUTES, SCHEMA_IDS
from cortex.contracts import load_schema, make_artifact, make_envelope, validate_contract, validate_registry


def schema() -> dict:
    return {
        "schema_version": "2.0.0",
        "types": {
            "reference": {
                "status": "active",
                "identifier_dimension": "project",
                "dimensions": {
                    "project": {
                        "assignment": "user_or_llm",
                        "cardinality": {"min": 1, "max": 1},
                        "values": [{"tag": "project-elevate", "label": "Elevate", "aliases": [], "derived_tags": ["listing-main"]}],
                    },
                    "listing_standard": {
                        "assignment": "derived",
                        "cardinality": {"min": 1, "max": 1},
                        "values": [{"tag": "listing-main", "label": "Main", "aliases": [], "derived_tags": []}],
                    },
                },
            },
            "concept": {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}},
            "entity": {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}},
        },
    }


def expanded_schema() -> dict:
    value = copy.deepcopy(schema())
    value["types"]["reference"]["dimensions"]["project"]["values"].append(
        {"tag":"project-beta","label":"Beta","aliases":["B"],"derived_tags":["listing-main"]}
    )
    return value


def invoke(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    code = main(["--json", *argv])
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    return code, json.loads(output)


def init_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "knowledge"
    source = tmp_path / "tag-schema.json"
    source.write_text(json.dumps(schema()), encoding="utf-8")
    code, planned = invoke(capsys, "--workspace", str(root), "manage", "init", "--tag-schema", str(source))
    assert code == 0
    plan_id = planned["data"]["artifact_id"]
    code, applied = invoke(capsys, "--workspace", str(root), "manage", "init", "--plan", plan_id, "--apply")
    assert code == 0
    assert applied["data"]["artifact_id"].startswith("verification-receipt@")
    return root


def test_closed_surface_and_registry() -> None:
    assert len(PUBLIC_LEAF_ROUTES) == 9
    assert len(FEATURE_IDS) == 9
    assert len(SCHEMA_IDS) == 12
    assert len(validate_registry()) == 12


def test_status_method_ignores_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = invoke(capsys, "--workspace", str(tmp_path / "absent"), "manage", "status", "--kind", "method")
    assert code == 0
    assert payload["data"]["method_id"] == "cortex-okf-workspace-v4"
    assert not (tmp_path / ".cortex").exists()


def test_init_plan_apply_and_one_use(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    assert (root / "index.md").read_bytes() == b'---\nokf_version: "0.1"\n---\n\n# Knowledge Index\n'
    state = next((tmp_path / ".cortex").glob("b-*"))
    assert set(path.name for path in state.iterdir()) >= {"artifacts", "journals", "staging", "backups", "indexes", "locks"}
    plan = next(path.stem.removesuffix(".json") for path in (state / "artifacts").glob("mutation-plan@*.json"))
    code, payload = invoke(capsys, "--workspace", str(root), "manage", "init", "--plan", plan, "--apply")
    assert code == 5
    assert payload["issues"][0]["code"] == "plan_consumed"


def test_ingest_plan_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    draft = tmp_path / "Elevate.md"
    draft.write_text('---\ntype: ""\ntitle: Elevate Note\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n', encoding="utf-8")
    code, planned = invoke(capsys, "--workspace", str(root), "build", "ingest", "--source", str(draft), "--tag", "project-elevate")
    assert code == 0
    assert planned["data"]["artifact_id"].startswith("mutation-plan@")
    code, applied = invoke(capsys, "--workspace", str(root), "build", "ingest", "--plan", planned["data"]["artifact_id"], "--apply")
    assert code == 0
    published = root / "references" / "project-elevate-elevate-note-20260806.md"
    assert published.is_file()
    assert draft.read_text(encoding="utf-8").endswith("Body.\n")


def test_link_sanitization_is_explicit_and_lineaged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    draft = tmp_path / "Elevate.md"
    original = '---\ntype: ""\ntitle: Elevate Broken\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nSee [date](yyyy.mm.dd).\n'
    draft.write_text(original, encoding="utf-8")
    code, blocked = invoke(capsys, "--workspace", str(root), "build", "ingest", "--source", str(draft), "--tag", "project-elevate")
    assert code == 3
    assert blocked["issues"][0]["code"] == "source_link_closure_required"
    context_id = next(item["artifact_id"] for item in blocked["artifacts"] if item["artifact_id"].startswith("ingest-context@"))
    state = next((tmp_path / ".cortex").glob("b-*"))
    context = json.loads(open(_native_path(state / "artifacts" / f"{context_id}.json"), encoding="utf-8").read())
    proposal = {"context_id": context_id, "items": [{"source_id": context["sources"][0]["source_id"], "assignments": {}}]}
    old_stdin = sys.stdin
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(proposal).encode("utf-8")), encoding="utf-8")
        code, planned = invoke(capsys, "--workspace", str(root), "build", "ingest", "--context", context_id, "--proposal", "-", "--sanitize-links")
    finally:
        sys.stdin = old_stdin
    assert code == 0
    proposal_ref = next(item for item in planned["artifacts"] if item["artifact_id"].startswith("ingest-proposal@"))
    proposal2 = json.loads(open(_native_path(state / "artifacts" / f"{proposal_ref['artifact_id']}.json"), encoding="utf-8").read())
    assert proposal2["source_rewrites"][0]["transformations"][0]["after"] == "date"
    assert draft.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("sanitize",[False,True])
def test_unsupported_link_context_publishes_no_proposal_or_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str], sanitize: bool) -> None:
    root=init_bundle(tmp_path,capsys);state=next((tmp_path/".cortex").glob("b-*"));draft=tmp_path/"nested.md"
    raw=b'---\ntype: ""\ntitle: Nested\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\n- [broken](missing.md)\n';draft.write_bytes(raw)
    before={path.name for path in (state/"artifacts").iterdir() if path.name.startswith(("ingest-proposal@","mutation-plan@"))}
    args=["--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate"]
    if sanitize:args.append("--sanitize-links")
    code,payload=invoke(capsys,*args)
    after={path.name for path in (state/"artifacts").iterdir() if path.name.startswith(("ingest-proposal@","mutation-plan@"))}
    assert code==3 and payload["issues"][0]["code"]=="unsupported_markdown_link_context"
    assert before==after and draft.read_bytes()==raw


@pytest.mark.parametrize("sanitize",[False,True])
def test_batch_source_structure_reports_one_ordered_issue_per_source_and_only_context(tmp_path: Path, capsys: pytest.CaptureFixture[str], sanitize: bool) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root);drafts=[]
    for name,body in (("container",b"- [x](missing.md)\n"),("malformed",b"broken [x](missing.md\n")):
        draft=tmp_path/f"{name}.md";draft.write_bytes(b'---\ntype: ""\ntitle: '+name.title().encode()+b'\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\n'+body);drafts.append(draft)
    before={path.name for path in state.artifacts.iterdir() if path.name.startswith(("ingest-proposal@","mutation-plan@"))}
    args=["--workspace",str(root),"build","ingest","--source",str(drafts[0]),"--source",str(drafts[1]),"--tag","project-elevate"]
    if sanitize:args.append("--sanitize-links")
    status,result=invoke(capsys,*args)
    after={path.name for path in state.artifacts.iterdir() if path.name.startswith(("ingest-proposal@","mutation-plan@"))}
    assert status==3 and [item["code"] for item in result["issues"]]==["unsupported_markdown_link_context","malformed_internal_link"]
    assert [item["path"] for item in result["issues"]]==[str(path) for path in drafts]
    assert len(result["artifacts"])==1 and result["artifacts"][0]["artifact_id"].startswith("ingest-context@") and before==after


def test_duplicate_json_key_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schema_version":"2.0.0","schema_version":"2.0.0","types":{}}')
    code, payload = invoke(capsys, "--workspace", str(root), "manage", "config", "set", "--file", str(duplicate))
    assert code == 3
    assert payload["issues"][0]["code"] == "duplicate_json_key"


@pytest.mark.parametrize("payload,code", [
    (b"", "stdin_empty"),
    (b"\xff", "invalid_json"),
    (b"\xef\xbb\xbf{}", "invalid_text_encoding"),
])
def test_stdin_guards(tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: bytes, code: str) -> None:
    root = init_bundle(tmp_path, capsys)
    old_stdin = sys.stdin
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        status, result = invoke(capsys, "--workspace", str(root), "manage", "config", "set", "--file", "-")
    finally:
        sys.stdin = old_stdin
    assert status in {2, 3}
    assert result["issues"][0]["code"] == code


def test_stdin_tty_and_size_guards() -> None:
    class TTY(io.BytesIO):
        def isatty(self) -> bool: return True
    from cortex.core4 import read_json_operand
    with pytest.raises(CortexError, match="interactive") as caught:
        read_json_operand("-", TTY(b"{}"), subject="test")
    assert caught.value.code == "stdin_is_tty"
    with pytest.raises(CortexError) as large:
        strict_json(b" " * (16 * 1024 * 1024 + 1), subject="test")
    assert large.value.code == "input_too_large"


def test_internal_artifact_load_is_uncapped_but_caller_operand_and_filename_identity_are_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root)
    issue={"rule_id":"large","code":"large","severity":"warning","message":"x"*(17*1024*1024),"path":None,"concept_id":None,"operation_id":None,"hint":None,"details":[]}
    artifact=make_artifact("validation-report",{"bundle_identity":"0"*64,"validated_tree_digest":"1"*64,"outcome":"pass","counts":{"errors":0,"warnings":1},"issues":[issue]})
    artifact_path=state.artifacts/f"{artifact['artifact_id']}.json";persist_artifact(state,artifact);assert os.path.getsize(native_path(artifact_path))>16*1024*1024
    assert load_artifact(state,artifact["artifact_id"],"validation-report")["artifact_id"]==artifact["artifact_id"]
    caller=tmp_path/"caller.json";caller.write_bytes(b" "*(16*1024*1024+1))
    with pytest.raises(CortexError) as capped:read_json_operand(str(caller),subject="caller")
    assert capped.value.code=="input_too_large"
    wrong="validation-report@"+"f"*64
    with open(native_path(artifact_path),"rb") as source,open(native_path(state.artifacts/f"{wrong}.json"),"wb") as destination:destination.write(source.read())
    with pytest.raises(CortexError) as mismatch:load_artifact(state,wrong,"validation-report")
    assert mismatch.value.code=="artifact_id_mismatch"


def test_source_stdin_and_sanitize_apply_are_forbidden(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    status, result = invoke(capsys, "--workspace", str(root), "build", "ingest", "--source", "-")
    assert status == 2 and result["issues"][0]["code"] == "source_stdin_unsupported"
    state = next((tmp_path / ".cortex").glob("b-*"))
    plan = next(path.stem.removesuffix(".json") for path in (state / "artifacts").glob("mutation-plan@*.json"))
    status, result = invoke(capsys, "--workspace", str(root), "build", "ingest", "--plan", plan, "--apply", "--sanitize-links")
    assert status == 2 and result["issues"][0]["code"] == "sanitize_mode_forbidden"


def test_human_summary_is_copyable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "knowledge"
    schema_path = tmp_path / "schema.json"; schema_path.write_text(json.dumps(schema()), encoding="utf-8")
    assert main(["--workspace", str(root), "manage", "init", "--tag-schema", str(schema_path)]) == 0
    output = capsys.readouterr().out
    assert "status: ok" in output and "artifact_id: mutation-plan@" in output
    assert str(root) in output and "manage init --plan" in output and "--apply" in output


def test_markdown_tokenizer_skips_safe_contexts_and_sanitizes_supported_forms() -> None:
    body = (
        b"[web](https://example.com) [mail](a@example.com) [frag](#x) ` [code](missing.md) `\n"
        b"```md\n[fenced](missing.md)\n```\n"
        b"[prose](missing.md) ![picture](missing.png) [sheet](missing.xlsx) ![[missing-image.png|Logo]]\n"
        b"[defined][note]\n[note]: missing-note.md\n"
    )
    output, transformations, issues = sanitize_body(body, "references/a.md", {"references/a.md"}, True)
    assert len(transformations) == 5
    assert {item["kind"] for item in transformations} == {"prose", "image", "attachment"}
    assert b"https://example.com" in output and b"a@example.com" in output and b"[code](missing.md)" in output
    assert b"[missing image: picture]" in output and b"[missing attachment: sheet]" in output
    assert all(item["code"] == "source_link_sanitized" for item in issues)


def test_malformed_markdown_link_blocks() -> None:
    with pytest.raises(CortexError) as caught:
        markdown_links(b"broken [label](missing.md\n")
    assert caught.value.code == "malformed_internal_link"


def test_crlf_link_offsets_are_utf8_bytes() -> None:
    body = "前言\r\n[日期](missing.md)\r\n".encode("utf-8")
    output, transformations, _ = sanitize_body(body, "references/a.md", {"references/a.md"}, True)
    item = transformations[0]
    assert body[item["start_byte"]:item["end_byte"]].decode("utf-8") == item["before"]
    assert output == "前言\r\n日期\r\n".encode("utf-8")


def test_multibyte_crlf_malformed_offsets_include_character_and_byte_coordinates() -> None:
    text="前置\r\nbroken [[目标\r\n";body=text.encode("utf-8");start=text.index("[[")
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="malformed_internal_link"
    assert caught.value.details=={"start":start,"line":2,"column":8,"start_byte":body.index(b"[[")}


def test_utf8_prefix_is_lazy_for_success_and_built_once_for_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.core4 as core4
    original=core4._utf8_boundary_prefix;calls=0
    def counted(text: str) -> tuple[int,...]:
        nonlocal calls;calls+=1;return original(text)
    monkeypatch.setattr(core4,"_utf8_boundary_prefix",counted)
    assert markdown_links(("无链接"*100_000+"\r\n").encode("utf-8"))==[] and calls==0
    body="前置\r\n[日期](missing.md)\r\n".encode("utf-8");_,transformations,_=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    item=transformations[0];assert calls==1 and body[item["start_byte"]:item["end_byte"]].decode("utf-8")==item["before"]
    calls=0;malformed="前置\r\nbroken [[目标\r\n".encode("utf-8")
    with pytest.raises(CortexError) as caught:markdown_links(malformed)
    assert calls==1 and caught.value.details["start_byte"]==malformed.index(b"[[")


def test_candidate_driven_token_scan_checks_spans_only_at_bracket_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.core4 as core4
    original=core4._covering_span;calls=[]
    def counted(spans: object,cursor: int,position: int) -> tuple[int,int|None]:
        calls.append(position);return original(spans,cursor,position)  # type: ignore[arg-type]
    monkeypatch.setattr(core4,"_covering_span",counted)
    prefix="界"*200_000;body=(prefix+"[label](missing.md)\n").encode("utf-8")
    tokens=markdown_links(body)
    assert len(tokens)==1 and tokens[0].start==len(prefix) and calls==[len(prefix),len(prefix)]
    output,transformations,_=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert body[transformations[0]["start_byte"]:transformations[0]["end_byte"]]==b"[label](missing.md)" and output.endswith(b"label\n")


def test_same_batch_links_close_without_sanitization(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    first = tmp_path / "first.md"; second = tmp_path / "second.md"
    first.write_text('---\ntype: ""\ntitle: First\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\n[Second](project-elevate-second-20260806.md)\n', encoding="utf-8")
    second.write_text('---\ntype: ""\ntitle: Second\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nDone.\n', encoding="utf-8")
    status, result = invoke(capsys, "--workspace", str(root), "build", "ingest", "--source", str(first), "--source", str(second), "--tag", "project-elevate")
    assert status == 0 and result["data"]["artifact_id"].startswith("mutation-plan@")


def test_source_and_bundle_staleness(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    draft = tmp_path / "draft.md"; draft.write_text('---\ntype: ""\ntitle: Draft\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n', encoding="utf-8")
    status, context_result = invoke(capsys, "--workspace", str(root), "build", "ingest", "--source", str(draft))
    assert status == 0 and context_result["data"]["artifact_id"].startswith("ingest-context@")
    context_id = context_result["data"]["artifact_id"]
    state = state_paths(root); context = load_artifact(state, context_id, "ingest-context")
    proposal = {"context_id":context_id,"items":[{"source_id":context["sources"][0]["source_id"],"assignments":{"project":"project-elevate"}}]}
    proposal_path=tmp_path/"proposal.json";proposal_path.write_text(json.dumps(proposal),encoding="utf-8")
    draft.write_text(draft.read_text(encoding="utf-8")+"changed",encoding="utf-8")
    status, stale = invoke(capsys,"--workspace",str(root),"build","ingest","--context",context_id,"--proposal",str(proposal_path))
    assert status == 5 and stale["issues"][0]["code"] == "stale_source_digest"


def test_conflict_requires_exact_complete_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = init_bundle(tmp_path, capsys)
    for title in ("One","Two"):
        draft=tmp_path/f"{title}.md";draft.write_text(f'---\ntype: ""\ntitle: {title}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nold\n',encoding="utf-8")
        _,plan=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
        invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["data"]["artifact_id"],"--apply")
        draft.write_text(draft.read_text(encoding="utf-8").replace("old","new"),encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(tmp_path/"One.md"),"--source",str(tmp_path/"Two.md"),"--tag","project-elevate")
    assert status==5
    conflicts=[item["artifact_id"] for item in result["artifacts"] if item["artifact_id"].startswith("ingest-conflict@")]
    assert len(conflicts)==2
    assert all(next(detail["value"] for detail in issue["details"] if detail["name"]=="diff").startswith("--- ") for issue in result["issues"])
    status,incomplete=invoke(capsys,"--workspace",str(root),"build","ingest","--replace-conflict",conflicts[0])
    assert incomplete["issues"][0]["code"]=="conflict_set_mismatch"
    assert status==5


@pytest.mark.parametrize("point", ["after_claim","after_stage","before_park","after_park_effect","after_park","before_publish","after_publish_effect","after_publish","after_receipt"])
def test_transaction_faults_resume_exactly(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    case = tmp_path / point
    root = init_bundle(case, capsys)
    draft=case/"draft.md";draft.write_text('---\ntype: ""\ntitle: Fault\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    plan_id=planned["data"]["artifact_id"]
    monkeypatch.setenv("CORTEX_TEST_FAULT",point)
    status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply")
    assert status==6
    monkeypatch.delenv("CORTEX_TEST_FAULT")
    status,resumed=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply")
    assert status==0, resumed["issues"]
    assert resumed["data"]["artifact_id"].startswith("verification-receipt@")


def test_fresh_apply_reuses_stage_proof_with_live_identity_but_resumed_apply_validates_live(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.core4 as core4
    original=core4._validation_snapshot
    def planned(case: Path,title: str) -> tuple[Path,str]:
        root=init_bundle(case,capsys);draft=case/f"{title}.md";draft.write_text(f'---\ntype: ""\ntitle: {title}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
        _,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");return root,result["data"]["artifact_id"]
    fresh_root,fresh_plan=planned(tmp_path/"fresh","FreshProof");calls=[]
    def counted(path: Path):calls.append(Path(path));return original(path)
    monkeypatch.setattr(core4,"_validation_snapshot",counted)
    status,result=invoke(capsys,"--workspace",str(fresh_root),"build","ingest","--plan",fresh_plan,"--apply")
    report_id=next(item["artifact_id"] for item in result["artifacts"] if item["artifact_id"].startswith("validation-report@"));report=load_artifact(state_paths(fresh_root),report_id,"validation-report")
    assert status==0 and len(calls)==1 and calls[0]!=fresh_root and report["bundle_identity"]==state_paths(fresh_root).identity
    monkeypatch.setattr(core4,"_validation_snapshot",original);resumed_root,resumed_plan=planned(tmp_path/"resumed","ResumeProof");monkeypatch.setattr(core4,"_validation_snapshot",counted);calls.clear()
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_stage");status,_=invoke(capsys,"--workspace",str(resumed_root),"build","ingest","--plan",resumed_plan,"--apply");assert status==6
    monkeypatch.delenv("CORTEX_TEST_FAULT");calls.clear();status,_=invoke(capsys,"--workspace",str(resumed_root),"build","ingest","--plan",resumed_plan,"--apply")
    assert status==0 and calls==[resumed_root]


def test_transaction_backup_drift_is_ambiguous_and_preserved(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"draft.md";draft.write_text('---\ntype: ""\ntitle: Drift\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan_id=planned["data"]["artifact_id"]
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_publish");invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    state=state_paths(root);plan=load_artifact(state,plan_id,"mutation-plan");backup=state.backups/plan["digest"]/"bundle"/"index.md"
    with open(native_path(backup),"ab") as handle:handle.write(b"drift")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply")
    assert status==6 and result["issues"][0]["code"]=="recovery_ambiguous"
    assert os.path.exists(native_path(backup)) and os.path.exists(native_path(root))


def test_publication_permission_before_rename_is_actionable_and_exactly_retryable(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.core4 as core4
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"access.md";draft.write_text('---\ntype: ""\ntitle: Access\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan=load_artifact(state_paths(root),planned["data"]["artifact_id"],"mutation-plan");state=state_paths(root);backup=state.backups/plan["digest"]/"bundle";stage=state.staging/plan["digest"]/"bundle"
    original=core4.os.replace;blocked=0
    def deny(source: object,destination: object) -> None:
        nonlocal blocked
        if str(destination)==native_path(backup):blocked+=1;raise PermissionError(13,"busy",str(source),str(destination))
        original(source,destination)
    monkeypatch.setattr(core4.os,"replace",deny)
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");issue=result["issues"][0];details={item["name"]:item["value"] for item in issue["details"]}
    assert status==6 and issue["code"]=="publication_access_blocked" and issue["hint"] and blocked==1
    assert details["phase"]=="park" and details["plan_id"]==plan["artifact_id"] and details["retry_same_plan"] is True
    assert root.is_dir() and stage.is_dir() and not backup.exists()
    monkeypatch.setattr(core4.os,"replace",original);status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==0 and result["data"]["artifact_id"].startswith("verification-receipt@")


@pytest.mark.parametrize(("phase","target_call"),[("park",1),("publish",2)])
def test_publication_barrier_failure_after_rename_effect_replays_barrier_without_ambiguity(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, phase: str, target_call: int) -> None:
    import cortex.core4 as core4
    root=init_bundle(tmp_path,capsys);draft=tmp_path/f"{phase}.md";draft.write_text(f'---\ntype: ""\ntitle: Barrier {phase}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan=load_artifact(state_paths(root),planned["data"]["artifact_id"],"mutation-plan");state=state_paths(root);backup=state.backups/plan["digest"]/"bundle";stage=state.staging/plan["digest"]/"bundle"
    original=core4.fsync_dir;calls=0
    def deny_parent(path: Path) -> None:
        nonlocal calls
        if Path(path)==root.parent:
            calls+=1
            if calls==target_call:raise PermissionError(13,"busy",str(path))
        original(path)
    monkeypatch.setattr(core4,"fsync_dir",deny_parent)
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");details={item["name"]:item["value"] for item in result["issues"][0]["details"]}
    assert status==6 and result["issues"][0]["code"]=="publication_access_blocked" and details["phase"]==phase
    if phase=="park":assert not root.exists() and backup.is_dir() and stage.is_dir()
    else:assert root.is_dir() and backup.is_dir() and not stage.exists()
    monkeypatch.setattr(core4,"fsync_dir",original);status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==0 and result["data"]["artifact_id"].startswith("verification-receipt@")


@pytest.mark.parametrize("namespace_name",["staging","backups"])
def test_unjournaled_transaction_namespace_collision_preserves_sentinel(tmp_path: Path, capsys: pytest.CaptureFixture[str], namespace_name: str) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"collision.md";draft.write_text('---\ntype: ""\ntitle: Collision\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    state=state_paths(root);plan=load_artifact(state,planned["data"]["artifact_id"],"mutation-plan")
    namespace=getattr(state,namespace_name)/plan["digest"];namespace.mkdir();sentinel=namespace/"sentinel";sentinel.write_text("keep",encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==5 and result["issues"][0]["code"]=="scratch_collision"
    assert sentinel.read_text(encoding="utf-8")=="keep" and not (state.journals/plan["digest"]).exists()


@pytest.mark.parametrize(("point","namespace_name"),[("after_claim","staging"),("after_park","backups")])
def test_owned_resume_rejects_unknown_namespace_entries_and_preserves_them(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, point: str, namespace_name: str) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/f"{point}.md";draft.write_text(f'---\ntype: ""\ntitle: {point}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    state=state_paths(root);plan=load_artifact(state,planned["data"]["artifact_id"],"mutation-plan")
    monkeypatch.setenv("CORTEX_TEST_FAULT",point);status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    assert status==6 and (state.journals/plan["digest"]/"journal.json").is_file()
    namespace=getattr(state,namespace_name)/plan["digest"];namespace.mkdir(exist_ok=True);sentinel=namespace/"sentinel";sentinel.write_text("keep",encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==6 and result["issues"][0]["code"]=="recovery_ambiguous"
    assert sentinel.read_text(encoding="utf-8")=="keep"


@pytest.mark.parametrize(("point","namespace_name"),[("after_claim","staging"),("after_stage","backups")])
def test_exact_name_injection_without_journal_bound_marker_is_preserved(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, point: str, namespace_name: str) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/f"inject-{point}.md";draft.write_text(f'---\ntype: ""\ntitle: Inject {point}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    state=state_paths(root);plan=load_artifact(state,planned["data"]["artifact_id"],"mutation-plan")
    monkeypatch.setenv("CORTEX_TEST_FAULT",point);status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    assert status==6
    with open(native_path(state.journals/plan["digest"]/"journal.json"),encoding="utf-8") as handle:journal=json.load(handle)
    assert {"staging_ownership_token","backup_ownership_token"}<=set(journal["events"][0])
    namespace=getattr(state,namespace_name)/plan["digest"];bundle=namespace/"bundle";os.makedirs(native_path(bundle));sentinel=bundle/"sentinel.bin"
    with open(native_path(sentinel),"wb") as handle:handle.write(b"foreign-exact-name")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==6 and result["issues"][0]["code"]=="recovery_ambiguous"
    with open(native_path(sentinel),"rb") as handle:assert handle.read()==b"foreign-exact-name"


def test_exactly_owned_transaction_namespaces_resume_and_cleanup(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"owned.md";draft.write_text('---\ntype: ""\ntitle: Owned\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    state=state_paths(root);plan=load_artifact(state,planned["data"]["artifact_id"],"mutation-plan")
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_park");status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    assert status==6 and sorted(path.name for path in (state.staging/plan["digest"]).iterdir())==["bundle","owner.json"] and sorted(path.name for path in (state.backups/plan["digest"]).iterdir())==["bundle","owner.json"]
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==0,result["issues"]
    assert not (state.staging/plan["digest"]).exists() and not (state.backups/plan["digest"]).exists()


def test_completed_cleanup_preserves_exact_name_namespace_with_invalid_marker(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"completed-marker.md";draft.write_text('---\ntype: ""\ntitle: Completed Marker\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    state=state_paths(root);plan=load_artifact(state,planned["data"]["artifact_id"],"mutation-plan")
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_terminal");status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    assert status==6
    marker=state.backups/plan["digest"]/"owner.json";marker.write_bytes(b"foreign-marker")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==6 and result["issues"][0]["code"]=="recovery_ambiguous" and marker.read_bytes()==b"foreign-marker"


def test_existing_empty_root_initializes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=tmp_path/"empty";root.mkdir()
    schema_path=tmp_path/"schema.json";schema_path.write_text(json.dumps(schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","init","--tag-schema",str(schema_path))
    assert status==0
    status,_=invoke(capsys,"--workspace",str(root),"manage","init","--plan",planned["data"]["artifact_id"],"--apply")
    assert status==0 and (root/"index.md").is_file()


def test_config_is_direct_compatible_transaction_and_cleans_scratch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);candidate=tmp_path/"expanded.json";candidate.write_text(json.dumps(expanded_schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(candidate))
    assert status==0
    state=state_paths(root)
    assert not list(state.staging.glob("config-check-*"))
    status,_=invoke(capsys,"--workspace",str(root),"manage","config","set","--plan",planned["data"]["artifact_id"],"--apply")
    assert status==0
    assert json.loads((root/"profiles"/"tag-schema.json").read_text(encoding="utf-8"))["types"]["reference"]["dimensions"]["project"]["values"][1]["tag"]=="project-beta"


def test_config_allows_removing_an_unused_registered_tag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);expanded=tmp_path/"expanded.json";expanded.write_text(json.dumps(expanded_schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(expanded));assert status==0
    _apply_route(capsys,root,["manage","config","set"],planned)
    reduced=tmp_path/"reduced.json";reduced.write_text(json.dumps(schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(reduced))
    assert status==0,planned["issues"]
    _apply_route(capsys,root,["manage","config","set"],planned)
    tags={item["tag"] for item in json.loads((root/"profiles"/"tag-schema.json").read_text(encoding="utf-8"))["types"]["reference"]["dimensions"]["project"]["values"]}
    assert tags=={"project-elevate"}


def test_config_rejects_removing_active_tags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);expanded=tmp_path/"expanded.json";expanded.write_text(json.dumps(expanded_schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(expanded));assert status==0
    _apply_route(capsys,root,["manage","config","set"],planned)
    _ingest_named(root,tmp_path,capsys,"Beta Used",tag="project-beta")
    candidate=tmp_path/"remove-beta.json";candidate.write_text(json.dumps(schema()),encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(candidate))
    assert status==4 and result["issues"][0]["code"]=="incompatible_tag_schema"


def _ingest_named(root: Path, base: Path, capsys: pytest.CaptureFixture[str], title: str, body: str="Body.\n", tag: str="project-elevate") -> Path:
    draft=base/(title+".md");draft.write_text(f'---\ntype: ""\ntitle: {title}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\n{body}',encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag",tag);assert status==0
    status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",planned["data"]["artifact_id"],"--apply");assert status==0
    return root/"references"/f"{tag}-{title.casefold().replace(' ','-')}-20260806.md"


def test_validation_and_repair_share_unsupported_context_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);reference=_ingest_named(root,tmp_path,capsys,"Nested Canonical")
    reference.write_bytes(reference.read_bytes()+b"- [broken](missing.md)\n")
    status,result=invoke(capsys,"--workspace",str(root),"manage","validate")
    assert status==3 and "unsupported_markdown_link_context" in {item["code"] for item in result["issues"]}
    state=state_paths(root);before={path.name for path in state.artifacts.glob("mutation-plan@*.json")}
    status,result=invoke(capsys,"--workspace",str(root),"manage","repair","--phase","link-closure")
    after={path.name for path in state.artifacts.glob("mutation-plan@*.json")}
    assert status==3 and result["issues"][0]["code"]=="unsupported_markdown_link_context" and before==after


def _apply_route(capsys: pytest.CaptureFixture[str], root: Path, route: list[str], planned: dict) -> dict:
    status,result=invoke(capsys,"--workspace",str(root),*route,"--plan",planned["data"]["artifact_id"],"--apply")
    assert status==0,result["issues"]
    return result


def test_canonical_rename_updates_links_and_frontmatter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);old=_ingest_named(root,tmp_path,capsys,"Alpha")
    index=root/"index.md";index.write_bytes(index.read_bytes()+b"\n[Alpha](references/project-elevate-alpha-20260806.md)\n")
    new="references/project-elevate-beta-note-20260806.md"
    status,planned=invoke(capsys,"--workspace",str(root),"manage","rename","--from",old.relative_to(root).as_posix(),"--to",new)
    assert status==0,planned["issues"]
    _apply_route(capsys,root,["manage","rename"],planned)
    assert not old.exists() and (root/new).is_file()
    assert b"references/project-elevate-beta-note-20260806.md" in index.read_bytes()
    assert b'title: "project-elevate-beta-note-20260806"' in (root/new).read_bytes()


def test_retag_moves_reference_and_updates_links(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);candidate=tmp_path/"expanded.json";candidate.write_text(json.dumps(expanded_schema()),encoding="utf-8")
    _,config=invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(candidate));_apply_route(capsys,root,["manage","config","set"],config)
    old=_ingest_named(root,tmp_path,capsys,"Retag")
    index=root/"index.md";index.write_bytes(index.read_bytes()+b"\n[Retag](references/project-elevate-retag-20260806.md)\n")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","retag","set","--reference",old.relative_to(root).as_posix(),"--tags","B")
    assert status==0,planned["issues"]
    _apply_route(capsys,root,["manage","retag","set"],planned)
    new=root/"references"/"project-beta-retag-20260806.md"
    assert new.is_file() and not old.exists() and b"project-beta-retag" in index.read_bytes()
    assert b'  - "project-beta"\n  - "listing-main"' in new.read_bytes()


def test_structural_and_link_repairs_are_exact_plans(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);os.unlink(native_path(root/"index.md"))
    status,planned=invoke(capsys,"--workspace",str(root),"manage","repair","--phase","structural");assert status==0
    _apply_route(capsys,root,["manage","repair","--phase","structural"],planned);assert (root/"index.md").read_bytes().startswith(b"---")
    reference=_ingest_named(root,tmp_path,capsys,"Broken")
    reference.write_bytes(reference.read_bytes()+b"[missing](missing.md)\n")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","repair","--phase","link-closure");assert status==0
    _apply_route(capsys,root,["manage","repair","--phase","link-closure"],planned)
    assert b"[missing](missing.md)" not in reference.read_bytes() and reference.read_bytes().endswith(b"missing\n")


def test_external_index_does_not_change_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);_ingest_named(root,tmp_path,capsys,"Indexed");before=tree_digest(root)
    status,result=invoke(capsys,"--workspace",str(root),"manage","index")
    assert status==0 and tree_digest(root)==before and not (root/".cortex").exists()
    assert (state_paths(root).indexes/before/"index.json").is_file()


def test_index_uses_one_validation_snapshot_for_report_destination_and_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.core4 as core4
    root=init_bundle(tmp_path,capsys);reference=_ingest_named(root,tmp_path,capsys,"Snapshot");expected=tree_digest(root)
    original=core4.tree_manifest;calls=0
    def counted(*args: object,**kwargs: object) -> dict:
        nonlocal calls;calls+=1;return original(*args,**kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(core4,"tree_manifest",counted)
    status,_=invoke(capsys,"--workspace",str(root),"manage","index")
    payload=json.loads((state_paths(root).indexes/expected/"index.json").read_text(encoding="utf-8"))
    assert status==0 and calls==1 and payload["bundle_tree_digest"]==expected
    assert payload["references"]==[{"path":reference.relative_to(root).as_posix(),"digest":__import__("hashlib").sha256(reference.read_bytes()).hexdigest()}]


def test_full_validation_blocks_noncanonical_and_unsupported_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);reference=_ingest_named(root,tmp_path,capsys,"Validate")
    reference.write_bytes(reference.read_bytes().replace(b'title: "project-elevate-validate-20260806"',b'title: "wrong"'))
    (root/"concept.md").write_text("# unsupported\n",encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"manage","validate")
    codes={item["code"] for item in result["issues"]}
    assert status==3 and {"noncanonical_reference_title","unsupported_document_type"}<=codes


def test_all_schema_objects_are_explicitly_closed_and_semantics_are_enforced() -> None:
    def walk(value: object) -> None:
        if isinstance(value,dict):
            if value.get("type")=="object": assert "additionalProperties" in value
            for item in value.values():walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for name in SCHEMA_IDS:walk(load_schema(name))
    invalid=schema();invalid["types"]["reference"]["dimensions"]["project"]["values"][0]["derived_tags"]=["unknown"]
    with pytest.raises(CortexError) as caught:validate_contract(invalid,"tag-schema")
    assert caught.value.code=="invalid_derived_tag"
    envelope=make_envelope("manage.status","ok",{"schema_id":SCHEMA_IDS["workspace-status"],"schema_version":"1.0.0","initialized":False,"workspace":"x","bundle_identity":None,"state_exists":False,"bundle_tree_digest":None,"recovery":"not_initialized"},SCHEMA_IDS["workspace-status"])
    envelope["data"]["unknown"]=True
    with pytest.raises(CortexError):validate_contract(envelope,"result-envelope")


def test_capability_fixture_matches_closed_surface() -> None:
    fixture=json.loads((Path(__file__).parents[1]/"fixtures"/"capabilities"/"cortex4-surface.json").read_text(encoding="utf-8"))
    assert fixture["routes"]==list(PUBLIC_LEAF_ROUTES) and fixture["schema_count"]==len(SCHEMA_IDS)
    assert fixture["skill_count"]==len(list((Path(__file__).parents[1]/"skills").glob("*/SKILL.md")))==2


def test_component_capacity_fails_before_simulation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);title="x"*245;draft=tmp_path/"long.md"
    draft.write_text(f'---\ntype: ""\ntitle: {title}\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    assert status==3 and result["issues"][0]["code"]=="path_capacity_exceeded"
    state=state_paths(root);assert not list(state.staging.glob("preflight-*"))


@pytest.mark.skipif(os.name!="nt",reason="Windows extended-path regression")
def test_windows_path_beyond_260_is_supported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=tmp_path/("a"*90)/("b"*90)/("c"*90)/"knowledge"
    assert len(str(root))>260
    os.makedirs(native_path(root.parent),exist_ok=True)
    schema_path=tmp_path/"schema.json";schema_path.write_text(json.dumps(schema()),encoding="utf-8")
    status,planned=invoke(capsys,"--workspace",str(root),"manage","init","--tag-schema",str(schema_path));assert status==0
    status,_=invoke(capsys,"--workspace",str(root),"manage","init","--plan",planned["data"]["artifact_id"],"--apply");assert status==0
    assert len(str(root))>260 and os.path.isfile(native_path(root/"index.md"))


def test_reparse_workspace_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target=tmp_path/"target";target.mkdir();link=tmp_path/"link"
    try:link.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip("symlink privilege unavailable")
    schema_path=tmp_path/"schema.json";schema_path.write_text(json.dumps(schema()),encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(link),"manage","init","--tag-schema",str(schema_path))
    assert status in {4,5} and result["issues"][0]["code"] in {"init_root_not_empty","reparse_traversal"}


def test_terminal_journal_consumes_plan_even_if_response_was_interrupted(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"terminal.md";draft.write_text('---\ntype: ""\ntitle: Terminal\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan_id=planned["data"]["artifact_id"]
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_terminal");status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    assert status==6
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply")
    assert status==5 and result["issues"][0]["code"]=="plan_consumed"
    plan=load_artifact(state_paths(root),plan_id,"mutation-plan")
    assert not (state_paths(root).backups/plan["digest"]).exists() and not (state_paths(root).staging/plan["digest"]).exists()


def test_status_reports_parked_recovery_without_live_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"park.md";draft.write_text('---\ntype: ""\ntitle: Park\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    monkeypatch.setenv("CORTEX_TEST_FAULT","after_park");invoke(capsys,"--workspace",str(root),"build","ingest","--plan",planned["data"]["artifact_id"],"--apply");monkeypatch.delenv("CORTEX_TEST_FAULT")
    status,result=invoke(capsys,"--workspace",str(root),"manage","status")
    assert status==0 and result["data"]["initialized"] is False and result["data"]["recovery"]=="required"


def test_bundle_staleness_after_context_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"stale.md";draft.write_text('---\ntype: ""\ntitle: Stale\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft));assert status==0
    context_id=result["data"]["artifact_id"];context=load_artifact(state_paths(root),context_id,"ingest-context")
    proposal={"context_id":context_id,"items":[{"source_id":context["sources"][0]["source_id"],"assignments":{"project":"project-elevate"}}]}
    proposal_path=tmp_path/"proposal.json";proposal_path.write_text(json.dumps(proposal),encoding="utf-8")
    (root/"index.md").write_bytes((root/"index.md").read_bytes()+b"\nchanged\n")
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--context",context_id,"--proposal",str(proposal_path))
    assert status==5 and result["issues"][0]["code"]=="stale_bundle_digest"


def test_bundle_lock_is_fail_closed_and_retryable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"lock.md";draft.write_text('---\ntype: ""\ntitle: Lock\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan_id=planned["data"]["artifact_id"]
    with bundle_lock(state_paths(root)):
        status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply")
    assert status==5 and result["issues"][0]["code"]=="bundle_locked"
    status,_=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan_id,"--apply");assert status==0


def test_artifact_and_rename_traversal_operands_are_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys)
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan","../plan.json","--apply")
    assert status==2 and result["issues"][0]["code"]=="artifact_id_required"
    status,result=invoke(capsys,"--workspace",str(root),"manage","rename","--from","../outside.md","--to","references/x.md")
    assert status==4 and result["issues"][0]["code"]=="path_escape"


def test_exact_complete_conflict_set_replaces_all_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys)
    for title in ("FirstConflict","SecondConflict"):_ingest_named(root,tmp_path,capsys,title)
    for title in ("FirstConflict","SecondConflict"):
        draft=tmp_path/(title+".md");draft.write_bytes(draft.read_bytes().replace(b"Body.",b"Replacement."))
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(tmp_path/"FirstConflict.md"),"--source",str(tmp_path/"SecondConflict.md"),"--tag","project-elevate")
    assert status==5
    conflicts=[item["artifact_id"] for item in result["artifacts"] if item["artifact_id"].startswith("ingest-conflict@")]
    argv=[]
    for item in conflicts:argv.extend(["--replace-conflict",item])
    status,planned=invoke(capsys,"--workspace",str(root),"build","ingest",*argv);assert status==0
    _apply_route(capsys,root,["build","ingest"],planned)
    assert all(b"Replacement." in path.read_bytes() for path in (root/"references").glob("*conflict*.md"))


def test_plan_route_binding_and_preflight_phase_coverage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"bound.md";draft.write_text('---\ntype: ""\ntitle: Bound\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    plan=load_artifact(state_paths(root),planned["data"]["artifact_id"],"mutation-plan")
    assert {item["phase"] for item in plan["path_preflight"]}=={"live","stage","backup","artifact","journal","index"}
    status,result=invoke(capsys,"--workspace",str(root),"manage","rename","--plan",plan["artifact_id"],"--apply")
    assert status==5 and result["issues"][0]["code"]=="plan_route_mismatch"


def test_volume_mismatch_preflight_is_policy_blocked(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"volume.md";draft.write_text('---\ntype: ""\ntitle: Volume\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    values=iter(("volume-a","volume-b"))
    monkeypatch.setattr("cortex.core4.volume_identity",lambda _path:next(values))
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate")
    assert status==4 and result["issues"][0]["code"]=="cross_volume_state"


@pytest.mark.skipif(os.name!="nt",reason="Windows path identity contract")
def test_windows_bundle_identity_is_case_insensitive(tmp_path: Path) -> None:
    path=tmp_path/"Knowledge";path.mkdir()
    assert bundle_identity(path)[1]==bundle_identity(Path(str(path).upper()))[1]


def test_long_path_helper_seams_are_portable_invariants() -> None:
    repository_root=Path(__file__).parents[1]
    preserved={
        "src/cortex/okf.py":b"_native_path",
        "src/cortex/repository.py":b"_native_replace_path",
        "src/cortex/validation.py":b"_native_path",
    }
    for relative,seam in preserved.items():
        current=(repository_root/relative).read_bytes()
        assert seam in current


def test_owned_planning_scratch_is_unique_and_exactly_cleaned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root)
    preserved=state.staging/"plan-check-preexisting";preserved.mkdir();(preserved/"sentinel").write_text("keep",encoding="utf-8")
    with _owned_scratch(state,"plan-check-") as first:
        with _owned_scratch(state,"plan-check-") as second:
            assert first!=second and first.is_dir() and second.is_dir()
        assert first.is_dir() and not second.exists()
    assert preserved.is_dir() and (preserved/"sentinel").read_text(encoding="utf-8")=="keep"
    assert sorted(path.name for path in state.staging.glob("plan-check-*"))==["plan-check-preexisting"]


def test_owned_scratch_concurrency_and_cleanup_faults(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root);barrier=threading.Barrier(4)
    def worker() -> str:
        with _owned_scratch(state,"config-check-") as scratch:
            barrier.wait(timeout=5);return str(scratch)
    with ThreadPoolExecutor(max_workers=4) as pool:paths=list(pool.map(lambda _index:worker(),range(4)))
    assert len(set(paths))==4 and not list(state.staging.glob("config-check-*"))
    with pytest.raises(RuntimeError):
        with _owned_scratch(state,"plan-check-"):raise RuntimeError("planned validation fault")
    assert not list(state.staging.glob("plan-check-*"))
    original_fsync=__import__("cortex.core4",fromlist=["fsync_dir"]).fsync_dir
    def fail_after_cleanup(_path: Path) -> None:raise OSError("barrier fault")
    with pytest.raises(OSError):
        with _owned_scratch(state,"plan-check-") as scratch:monkeypatch.setattr("cortex.core4.fsync_dir",fail_after_cleanup)
    monkeypatch.setattr("cortex.core4.fsync_dir",original_fsync)
    assert not scratch.exists()


def test_config_validation_fault_cleans_only_invocation_scratch(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root);preserved=state.staging/"config-check-preexisting";preserved.mkdir()
    monkeypatch.setattr("cortex.core4.validate_bundle",lambda _root:(_ for _ in ()).throw(RuntimeError("fault")))
    with pytest.raises(RuntimeError):config_compatible(root,expanded_schema())
    assert preserved.is_dir() and sorted(path.name for path in state.staging.glob("config-check-*"))==["config-check-preexisting"]


@pytest.mark.parametrize("code",[1,6])
def test_failed_windows_directory_flush_is_never_success(code: int) -> None:
    from cortex.native import require_directory_flush_success
    with pytest.raises(OSError) as caught:require_directory_flush_success(False,code)
    assert caught.value.errno==code


@pytest.mark.skipif(os.name!="nt",reason="Windows native flush result")
def test_mocked_windows_invalid_function_makes_durability_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes
    import cortex.native as native
    monkeypatch.setattr(native,"_win_handle",lambda _path,write=False:123)
    monkeypatch.setattr(native,"_CloseHandle",lambda _handle:True)
    def failed(_handle: int) -> int:ctypes.set_last_error(1);return 0
    monkeypatch.setattr(native,"_FlushFileBuffers",failed)
    assert native.durability_supported(tmp_path) is False


def test_unsupported_durability_writes_no_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"durability.md";draft.write_text('---\ntype: ""\ntitle: Durability\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,planned=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate");plan=load_artifact(state_paths(root),planned["data"]["artifact_id"],"mutation-plan")
    run=state_paths(root).journals/plan["digest"];assert not run.exists()
    monkeypatch.setattr("cortex.core4.durability_supported",lambda _path:False)
    status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--plan",plan["artifact_id"],"--apply")
    assert status==7 and result["issues"][0]["code"]=="durable_publish_unsupported" and not run.exists()


def test_tokenizer_nested_parentheses_and_nested_labels_are_byte_exact() -> None:
    body="前\r\n[outer [inner]](folder/a(b)c.md \"caption\")\r\n".encode("utf-8")
    tokens=markdown_links(body);assert len(tokens)==1 and tokens[0].destination=="folder/a(b)c.md"
    output,transforms,_=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert body[transforms[0]["start_byte"]:transforms[0]["end_byte"]].decode("utf-8")==transforms[0]["before"]
    assert output=="前\r\nouter [inner]\r\n".encode("utf-8")


@pytest.mark.parametrize("body",[
    b"\\[inline](missing.md)\n",
    b"!\\[image](missing.png)\n",
    b"\\[[wiki.md]]\n",
    b"!\\[[asset.png]]\n",
    b"\\[full]\\[id]\n\n[id]: missing.md\n",
    b"\\[collapsed]\\[]\n\n[collapsed]: missing.md\n",
    b"\\[shortcut]\n\n[shortcut]: missing.md\n",
    b"[use]\n\n\\[use]: missing.md\n",
])
def test_odd_backslash_parity_escapes_supported_link_punctuation(body: bytes) -> None:
    assert markdown_links(body)==[]
    for enabled in (False,True):
        output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
        assert output==body and transformations==[] and issues==[]


@pytest.mark.parametrize("body,expected_before,expected_kind",[
    (b"\\\\[inline](missing.md)\n", "[inline](missing.md)", "prose"),
    (b"\\\\![image](missing.png)\n", "![image](missing.png)", "image"),
    (b"\\\\[[wiki.md]]\n", "[[wiki.md]]", "prose"),
    (b"\\\\![[asset.png]]\n", "![[asset.png]]", "image"),
    (b"\\\\[full][id]\n\n[id]: missing.md\n", "[full][id]", "prose"),
    (b"\\\\[collapsed][]\n\n[collapsed]: missing.md\n", "[collapsed][]", "prose"),
    (b"\\\\[shortcut]\n\n[shortcut]: missing.md\n", "[shortcut]", "prose"),
])
def test_even_backslash_parity_keeps_supported_links_parseable(body: bytes, expected_before: str, expected_kind: str) -> None:
    tokens=markdown_links(body)
    assert len(tokens)==1 and tokens[0].before==expected_before and tokens[0].kind==expected_kind
    unchanged,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},False)
    assert unchanged==body and len(transformations)==1 and issues[0]["code"]=="source_link_closure_required"


@pytest.mark.parametrize("newline",[b"\n",b"\r\n"])
@pytest.mark.parametrize("backslash_count",[1,2,3,4])
def test_reference_definition_escape_parity_agrees_with_root_shortcut_tokenization_and_sanitization(newline: bytes, backslash_count: int) -> None:
    prefix=b"\\"*backslash_count
    body=b"[id]"+newline+newline+prefix+b"[id]: missing.md"+newline
    tokens=markdown_links(body)
    if backslash_count%2:
        assert tokens==[]
        for enabled in (False,True):
            output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
            assert output==body and transformations==[] and issues==[]
    else:
        assert len(tokens)==1 and tokens[0].before=="[id]" and tokens[0].destination=="missing.md"
        unchanged,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},False)
        assert unchanged==body and len(transformations)==1 and issues[0]["code"]=="source_link_closure_required"
        output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},True)
        assert output==b"id"+newline+newline+prefix+b"[id]: missing.md"+newline
        assert len(transformations)==1 and issues[0]["code"]=="source_link_sanitized"


@pytest.mark.parametrize("newline",[b"\n",b"\r\n"])
@pytest.mark.parametrize("backslash_count",[1,2,3,4])
def test_reference_definition_escape_parity_agrees_with_container_shortcut_classification(newline: bytes, backslash_count: int) -> None:
    prefix=b"\\"*backslash_count
    body=b"- [id]"+newline+newline+prefix+b"[id]: missing.md"+newline
    if backslash_count%2:
        assert markdown_links(body)==[]
        for enabled in (False,True):
            output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
            assert output==body and transformations==[] and issues==[]
    else:
        with pytest.raises(CortexError) as caught:markdown_links(body)
        assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["reason"]=="container"
        for enabled in (False,True):
            with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
            assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["path"]=="references/source.md"


def test_even_prefixed_reference_definitions_keep_the_root_indentation_limit() -> None:
    accepted=b"[id]\n\n   \\\\[id]: missing.md\n"
    rejected=b"[id]\n\n    \\\\[id]: missing.md\n"
    assert len(markdown_links(accepted))==1
    assert markdown_links(rejected)==[]


def _pseudo_reference_definition(kind: str, prefix: bytes, newline: bytes) -> bytes:
    definition=prefix+b"[id]: missing.md"
    if kind=="four_space":return b"    "+definition
    if kind=="malformed_destination":return prefix+b"[id]: <missing.md"
    if kind=="malformed_title":return definition+b' "unterminated'
    if kind=="fenced_code":return b"```md"+newline+definition+newline+b"```"
    if kind=="inline_code":return b"`"+definition+b"`"
    if kind=="raw_html":return b"<div>"+newline+definition+newline+b"</div>"
    raise AssertionError(kind)


def _ordered_definition_probe(first: bytes, second: bytes, newline: bytes) -> bytes:
    return first+newline+newline+b"# boundary"+newline+newline+second+newline


@pytest.mark.parametrize("container_use",[False,True])
@pytest.mark.parametrize("prefix_count",[0,1,2,3,4])
@pytest.mark.parametrize("order",["before","after"])
@pytest.mark.parametrize("newline",[b"\n",b"\r\n"])
@pytest.mark.parametrize("pseudo_kind",["four_space","malformed_destination","malformed_title","fenced_code","inline_code","raw_html"])
def test_pseudo_definitions_never_activate_root_or_container_shortcuts(container_use: bool, prefix_count: int, order: str, newline: bytes, pseudo_kind: str) -> None:
    use=b"- [id]" if container_use else b"[id]"
    pseudo=_pseudo_reference_definition(pseudo_kind,b"\\"*prefix_count,newline)
    body=_ordered_definition_probe(pseudo,use,newline) if order=="before" else _ordered_definition_probe(use,pseudo,newline)
    assert markdown_links(body)==[]
    for enabled in (False,True):
        output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
        assert output==body and transformations==[] and issues==[]


@pytest.mark.parametrize("container_use",[False,True])
@pytest.mark.parametrize("prefix_count",[0,1,2,3,4])
@pytest.mark.parametrize("order",["before","after"])
@pytest.mark.parametrize("newline",[b"\n",b"\r\n"])
def test_exact_definitions_alone_activate_shortcut_tokens_and_container_guards(container_use: bool, prefix_count: int, order: str, newline: bytes) -> None:
    use=b"- [id]" if container_use else b"[id]"
    definition=b"\\"*prefix_count+b"[id]: missing.md"
    body=_ordered_definition_probe(definition,use,newline) if order=="before" else _ordered_definition_probe(use,definition,newline)
    if prefix_count%2:
        assert markdown_links(body)==[]
        for enabled in (False,True):
            output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
            assert output==body and transformations==[] and issues==[]
    elif container_use:
        use_start=body.index(b"- [id]")+2
        expected_line=body[:use_start].count(b"\n")+1
        with pytest.raises(CortexError) as caught:markdown_links(body)
        assert caught.value.code=="unsupported_markdown_link_context"
        expected={"reason":"container","syntax_kind":"shortcut_reference","line":expected_line,"column":3,"start_byte":use_start}
        assert all(caught.value.details[key]==value for key,value in expected.items())
        for enabled in (False,True):
            with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
            assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["path"]=="references/source.md"
    else:
        tokens=markdown_links(body)
        assert len(tokens)==1 and tokens[0].before=="[id]" and tokens[0].destination=="missing.md"
        unchanged,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},False)
        assert unchanged==body and len(transformations)==1 and issues[0]["code"]=="source_link_closure_required"


def test_escaped_image_marker_demotes_to_a_normal_link() -> None:
    body=b"\\![image](missing.png)\n"
    tokens=markdown_links(body)
    assert len(tokens)==1 and tokens[0].before=="[image](missing.png)" and tokens[0].kind=="image"
    output,transformations,issues=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert output==b"\\![missing image: image]\n" and len(transformations)==1 and issues[0]["code"]=="source_link_sanitized"


def test_escaped_full_reference_opener_leaves_its_shortcut_tail_parseable() -> None:
    body=b"\\[full][id]\n\n[id]: missing.md\n"
    tokens=markdown_links(body)
    assert len(tokens)==1 and tokens[0].before=="[id]" and tokens[0].destination=="missing.md"


@pytest.mark.parametrize("odd,even",[
    (b"- \\[inline](missing.md)\n",b"- \\\\[inline](missing.md)\n"),
    (b"- !\\[image](missing.png)\n",b"- !\\\\[image](missing.png)\n"),
    (b"- \\[[wiki.md]]\n",b"- \\\\[[wiki.md]]\n"),
    (b"- !\\[[asset.png]]\n",b"- !\\\\[[asset.png]]\n"),
    (b"- \\[full]\\[id]\n\n[id]: missing.md\n",b"- \\\\[full]\\\\[id]\n\n[id]: missing.md\n"),
    (b"- \\[collapsed]\\[]\n\n[collapsed]: missing.md\n",b"- \\\\[collapsed]\\\\[]\n\n[collapsed]: missing.md\n"),
    (b"- \\[shortcut]\n\n[shortcut]: missing.md\n",b"- \\\\[shortcut]\n\n[shortcut]: missing.md\n"),
    (b"- \\[id]: missing.md\n",b"- \\\\[id]: missing.md\n"),
])
def test_unsupported_context_candidate_classification_uses_escape_parity(odd: bytes, even: bytes) -> None:
    assert markdown_links(odd)==[]
    with pytest.raises(CortexError) as caught:markdown_links(even)
    assert caught.value.code=="unsupported_markdown_link_context"


@pytest.mark.parametrize("odd,even",[
    (b"\\[[folder/\nmissing.md]]\n",b"\\\\[[folder/\nmissing.md]]\n"),
    (b"!\\[[folder/\nmissing.png]]\n",b"!\\\\[[folder/\nmissing.png]]\n"),
    (b"\\[x][multi\n space]\n\n[multi space]: missing.md\n",b"\\\\[x][multi\n space]\n\n[multi space]: missing.md\n"),
])
def test_cross_line_candidate_classification_uses_escape_parity(odd: bytes, even: bytes) -> None:
    assert markdown_links(odd)==[]
    with pytest.raises(CortexError) as caught:markdown_links(even)
    assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["reason"]=="cross_line"


def test_tokenizer_leaves_all_code_and_raw_html_contexts_untouched() -> None:
    body=(b"    [indented](missing.md)\n\t[tabbed](missing.md)\n```md\n[fenced](missing.md)\n```\n`[inline](missing.md)`\n"
          b"<div>\n[raw](missing.md)\n</div>\n\n<span data-x='[attribute](missing.md)'>text</span>\n[actual](missing.md)\n")
    tokens=markdown_links(body);assert [item.label for item in tokens]==["actual"]
    output,transforms,_=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert len(transforms)==1 and b"[indented](missing.md)" in output and output.endswith(b"actual\n")


def test_tokenizer_preserves_custom_commonmark_type7_html_blocks() -> None:
    body=b"<x-widget>\n[x](missing.md)\n</x-widget>\n\n[actual](missing.md)\n"
    tokens=markdown_links(body)
    assert [item.label for item in tokens]==["actual"]
    output,transforms,_=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert len(transforms)==1 and output==b"<x-widget>\n[x](missing.md)\n</x-widget>\n\nactual\n"


def test_container_tainted_html_is_not_opaque() -> None:
    body=(b"> <x-widget>\n> [quoted](missing.md)\n> </x-widget>\n>\n"
          b"- <x-widget data-kind='list'>\n  [listed](missing.md)\n  </x-widget>\n\n[actual](missing.md)\n")
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert caught.value.code=="unsupported_markdown_link_context"


@pytest.mark.parametrize("body",[
    b"- outer\n  - paragraph\n    <x-widget>\n    [x](missing.md)\n    </x-widget>\n",
    b"1234567890. paragraph\n            [wide](missing.md)\n",
    b"- outer\n  lazy [continued](missing.md)\n",
    b"> - paragraph\n>   [quoted-list](missing.md)\n",
    b"- [x]\n  [x]: missing.md\n",
])
def test_container_and_lazy_link_contexts_fail_closed(body: bytes) -> None:
    for enabled in (False,True):
        with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
        assert caught.value.code=="unsupported_markdown_link_context"
        assert caught.value.details["path"]=="references/source.md"
        assert {"reason","syntax_kind","line","column","start_byte"}<=caught.value.details.keys()


@pytest.mark.parametrize("body",[
    b"- outer\n  - <x-widget>\n    [nested](missing.md)\n    </x-widget>\n\n",
    b"123456789. <x-widget>\n           [wide](missing.md)\n           </x-widget>\n\n",
    b"> - <x-widget>\n>   [quoted-list](missing.md)\n>   </x-widget>\n>\n",
])
def test_actual_type7_blocks_in_nested_wide_and_quoted_lists_are_unsupported(body: bytes) -> None:
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert caught.value.code=="unsupported_markdown_link_context"


def test_indented_code_inside_list_is_unsupported_not_opaque() -> None:
    body=b"- outer\n\n      [code](missing.md)\n\n[actual](missing.md)\n"
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"


@pytest.mark.parametrize("body",[
    b"> paragraph\n<x>\n[bad](missing.md)\n",
    b"- paragraph\n<x>\n[bad](missing.md)\n",
    b"- paragraph\n\n  <x>\n  [bad](missing.md)\n",
])
def test_container_html_exact_audit_probes_fail_before_opacity(body: bytes) -> None:
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    for enabled in (False,True):
        with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
        assert caught.value.code=="unsupported_markdown_link_context"


@pytest.mark.parametrize("body,kind",[
    (b"[[folder/\nmissing.md]]\n","wiki"),
    (b"![[folder/\nmissing.png]]\n","wiki"),
    (b"[x][multi\n space]\n\n[multi space]: missing.md\n","reference"),
])
def test_cross_line_explicit_link_signatures_fail_closed(body: bytes, kind: str) -> None:
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    assert caught.value.details["reason"]=="cross_line" and caught.value.details["syntax_kind"]==kind
    for enabled in (False,True):
        with pytest.raises(CortexError) as caught:sanitize_body(body,"references/source.md",{"references/source.md"},enabled)
        assert caught.value.code=="unsupported_markdown_link_context"


def test_cross_line_signature_priority_precedes_global_source_position() -> None:
    body=b"[inline\nlabel](missing.md)\n\n[[later\nwiki.md]]\n"
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    assert caught.value.details["reason"]=="cross_line" and caught.value.details["syntax_kind"]=="wiki"
    assert caught.value.details["start_byte"]==body.index(b"[[later")


def test_unsupported_context_reports_physical_utf8_byte_offset() -> None:
    body="- 娈佃惤\n  [閾炬帴](missing.md)\n".encode("utf-8")
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context"
    assert body[caught.value.details["start_byte"]:].startswith("[閾炬帴](missing.md)".encode("utf-8"))


def test_type7_html_after_active_paragraph_is_unsupported() -> None:
    body=b"paragraph\n<x-widget>\n[visible](missing.md)\n</x-widget>\n"
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["reason"]=="ambiguous_html"


def test_four_space_continuation_after_active_paragraph_is_unsupported() -> None:
    body=b"paragraph\n    [visible](missing.md)\n"
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="unsupported_markdown_link_context" and caught.value.details["reason"]=="ambiguous_indentation"


def test_shortcut_reference_links_cannot_escape_closure_or_sanitization() -> None:
    body=b"[x]\n\n[x]: missing.md\n"
    tokens=markdown_links(body)
    assert len(tokens)==1 and tokens[0].before=="[x]" and tokens[0].destination=="missing.md"
    unchanged,transforms,issues=sanitize_body(body,"references/source.md",{"references/source.md"},False)
    assert unchanged==body and len(transforms)==1 and issues[0]["code"]=="source_link_closure_required"
    output,transforms,issues=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert output==b"x\n\n[x]: missing.md\n" and len(transforms)==1 and issues[0]["code"]=="source_link_sanitized"


def test_reference_definitions_support_commonmark_destinations_titles_and_label_normalization() -> None:
    body=(b"[Shortcut]\n[full][multi\t space]\n[collapsed][]\n[next]\n\n"
          b"   [ shortcut ]: <folder/a(b).md> \"angle title\"\n"
          b"[MULTI  SPACE]: folder/(balanced).md 'single title'\n"
          b"[collapsed]: other.md (parenthesized title)\n"
          b"[next]: next.md\n  \"next-line title\"\n")
    tokens=markdown_links(body)
    assert [(item.label,item.destination) for item in tokens]==[
        ("Shortcut","folder/a(b).md"),("full","folder/(balanced).md"),("collapsed","other.md"),("next","next.md")
    ]
    output,transforms,issues=sanitize_body(body,"references/source.md",{"references/source.md"},True)
    assert len(transforms)==4 and len(issues)==4
    assert output.startswith(b"Shortcut\nfull\ncollapsed\nnext\n") and b"[ shortcut ]: <folder/a(b).md> \"angle title\"" in output


@pytest.mark.parametrize("definition",[
    b"[x]: <missing.md\n",
    b"[x]: missing(a.md\n",
    b"[x]: missing.md \"unterminated\n",
    b"    [x]: missing.md\n",
])
def test_malformed_or_indented_reference_definitions_remain_ordinary_text(definition: bytes) -> None:
    body=b"[x]\n\n"+definition
    assert markdown_links(body)==[]


@pytest.mark.parametrize("body",[b"[x](a(b.md\n",b"[x](a.md \"unterminated)\n",b"[[missing\n",b"[x][undefined]\n"])
def test_malformed_token_counterexamples_block(body: bytes) -> None:
    with pytest.raises(CortexError) as caught:markdown_links(body)
    assert caught.value.code=="malformed_internal_link"


@pytest.mark.parametrize("replacement,expected",[
    (b'timestamp: "2026-02-30"',"invalid_reference_timestamp"),
    (b'timestamp: "2026-08-06T25:00:00+08:00"',"invalid_reference_timestamp"),
    (b'tags:\n  - 7\n  - "listing-main"',"invalid_reference_tags"),
])
def test_bundle_validation_reports_malformed_reference_without_internal_error(tmp_path: Path, capsys: pytest.CaptureFixture[str], replacement: bytes, expected: str) -> None:
    root=init_bundle(tmp_path,capsys);reference=_ingest_named(root,tmp_path,capsys,"Malformed");raw=reference.read_bytes()
    raw=re.sub(br'timestamp: "[^"]+"',replacement,raw) if replacement.startswith(b"timestamp") else re.sub(br'tags:\n  - "project-elevate"\n  - "listing-main"',replacement,raw)
    reference.write_bytes(raw);status,result=invoke(capsys,"--workspace",str(root),"manage","validate")
    assert status==3 and expected in {item["code"] for item in result["issues"]}


@pytest.mark.parametrize("source,destination",[("../../outside.md","references/x.md"),("C:/outside.md","references/x.md"),("references/x.md","../outside.md")])
def test_rename_rejects_unconfined_operands_before_filesystem_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, destination: str) -> None:
    from cortex.service import CortexService
    def forbidden(_path: object) -> bool:raise AssertionError("filesystem probe escaped lexical guard")
    monkeypatch.setattr("cortex.service.native_is_file",forbidden);monkeypatch.setattr("cortex.service.native_exists",forbidden)
    with pytest.raises(CortexError):CortexService(tmp_path/"bundle")._canonical_move_operations(source,destination)


def test_rename_reparse_guard_precedes_content_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.service import CortexService
    monkeypatch.setattr("cortex.service.reject_reparse_ancestry",lambda _path:(_ for _ in ()).throw(OSError("junction")))
    monkeypatch.setattr("builtins.open",lambda *_a,**_k:(_ for _ in ()).throw(AssertionError("outside read")))
    with pytest.raises(CortexError) as caught:CortexService(tmp_path/"bundle")._canonical_move_operations("references/a.md","references/b.md")
    assert caught.value.code=="reparse_traversal"


def test_all_packaged_modules_import_without_expanding_public_surface() -> None:
    import cortex
    names=sorted(item.name for item in pkgutil.iter_modules(cortex.__path__))
    for name in names:importlib.import_module(f"cortex.{name}")
    assert set(PUBLIC_LEAF_ROUTES)==set(__import__("cortex.constants",fromlist=["PUBLIC_LEAF_ROUTES"]).PUBLIC_LEAF_ROUTES)
    assert not ({"migration","target_ops","query","governance","adapters"}&set(names))


@pytest.mark.parametrize("left,right",[
    (r"C:\PROGRA~1\Knowledge",r"C:\Program Files\Knowledge"),(r"\\server\share\Knowledge",r"Z:\Knowledge"),
    (r"C:\KNOWLEDGE",r"c:\knowledge"),("/mnt/alias/knowledge","/srv/real/knowledge"),
])
def test_alias_identity_uses_final_platform_resolution(monkeypatch: pytest.MonkeyPatch, left: str, right: str) -> None:
    final=r"\\?\Volume{00000000-0000-0000-0000-000000000001}\canonical\knowledge" if "\\" in left else "/device/canonical/knowledge"
    monkeypatch.setattr("cortex.core4.canonical_handle_path",lambda _path:final.casefold() if "\\" in final else final)
    assert bundle_identity(Path(left))[1]==bundle_identity(Path(right))[1]


@pytest.mark.skipif(os.name!="nt",reason="Windows registry evidence")
def test_long_paths_disabled_host_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:value,_=winreg.QueryValueEx(key,"LongPathsEnabled")
    if value!=0:pytest.skip("host LongPathsEnabled is not zero")
    test_windows_path_beyond_260_is_supported(tmp_path,capsys)


def test_reparse_contract_mock_is_always_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("cortex.native.exists",lambda _path:True);monkeypatch.setattr("cortex.native.is_reparse",lambda _path:True)
    from cortex.native import reject_reparse_ancestry
    with pytest.raises(OSError):reject_reparse_ancestry(tmp_path/"junction"/"bundle")


def test_init_and_config_accept_valid_json_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=tmp_path/"knowledge";old=sys.stdin
    try:
        sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps(schema()).encode()),encoding="utf-8")
        status,planned=invoke(capsys,"--workspace",str(root),"manage","init","--tag-schema","-")
    finally:sys.stdin=old
    assert status==0;_apply_route(capsys,root,["manage","init"],planned)
    try:
        sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps(expanded_schema()).encode()),encoding="utf-8")
        status,planned=invoke(capsys,"--workspace",str(root),"manage","config","set","--file","-")
    finally:sys.stdin=old
    assert status==0;_apply_route(capsys,root,["manage","config","set"],planned)


def test_multiple_stdin_consumers_are_rejected_before_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status,result=invoke(capsys,"--workspace",str(tmp_path/"knowledge"),"build","ingest","--source","-","--proposal","-")
    assert status==2 and result["issues"][0]["code"]=="multiple_stdin_operands"


def test_no_temporary_or_planning_scratch_leaks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root);candidate=tmp_path/"expanded.json";candidate.write_text(json.dumps(expanded_schema()),encoding="utf-8")
    invoke(capsys,"--workspace",str(root),"manage","config","set","--file",str(candidate))
    leaked=[path for path in state.root.rglob("*") if path.name.endswith(".tmp") or path.name.startswith(("plan-check-","config-check-"))]
    assert leaked==[] and not list(tmp_path.glob(".tmp-*"))


def test_blocked_link_human_retry_uses_correct_lineage_and_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"blocked.md";draft.write_text('---\ntype: ""\ntitle: Blocked\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\n[x](missing.md)\n',encoding="utf-8")
    assert main(["--workspace",str(root),"build","ingest","--source",str(draft),"--tag","project-elevate"])==3
    output=capsys.readouterr().out;context_id=next(line.split(": ",1)[1] for line in output.splitlines() if line.startswith("artifact_id:"))
    retry=next(line.split(": ",1)[1] for line in output.splitlines() if line.startswith("next:"));argv=shlex.split(retry)[1:]
    from cortex.cli import _parser
    parsed=_parser().parse_args(argv)
    assert parsed.context==context_id and parsed.proposal=="-" and parsed.sanitize_links and str(root) in retry
    state=state_paths(root);proposals=[load_artifact(state,path.name[:-5],"ingest-proposal") for path in state.artifacts.glob("ingest-proposal@*.json")]
    proposal=next(item for item in proposals if item["context_id"]==context_id)
    assert proposal["parents"][0]["artifact_id"]==context_id


def test_every_human_next_command_round_trips_parser() -> None:
    from cortex.cli import _next,_parser
    plan="mutation-plan@"+"0"*64;context="ingest-context@"+"1"*64;workspace=r"C:\Path With Space\knowledge"
    for route in ("build.ingest","manage.init","manage.config","manage.repair","manage.rename","manage.retag"):
        command=_next(route,plan,workspace);assert command is not None;_parser().parse_args(shlex.split(command)[1:])
    command=_next("build.ingest",context,workspace);assert command is not None;_parser().parse_args(shlex.split(command)[1:])


def test_cleanup_removal_fault_never_deletes_another_scratch(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root=init_bundle(tmp_path,capsys);state=state_paths(root);other=state.staging/"plan-check-other";other.mkdir()
    original=__import__("cortex.core4",fromlist=["remove_tree"]).remove_tree;created:Path|None=None
    def fail_remove(path: Path) -> None:
        nonlocal created;created=path;raise OSError("cleanup denied")
    monkeypatch.setattr("cortex.core4.remove_tree",fail_remove)
    with pytest.raises(OSError):
        with _owned_scratch(state,"plan-check-") as scratch:assert scratch!=other
    assert created==scratch and scratch.is_dir() and other.is_dir()
    monkeypatch.setattr("cortex.core4.remove_tree",original);original(scratch);original(other)


def test_rename_destination_conflict_and_retag_traversal_are_nonmutating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);first=_ingest_named(root,tmp_path,capsys,"FirstSafe");second=_ingest_named(root,tmp_path,capsys,"SecondSafe");before=tree_digest(root)
    status,result=invoke(capsys,"--workspace",str(root),"manage","rename","--from",first.relative_to(root).as_posix(),"--to",second.relative_to(root).as_posix())
    assert status==5 and result["issues"][0]["code"]=="destination_conflict" and tree_digest(root)==before
    status,result=invoke(capsys,"--workspace",str(root),"manage","retag","set","--reference","../../outside.md","--tags","project-elevate")
    assert status==4 and result["issues"][0]["code"]=="path_escape" and tree_digest(root)==before


@pytest.mark.parametrize("payload,code",[
    (b"", "stdin_empty"),(b"\xff","invalid_json"),(b"\xef\xbb\xbf{}","invalid_text_encoding"),
    (b'{"schema_version":"2.0.0","schema_version":"2.0.0","types":{}}',"duplicate_json_key"),
])
def test_init_stdin_negative_matrix(tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: bytes, code: str) -> None:
    old=sys.stdin
    try:
        sys.stdin=io.TextIOWrapper(io.BytesIO(payload),encoding="utf-8")
        status,result=invoke(capsys,"--workspace",str(tmp_path/"knowledge"),"manage","init","--tag-schema","-")
    finally:sys.stdin=old
    assert status in {2,3} and result["issues"][0]["code"]==code


def test_proposal_stdin_negative_matrix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root=init_bundle(tmp_path,capsys);draft=tmp_path/"proposal.md";draft.write_text('---\ntype: ""\ntitle: Proposal\ndescription: ""\ntags: []\ntimestamp: "2026-08-06"\n---\nBody.\n',encoding="utf-8")
    _,context=invoke(capsys,"--workspace",str(root),"build","ingest","--source",str(draft));context_id=context["data"]["artifact_id"]
    for payload,code in ((b"", "stdin_empty"),(b"\xff","invalid_json"),(b'{"context_id":"x","context_id":"y","items":[]}',"duplicate_json_key")):
        old=sys.stdin
        try:
            sys.stdin=io.TextIOWrapper(io.BytesIO(payload),encoding="utf-8")
            status,result=invoke(capsys,"--workspace",str(root),"build","ingest","--context",context_id,"--proposal","-")
        finally:sys.stdin=old
        assert status in {2,3} and result["issues"][0]["code"]==code


def test_oversize_cli_stdin_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old=sys.stdin
    try:
        sys.stdin=io.TextIOWrapper(io.BytesIO(b" "*(16*1024*1024+1)),encoding="utf-8")
        status,result=invoke(capsys,"--workspace",str(tmp_path/"knowledge"),"manage","init","--tag-schema","-")
    finally:sys.stdin=old
    assert status==3 and result["issues"][0]["code"]=="input_too_large"


@pytest.mark.skipif(os.name!="nt",reason="Windows 8.3 alias evidence")
def test_real_windows_short_path_alias_identity_when_available(tmp_path: Path) -> None:
    import ctypes
    path=tmp_path/"Long Directory Name";path.mkdir();buffer=ctypes.create_unicode_buffer(32768)
    written=ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer))
    if not written or buffer.value.casefold()==str(path).casefold():pytest.skip("8.3 aliases unavailable on this volume")
    assert bundle_identity(path)[1]==bundle_identity(Path(buffer.value))[1]
