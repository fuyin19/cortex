"""Nine-route Cortex 4 application service."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .canonical import _native_path, canonical_json_bytes, sha256_digest, tree_manifest
from .commands import CommandOutcome
from .constants import FEATURE_IDS, INDEX_BYTES, METHOD_ID, METHOD_VERSION, PUBLIC_LEAF_ROUTES, SCHEMA_IDS, TAG_SCHEMA_PATH
from .contracts import artifact_ref, validate_contract
from .core4 import (
    _split_frontmatter, apply_plan, build_proposal, config_compatible, conflict_artifacts, context_for,
    ensure_state, error, load_artifact, load_tag_schema, make_plan, operation,
    path_preflight, persist_artifact, read_json_operand, render_canonical_reference, resolve_tag,
    rewrite_link_destinations, sanitize_body, state_paths, tag_schema_bytes,
    tree_digest, validate_bundle, validate_tag_schema,
)
from .errors import CortexError, Status
from .native import copy_tree, exists as native_exists, is_dir as native_is_dir, is_file as native_is_file, is_reparse, native_path, reject_reparse_ancestry, remove_tree
from .paths import normalize_relative_path


def _default_schema() -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "types": {
            "reference": {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}},
            "concept": {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}},
            "entity": {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}},
        },
    }


def _issue(exc: CortexError) -> dict[str, Any]:
    return {"rule_id":"service","code":exc.code,"severity":"error","message":str(exc),"path":exc.details.get("path"),"hint":None,"details":exc.details}


def _data(name: str, value: dict[str, Any], *, artifacts: Sequence[dict[str, Any]] = (), issues: Sequence[dict[str, Any]] = (), status: Status = Status.OK) -> CommandOutcome:
    return CommandOutcome(SCHEMA_IDS[name] if status is Status.OK else None, value if status is Status.OK else None, list(artifacts), list(issues), status)


class CortexService:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.absolute()

    def execute(self, route: str, args: Any) -> CommandOutcome:
        method = getattr(self, route.replace(".", "_"), None)
        if method is None: raise error("Route is not supported by Cortex 4", "unknown_route", Status.USAGE_ERROR, route=route)
        return method(args)

    def _require_bundle(self) -> None:
        if not native_is_dir(self.workspace) or is_reparse(self.workspace): raise error("Workspace is not an initialized bundle", "workspace_unavailable")
        legacy = [name for name in ("cortex.yaml", "bundles", "targets.json") if native_exists(self.workspace / name)]
        if legacy: raise error("Legacy Cortex workspace layouts are unsupported", "legacy_workspace_layout_unsupported", Status.UNSUPPORTED, markers=legacy)
        if not native_is_file(self.workspace / "index.md") or not native_is_file(self.workspace / "profiles" / "tag-schema.json"): raise error("Directory is not an initialized Cortex 4 bundle", "bundle_not_initialized")

    def _plan_apply(self, args: Any, route: str) -> CommandOutcome | None:
        if args.plan:
            if not args.apply: raise error("--plan requires --apply", "apply_required", Status.USAGE_ERROR)
            state = ensure_state(self.workspace)
            plan = load_artifact(state, args.plan, "mutation-plan")
            if plan["route"] != route: raise error("Plan belongs to another route", "plan_route_mismatch", Status.CONFLICT)
            receipt, report, journal = apply_plan(self.workspace, plan)
            return _data("verification-receipt", receipt, artifacts=(receipt, report, journal))
        if args.apply: raise error("--apply requires an exact --plan id", "plan_required", Status.USAGE_ERROR)
        return None

    def _reference_lexical(self, value: str, *, role: str) -> tuple[PurePosixPath, Path]:
        normalized=normalize_relative_path(value)
        relative=PurePosixPath(normalized)
        if len(relative.parts)!=2 or relative.parts[0]!="references" or relative.suffix.casefold()!=".md":
            raise error(f"{role} must be one canonical file directly under references/", "invalid_rename", Status.USAGE_ERROR, path=value)
        return relative,self.workspace.joinpath(*relative.parts)

    def _reference_physical(self, relative: PurePosixPath, path: Path, *, role: str, require_file: bool) -> None:
        try:reject_reparse_ancestry(path)
        except OSError as exc:raise error(f"{role} crosses a reparse point","reparse_traversal",Status.POLICY_BLOCKED,path=relative.as_posix()) from exc
        if is_reparse(path):raise error(f"{role} cannot be a reparse point","reparse_traversal",Status.POLICY_BLOCKED,path=relative.as_posix())
        if require_file and not native_is_file(path):raise error("Rename source is unavailable","rename_source_unavailable",Status.VALIDATION_BLOCKED,path=relative.as_posix())

    def _reference_operand(self, value: str, *, role: str, require_file: bool) -> tuple[PurePosixPath, Path]:
        relative,path=self._reference_lexical(value,role=role);self._reference_physical(relative,path,role=role,require_file=require_file);return relative,path

    def _canonical_move_operations(self, old_relative: str, new_relative: str, *, tags: Sequence[str] | None = None) -> list[dict[str, Any]]:
        old,source=self._reference_lexical(old_relative,role="Rename source")
        new,destination=self._reference_lexical(new_relative,role="Rename destination")
        self._reference_physical(old,source,role="Rename source",require_file=True)
        self._reference_physical(new,destination,role="Rename destination",require_file=False)
        if native_exists(destination) and old != new: raise error("Rename destination already exists", "destination_conflict", Status.CONFLICT, path=new.as_posix())
        raw = open(native_path(source), "rb").read(); metadata, body = _split_frontmatter(raw)
        final_tags = list(tags if tags is not None else metadata.get("tags", [])); timestamp = metadata.get("timestamp")
        moved_body = rewrite_link_destinations(body, old.as_posix(), new.as_posix(), {old.as_posix():new.as_posix()}, {entry["path"] for entry in tree_manifest(self.workspace,exclude=(".cortex",))["entries"]})
        moved = render_canonical_reference(new.stem, final_tags, str(timestamp), moved_body)
        operations = [operation("create", new.as_posix(), moved, index=0)] if old != new else [operation("replace", old.as_posix(), moved, raw, index=0)]
        available = {entry["path"] for entry in tree_manifest(self.workspace,exclude=(".cortex",))["entries"]}
        for relative in sorted(path for path in available if path == "index.md" or path.startswith("references/") and path.endswith(".md")):
            if relative == old.as_posix(): continue
            path = self.workspace.joinpath(*PurePosixPath(relative).parts); current = open(native_path(path), "rb").read()
            meta, current_body = _split_frontmatter(current)
            rewritten_body = rewrite_link_destinations(current_body, relative, relative, {old.as_posix():new.as_posix()}, available)
            if rewritten_body == current_body: continue
            if relative == "index.md":
                rewritten = b'---\nokf_version: "0.1"\n---\n' + rewritten_body
            else:
                rewritten = render_canonical_reference(str(meta["title"]), list(meta["tags"]), str(meta["timestamp"]), rewritten_body)
            operations.append(operation("replace", relative, rewritten, current, index=len(operations)))
        if old != new: operations.append(operation("delete", old.as_posix(), expected=raw, index=len(operations)))
        return operations

    def manage_status(self, args: Any) -> CommandOutcome:
        kind = args.kind or "bundle"
        if kind == "method":
            value = {"schema_id":SCHEMA_IDS["method-catalog"],"schema_version":"1.0.0","method_id":METHOD_ID,"method_version":METHOD_VERSION,"features":list(FEATURE_IDS),"routes":list(PUBLIC_LEAF_ROUTES),"schemas":list(SCHEMA_IDS.values())}
            return _data("method-catalog", value)
        if kind != "bundle": raise error("Status kind must be method or bundle", "invalid_status_kind", Status.USAGE_ERROR)
        state = state_paths(self.workspace); state_exists = native_is_dir(state.root)
        initialized = native_is_dir(self.workspace) and native_is_file(self.workspace / "index.md") and native_is_file(self.workspace / "profiles" / "tag-schema.json")
        digest = tree_digest(self.workspace) if initialized else None
        recovery = "not_initialized" if not initialized else "clean"
        if state_exists and native_is_dir(state.journals):
            for entry in os.scandir(native_path(state.journals)):
                journal_path = Path(entry.path) / "journal.json"
                if not entry.is_dir(follow_symlinks=False) or not native_is_file(journal_path): continue
                try:
                    journal = json.loads(open(native_path(journal_path), encoding="utf-8").read())
                    if journal.get("state") not in {"completed","failed","aborted"}: recovery = "required"
                    if journal.get("state") == "recovery_ambiguous": recovery = "ambiguous"
                except Exception: recovery = "ambiguous"
        value={"schema_id":SCHEMA_IDS["workspace-status"],"schema_version":"1.0.0","initialized":initialized,"workspace":str(self.workspace),"bundle_identity":state.identity if initialized or state_exists else None,"state_exists":state_exists,"bundle_tree_digest":digest,"recovery":recovery}
        return _data("workspace-status",value)

    def manage_init(self, args: Any) -> CommandOutcome:
        applied = self._plan_apply(args,"manage.init")
        if applied: return applied
        existing_empty = False
        if native_exists(self.workspace):
            if not native_is_dir(self.workspace) or is_reparse(self.workspace) or any(os.scandir(native_path(self.workspace))):
                raise error("Initialization requires an absent or existing empty bundle root", "init_root_not_empty", Status.CONFLICT)
            existing_empty = True
        if self.workspace == Path(self.workspace.anchor): raise error("Filesystem root cannot be a bundle", "unsafe_bundle_root", Status.POLICY_BLOCKED)
        schema = validate_tag_schema(read_json_operand(args.tag_schema, subject="tag schema")) if args.tag_schema else _default_schema()
        operations=[operation("create","index.md",INDEX_BYTES,index=0),operation("mkdir","references",index=1),operation("mkdir","profiles",index=2),operation("create",TAG_SCHEMA_PATH,tag_schema_bytes(schema),index=3)]
        plan=make_plan(self.workspace,"manage.init",operations,base_digest=tree_digest(self.workspace) if existing_empty else None,tag_digest=sha256_digest(tag_schema_bytes(schema)))
        state=ensure_state(self.workspace); persist_artifact(state,plan)
        return _data("mutation-plan",plan,artifacts=(plan,))

    def manage_config(self, args: Any) -> CommandOutcome:
        applied=self._plan_apply(args,"manage.config")
        if applied:return applied
        self._require_bundle()
        if args.action in {None,"show"}: return _data("tag-schema",load_tag_schema(self.workspace))
        if args.action != "set": raise error("Config supports only show and set", "invalid_config_action", Status.USAGE_ERROR)
        if not args.file: raise error("config set requires --file FILE|-", "config_file_required", Status.USAGE_ERROR)
        new_schema=validate_tag_schema(read_json_operand(args.file,subject="tag schema")); config_compatible(self.workspace,new_schema)
        old=open(native_path(self.workspace/"profiles"/"tag-schema.json"),"rb").read(); content=tag_schema_bytes(new_schema)
        plan=make_plan(self.workspace,"manage.config",[operation("replace",TAG_SCHEMA_PATH,content,old,index=0)],base_digest=tree_digest(self.workspace),tag_digest=sha256_digest(old))
        state=ensure_state(self.workspace);persist_artifact(state,plan)
        return _data("mutation-plan",plan,artifacts=(plan,))

    def build_ingest(self,args:Any)->CommandOutcome:
        if args.sanitize_links and (args.plan or args.apply or args.replace_conflict):
            raise error("--sanitize-links is valid only while creating a proposal plan", "sanitize_mode_forbidden", Status.USAGE_ERROR)
        applied=self._plan_apply(args,"build.ingest")
        if applied:return applied
        self._require_bundle()
        state=state_paths(self.workspace)
        if args.replace_conflict:
            state=ensure_state(self.workspace)
            if args.sanitize_links or args.source or args.context or args.proposal: raise error("Conflict replacement cannot be combined with source/proposal/sanitization", "invalid_ingest_mode", Status.USAGE_ERROR)
            supplied=[load_artifact(state,value,"ingest-conflict") for value in args.replace_conflict]
            if not supplied: raise error("At least one conflict id is required", "conflict_required", Status.USAGE_ERROR)
            proposal_ids={item["proposal_ref"]["artifact_id"] for item in supplied}
            if len(proposal_ids)!=1: raise error("Conflict ids must belong to one proposal", "conflict_set_mismatch", Status.CONFLICT)
            proposal=load_artifact(state,next(iter(proposal_ids)),"ingest-proposal"); context=load_artifact(state,proposal["context_id"],"ingest-context")
            current,creates=conflict_artifacts(self.workspace,context,proposal)
            if {item["artifact_id"] for item in supplied}!={item["artifact_id"] for item in current}: raise error("Replacement requires the exact complete current conflict set", "conflict_set_mismatch", Status.CONFLICT,expected=sorted(item["artifact_id"] for item in current))
            replacements=[]
            for index,item in enumerate(current,len(creates)):
                publication=next(pub for pub in proposal["publications"] if pub["path"]==item["path"]); existing=open(native_path(self.workspace.joinpath(*PurePosixPath(item["path"]).parts)),"rb").read()
                replacements.append(operation("replace",item["path"],base64.b64decode(publication["content_b64"]),existing,index=index))
            plan=make_plan(self.workspace,"build.ingest",creates+replacements,parents=(context,proposal,*current),base_digest=tree_digest(self.workspace),tag_digest=context["tag_schema_digest"])
            persist_artifact(state,plan);return _data("mutation-plan",plan,artifacts=(plan,))
        if args.source:
            if args.context or args.proposal: raise error("Source and proposal modes cannot be combined", "invalid_ingest_mode", Status.USAGE_ERROR)
            context,drafts=context_for(self.workspace,args.source,args.tag);state=ensure_state(self.workspace);persist_artifact(state,context)
            if any(source["unresolved_dimensions"] for source in context["sources"]): return _data("ingest-context",context,artifacts=(context,))
            proposal_input=None
        elif args.context:
            state=ensure_state(self.workspace)
            if not args.proposal: raise error("--context requires --proposal FILE|-", "proposal_required", Status.USAGE_ERROR)
            context=load_artifact(state,args.context,"ingest-context"); proposal_input=read_json_operand(args.proposal,subject="proposal input")
        else: raise error("Ingest requires a source, context/proposal, conflict set, or plan", "invalid_ingest_mode", Status.USAGE_ERROR)
        proposal,link_issues,_=build_proposal(self.workspace,context,proposal_input,args.sanitize_links);persist_artifact(state,proposal)
        if link_issues and not args.sanitize_links:
            return CommandOutcome(None,None,[context,proposal],link_issues,Status.VALIDATION_BLOCKED)
        conflicts,operations=conflict_artifacts(self.workspace,context,proposal)
        for conflict in conflicts:persist_artifact(state,conflict)
        if conflicts:
            issues=[{"rule_id":"ingest-conflict","code":"destination_conflict","severity":"error","message":"A destination already contains different bytes","path":item["path"],"hint":"Review the whole-file diff and retry with every exact conflict id","details":{"conflict_id":item["artifact_id"],"diff":item["diff"]}} for item in conflicts]
            return CommandOutcome(None,None,[context,proposal,*conflicts],issues,Status.CONFLICT)
        plan=make_plan(self.workspace,"build.ingest",operations,parents=(context,proposal),base_digest=context["base_tree_digest"],tag_digest=context["tag_schema_digest"]);persist_artifact(state,plan)
        return _data("mutation-plan",plan,artifacts=(context,proposal,plan),issues=link_issues)

    def manage_validate(self,args:Any)->CommandOutcome:
        self._require_bundle();report,issues=validate_bundle(self.workspace);state=ensure_state(self.workspace);persist_artifact(state,report)
        if report["outcome"]!="pass":return CommandOutcome(None,None,[report],issues,Status.VALIDATION_BLOCKED)
        return _data("validation-report",report,artifacts=(report,),issues=issues)

    def manage_index(self,args:Any)->CommandOutcome:
        self._require_bundle();state=ensure_state(self.workspace);digest=tree_digest(self.workspace);destination=state.indexes/digest/"index.json"
        report,issues=validate_bundle(self.workspace);persist_artifact(state,report)
        if report["outcome"]!="pass":return CommandOutcome(None,None,[report],issues,Status.VALIDATION_BLOCKED)
        path_preflight(self.workspace,state,[])
        entries=[]
        for item in tree_manifest(self.workspace,exclude=(".cortex",))["entries"]:
            if item["path"].startswith("references/") and item["path"].endswith(".md"):entries.append({"path":item["path"],"digest":item["digest"]})
        from .core4 import atomic_bytes
        os.makedirs(native_path(destination.parent),exist_ok=True);atomic_bytes(destination,canonical_json_bytes({"bundle_tree_digest":digest,"references":entries})+b"\n")
        return _data("validation-report",report,artifacts=(report,))

    def manage_repair(self,args:Any)->CommandOutcome:
        applied=self._plan_apply(args,"manage.repair")
        if applied:return applied
        if not native_is_dir(self.workspace) or is_reparse(self.workspace): raise error("Repair requires a real bundle root", "workspace_unavailable")
        operations=[]
        if args.phase=="structural":
            if not native_is_file(self.workspace/"profiles"/"tag-schema.json"): raise error("Structural repair cannot invent a missing TagSchema2", "tag_schema_required", Status.POLICY_BLOCKED)
            if not native_is_file(self.workspace/"index.md"):
                operations.append(operation("create","index.md",INDEX_BYTES,index=len(operations)))
            if not native_is_dir(self.workspace/"references"):operations.append(operation("mkdir","references",index=len(operations)))
            if not native_is_dir(self.workspace/"profiles"):operations.append(operation("mkdir","profiles",index=len(operations)))
        elif args.phase=="link-closure":
            self._require_bundle()
            available={entry["path"] for entry in tree_manifest(self.workspace,exclude=(".cortex",))["entries"]}
            for relative in sorted(path for path in available if path.startswith("references/") and path.endswith(".md")):
                target=self.workspace.joinpath(*PurePosixPath(relative).parts);raw=open(native_path(target),"rb").read();metadata,body=_split_frontmatter(raw)
                output,transforms,_=sanitize_body(body,relative,available,True)
                if transforms:
                    rendered=render_canonical_reference(str(metadata["title"]),list(metadata["tags"]),str(metadata["timestamp"]),output)
                    operations.append(operation("replace",relative,rendered,raw,index=len(operations)))
        else:raise error("Repair phase must be structural or link-closure","invalid_repair_phase",Status.USAGE_ERROR)
        plan=make_plan(self.workspace,"manage.repair",operations,base_digest=tree_digest(self.workspace),tag_digest=sha256_digest(tag_schema_bytes(load_tag_schema(self.workspace))));state=ensure_state(self.workspace);persist_artifact(state,plan);return _data("mutation-plan",plan,artifacts=(plan,))

    def manage_rename(self,args:Any)->CommandOutcome:
        applied=self._plan_apply(args,"manage.rename")
        if applied:return applied
        self._require_bundle()
        if not args.old or not args.new:raise error("rename requires --from and --to","rename_operands_required",Status.USAGE_ERROR)
        ops=self._canonical_move_operations(args.old,args.new)
        plan=make_plan(self.workspace,"manage.rename",ops,base_digest=tree_digest(self.workspace),tag_digest=sha256_digest(tag_schema_bytes(load_tag_schema(self.workspace))));state=ensure_state(self.workspace);persist_artifact(state,plan);return _data("mutation-plan",plan,artifacts=(plan,))

    def manage_retag(self,args:Any)->CommandOutcome:
        applied=self._plan_apply(args,"manage.retag")
        if applied:return applied
        self._require_bundle()
        if args.action!="set" or not args.reference or not args.tags:raise error("Cortex 4 retag requires set --reference PATH --tags TAG","invalid_retag",Status.USAGE_ERROR)
        # Retag re-renders canonical metadata, moves the deterministic path, and updates links.
        relative,path=self._reference_operand(args.reference,role="Retag reference",require_file=True);raw=open(native_path(path),"rb").read();metadata,_body=_split_frontmatter(raw)
        schema=load_tag_schema(self.workspace)
        selected=resolve_tag(schema,args.tags);old_tag=str(metadata["tags"][0]);title=str(metadata["title"])
        if not title.startswith(old_tag+"-"):raise error("Reference title is not canonical","noncanonical_reference_title")
        new_title=selected["tag"]+title[len(old_tag):];new_relative=(relative.parent/(new_title+".md")).as_posix()
        operations=self._canonical_move_operations(relative.as_posix(),new_relative,tags=[selected["tag"],*selected["derived_tags"]])
        plan=make_plan(self.workspace,"manage.retag",operations,base_digest=tree_digest(self.workspace),tag_digest=sha256_digest(tag_schema_bytes(schema)));state=ensure_state(self.workspace);persist_artifact(state,plan);return _data("mutation-plan",plan,artifacts=(plan,))
