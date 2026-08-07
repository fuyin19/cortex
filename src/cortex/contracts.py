"""Cortex 4 public JSON contracts and content-addressed artifacts."""

from __future__ import annotations

import json
import sys
import unicodedata
import uuid
import base64
import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .canonical import artifact_digest, canonical_json_bytes
from .constants import FEATURE_IDS, METHOD_ID, METHOD_VERSION, PUBLIC_LEAF_ROUTES, SCHEMA_IDS
from .errors import CortexError, Status

_SOURCE = Path(__file__).resolve().parents[2] / "schemas"
_INSTALLED = Path(sys.prefix) / "cortex" / "schemas"
SCHEMA_DIRECTORY = _SOURCE if _SOURCE.is_dir() else _INSTALLED
_ID_TO_NAME = {value: key for key, value in SCHEMA_IDS.items()}
_NON_ARTIFACTS = {"result-envelope", "method-catalog", "workspace-status", "tag-schema", "ingest-proposal-input"}


def _error(message: str, code: str, **details: object) -> CortexError:
    return CortexError(message, status=Status.VALIDATION_BLOCKED, code=code, details=dict(details))


def _name(value: str) -> str:
    name = _ID_TO_NAME.get(value, value)
    if name not in SCHEMA_IDS:
        raise _error("Unknown public schema", "unknown_schema", schema=value)
    return name


@lru_cache(maxsize=None)
def load_schema(name_or_id: str) -> dict[str, Any]:
    name = _name(name_or_id)
    path = SCHEMA_DIRECTORY / f"{name}.schema.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
    except Exception as exc:
        raise _error("Public schema is unavailable or invalid", "invalid_schema_registry", schema=name) from exc
    if value.get("$id") != SCHEMA_IDS[name]:
        raise _error("Public schema id differs from registry", "schema_id_mismatch", schema=name)
    return value


def validate_contract(instance: Any, schema: str | Mapping[str, Any]) -> None:
    resolved = load_schema(schema) if isinstance(schema, str) else dict(schema)
    errors = sorted(
        Draft202012Validator(resolved, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(item) for item in first.absolute_path) if first.absolute_path else ""
        raise _error(
            "Public contract validation failed",
            "contract_validation_failed",
            schema=resolved.get("$id"), pointer=pointer, reason=first.message, error_count=len(errors),
        )
    if isinstance(instance, Mapping) and instance.get("artifact_id"):
        digest = artifact_digest(instance)
        name = _ID_TO_NAME.get(str(instance.get("schema_id")), "artifact")
        if instance.get("digest") != digest or instance.get("artifact_id") != f"{name}@{digest}":
            raise _error("Artifact identity is invalid", "artifact_digest_mismatch")
    if isinstance(instance, Mapping):
        _validate_semantics(instance, _ID_TO_NAME.get(str(resolved.get("$id")), ""))


def _refs(value: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    return [(str(item["schema_id"]), str(item["artifact_id"]), str(item["digest"])) for item in value]


def _validate_refs(value: Iterable[Mapping[str, Any]]) -> None:
    for schema_id, artifact_id, digest in _refs(value):
        schema_name = schema_id.split(":")[-2] if schema_id.startswith("urn:cortex:schema:") else ""
        if artifact_id != f"{schema_name}@{digest}":
            raise _error("Artifact reference identity is invalid", "artifact_ref_mismatch", artifact_id=artifact_id)


def _validate_semantics(instance: Mapping[str, Any], name: str) -> None:
    if name == "result-envelope":
        expected = {"ok":0,"usage_error":2,"validation_blocked":3,"policy_blocked":4,"conflict":5,"interrupted":6,"unsupported":7}[str(instance["status"])]
        if instance["exit_code"] != expected: raise _error("Envelope status and exit code disagree", "status_exit_mismatch")
        if instance["status"] != "ok" and (instance["data"] is not None or instance["data_schema_id"] is not None): raise _error("Error envelopes cannot carry data", "error_data_forbidden")
        if (instance["data"] is None) != (instance["data_schema_id"] is None): raise _error("Envelope data and schema must be present together", "data_schema_mismatch")
        if instance["data_schema_id"] is not None:
            validate_contract(instance["data"], str(instance["data_schema_id"]))
        _validate_refs(instance["artifacts"])
        return
    if name == "method-catalog":
        if instance["method_id"] != METHOD_ID or instance["method_version"] != METHOD_VERSION or tuple(instance["features"]) != FEATURE_IDS or tuple(instance["routes"]) != PUBLIC_LEAF_ROUTES or tuple(instance["schemas"]) != tuple(SCHEMA_IDS.values()): raise _error("Method catalog differs from the closed Cortex 4 surface", "method_catalog_mismatch")
        return
    if name == "workspace-status":
        if not instance["initialized"]:
            if instance["bundle_tree_digest"] is not None or instance["recovery"] not in {"not_initialized", "required", "ambiguous"}:
                raise _error("Uninitialized workspace status is inconsistent", "workspace_status_mismatch")
        if not instance["state_exists"] and instance["bundle_identity"] is not None:
            raise _error("Workspace identity requires owned external state", "workspace_status_mismatch")
        return
    if name == "tag-schema":
        types = instance["types"]
        for kind in ("concept","entity"):
            if types[kind] != {"status":"unconfigured","identifier_dimension":None,"dimensions":{}}: raise _error("Concept and entity must remain unconfigured", "invalid_tag_schema", type=kind)
        reference=types["reference"]
        if reference["status"]=="unconfigured":
            if reference["identifier_dimension"] is not None or reference["dimensions"]: raise _error("Unconfigured reference policy must be empty", "invalid_tag_schema")
            return
        identifier=reference["identifier_dimension"]
        if identifier not in reference["dimensions"]: raise _error("Identifier dimension is missing", "invalid_tag_schema")
        global_tags:set[str]=set()
        for dimension_name, dimension in reference["dimensions"].items():
            minimum,maximum=dimension["cardinality"]["min"],dimension["cardinality"]["max"]
            if minimum>maximum or maximum>len(dimension["values"]): raise _error("Tag cardinality is invalid", "invalid_tag_schema", dimension=dimension_name)
            if dimension_name==identifier and (dimension["assignment"]!="user_or_llm" or (minimum,maximum)!=(1,1)): raise _error("Identifier assignment is invalid", "invalid_tag_schema")
            if dimension_name!=identifier and dimension["assignment"]!="derived": raise _error("Non-identifier dimensions must be derived", "invalid_tag_schema")
            identities:dict[str,str]={}
            for value in dimension["values"]:
                if value["tag"] in global_tags: raise _error("Tags must be globally unique", "duplicate_tag", tag=value["tag"])
                global_tags.add(value["tag"])
                for identity in (value["tag"],value["label"],*value["aliases"]):
                    key=unicodedata.normalize("NFC",identity).strip().casefold()
                    if key in identities and identities[key]!=value["tag"]: raise _error("Tag identities collide", "tag_normalization_collision", value=identity)
                    identities[key]=value["tag"]
        derived={value["tag"] for dim_name,dim in reference["dimensions"].items() if dim_name!=identifier for value in dim["values"]}
        for value in reference["dimensions"][identifier]["values"]:
            if any(tag not in derived for tag in value["derived_tags"]): raise _error("Identifier declares an unknown derived tag", "invalid_derived_tag", tag=value["tag"])
            for dim_name,dim in reference["dimensions"].items():
                if dim_name==identifier: continue
                registered={item["tag"] for item in dim["values"]};count=sum(tag in registered for tag in value["derived_tags"])
                if not dim["cardinality"]["min"]<=count<=dim["cardinality"]["max"]: raise _error("Derived tags violate dimension cardinality", "invalid_derived_tag", tag=value["tag"], dimension=dim_name)
        return
    if name == "ingest-proposal-input":
        ids=[item["source_id"] for item in instance["items"]]
        if len(ids)!=len(set(ids)): raise _error("Proposal input sources must be unique", "duplicate_ingest_source")
        return
    if "parents" in instance:
        _validate_refs(instance["parents"])
        refs=_refs(instance["parents"])
        if refs != sorted(set(refs)): raise _error("Artifact parents must be unique and canonically ordered", "artifact_parent_order")
    if name == "ingest-context":
        ids=[item["source_id"] for item in instance["sources"]];paths=[item["source_path"] for item in instance["sources"]]
        if len(ids)!=len(set(ids)) or len(paths)!=len(set(paths)): raise _error("Context sources must be unique", "duplicate_ingest_source")
        for source in instance["sources"]:
            if source["source_id"][4:] != source["source_digest"]: raise _error("Source identity and digest differ", "source_identity_mismatch")
            unresolved=[item["dimension"] for item in source["unresolved_dimensions"]]
            if len(unresolved)!=len(set(unresolved)) or set(unresolved)&set(source["resolved_assignments"]): raise _error("Context dimension coverage overlaps", "context_dimension_mismatch")
            for item in source["unresolved_dimensions"]:
                tags=[candidate["tag"] for candidate in item["candidates"]]
                if len(tags)!=len(set(tags)): raise _error("Context candidates must be unique", "duplicate_context_candidate")
    elif name == "ingest-proposal":
        parent=instance["parents"][0]
        if instance["context_id"]!=parent["artifact_id"]: raise _error("Proposal context differs from parent", "proposal_context_mismatch")
        item_ids=[item["source_id"] for item in instance["items"]];rewrite_ids=[item["source_id"] for item in instance["source_rewrites"]];publication_ids=[item["source_id"] for item in instance["publications"]]
        if not item_ids or item_ids!=rewrite_ids or item_ids!=publication_ids or len(item_ids)!=len(set(item_ids)): raise _error("Proposal source coverage/order is not exact", "proposal_coverage_mismatch")
        for rewrite in instance["source_rewrites"]:
            previous_end=0
            for ordinal,transformation in enumerate(rewrite["transformations"],1):
                if transformation["ordinal"]!=ordinal or transformation["start_byte"]<previous_end or transformation["end_byte"]<transformation["start_byte"]: raise _error("Transformations must be ordered and non-overlapping", "invalid_source_rewrite")
                previous_end=transformation["end_byte"]
        paths=[item["path"] for item in instance["publications"]]
        if len(paths)!=len(set(paths)): raise _error("Proposal destinations must be unique", "proposal_destination_collision")
        for publication in instance["publications"]:
            payload=base64.b64decode(publication["content_b64"],validate=True)
            if hashlib.sha256(payload).hexdigest()!=publication["content_digest"]: raise _error("Publication content digest differs", "publication_digest_mismatch")
    elif name == "ingest-conflict":
        expected={
            (instance["context_ref"]["schema_id"], instance["context_ref"]["artifact_id"], instance["context_ref"]["digest"]),
            (instance["proposal_ref"]["schema_id"], instance["proposal_ref"]["artifact_id"], instance["proposal_ref"]["digest"]),
        }
        if set(_refs(instance["parents"]))!=expected: raise _error("Conflict lineage differs from refs", "ingest_conflict_lineage_mismatch")
    elif name == "mutation-plan":
        ids=[item["id"] for item in instance["operations"]]
        if ids != [f"op-{index:04d}" for index in range(len(ids))]: raise _error("Plan operation ids are not canonical", "operation_order_mismatch")
        destructive=[item["id"] for item in instance["operations"] if item["kind"] in {"replace","move","delete"}]
        if instance["destructive_operation_ids"]!=destructive: raise _error("Destructive operation projection differs", "destructive_operations_mismatch")
        for item in instance["operations"]:
            content,expected,output=item["content_b64"],item["expected_sha256"],item["output_sha256"]
            valid={"mkdir":content is None and expected is None and output is None,"create":content is not None and expected is None and output is not None,"replace":content is not None and expected is not None and output is not None,"delete":content is None and expected is not None and output is None,"move":False}[item["kind"]]
            if not valid: raise _error("Plan operation fields disagree with kind", "invalid_operation", operation=item["id"])
            if content is not None and hashlib.sha256(base64.b64decode(content,validate=True)).hexdigest()!=output: raise _error("Operation output digest differs", "operation_digest_mismatch", operation=item["id"])
        expected_request=hashlib.sha256(canonical_json_bytes({"route":instance["route"],"operations":instance["operations"]})).hexdigest()
        if instance["request_digest"]!=expected_request: raise _error("Plan request digest differs", "plan_request_digest_mismatch")
        if {item["phase"] for item in instance["path_preflight"]}!={"live","stage","backup","artifact","journal","index"}: raise _error("Path preflight does not cover every phase", "path_preflight_incomplete")
        if any(item["observed"]>item["limit"] for item in instance["path_preflight"]): raise _error("Plan contains a failed path preflight", "path_preflight_failed")
    elif name == "validation-report":
        errors=sum(item["severity"]=="error" for item in instance["issues"]);warnings=sum(item["severity"]=="warning" for item in instance["issues"])
        if instance["counts"]!={"errors":errors,"warnings":warnings} or instance["outcome"]!=("fail" if errors else "pass"): raise _error("Validation counts/outcome disagree", "validation_report_mismatch")
    elif name == "apply-journal":
        if instance["parents"][0]["artifact_id"]!=instance["plan_id"]: raise _error("Journal plan binding differs", "journal_plan_mismatch")
        events=instance["events"]
        if [item["sequence"] for item in events]!=list(range(1,len(events)+1)) or events[-1]["event"]!=instance["state"]: raise _error("Journal transition sequence is invalid", "journal_state_mismatch")
        allowed=["claimed","staged","parked","published"]
        observed=[item["event"] for item in events]
        if observed[:min(len(observed),4)]!=allowed[:min(len(observed),4)] and instance["state"] not in {"failed","recovery_ambiguous"}: raise _error("Journal state progression is invalid", "journal_state_mismatch")
    elif name == "verification-receipt":
        parent_refs=set(_refs(instance["parents"])); required={
            (instance["validation_report_ref"]["schema_id"], instance["validation_report_ref"]["artifact_id"], instance["validation_report_ref"]["digest"]),
            (instance["journal_ref"]["schema_id"], instance["journal_ref"]["artifact_id"], instance["journal_ref"]["digest"]),
        }
        if not required<=parent_refs or not any(item[1]==instance["plan_id"] for item in parent_refs): raise _error("Receipt parent bindings are incomplete", "receipt_lineage_mismatch")
        lineage=_refs(instance["lineage"])
        if lineage!=sorted(set(lineage)): raise _error("Receipt lineage must be unique and ordered", "receipt_lineage_mismatch")


def artifact_ref(value: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(value[key]) for key in ("schema_id", "artifact_id", "digest")}


def make_artifact(
    schema_name_or_id: str,
    payload: Mapping[str, Any],
    *,
    parents: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    name = _name(schema_name_or_id)
    if name in _NON_ARTIFACTS:
        raise _error("Schema is not an artifact", "not_artifact_schema", schema=name)
    if {"schema_id", "schema_version", "artifact_id", "digest", "parents"} & set(payload):
        raise _error("Artifact payload contains header fields", "reserved_artifact_field")
    refs = sorted(
        {tuple(artifact_ref(item).values()) for item in parents},
        key=lambda item: (item[0], item[1], item[2]),
    )
    value: dict[str, Any] = {
        "schema_id": SCHEMA_IDS[name],
        "schema_version": SCHEMA_IDS[name].rsplit(":", 1)[1],
        "artifact_id": "",
        "digest": "",
        "parents": [dict(zip(("schema_id", "artifact_id", "digest"), item, strict=True)) for item in refs],
        **dict(payload),
    }
    digest = artifact_digest(value)
    value["digest"] = digest
    value["artifact_id"] = f"{name}@{digest}"
    validate_contract(value, name)
    return value


def normalize_issue(issue: Mapping[str, Any]) -> dict[str, Any]:
    details = issue.get("details", {})
    if isinstance(details, Mapping):
        normalized = [{"name": str(key), "value": value if value is None or isinstance(value, (str, int, float, bool)) else canonical_json_bytes(value).decode("utf-8")} for key, value in sorted(details.items())]
    else:
        normalized = list(details)
    return {
        "rule_id": str(issue.get("rule_id", "cortex")),
        "code": str(issue.get("code", "unknown")),
        "severity": str(issue.get("severity", "error")),
        "message": str(issue.get("message", "")),
        "path": issue.get("path"),
        "concept_id": issue.get("concept_id"),
        "operation_id": issue.get("operation_id"),
        "hint": issue.get("hint"),
        "details": normalized,
    }


def make_envelope(
    command: str,
    status: Status | str,
    data: Any = None,
    data_schema_id: str | None = None,
    *,
    issues: Iterable[Mapping[str, Any]] = (),
    artifacts: Iterable[Mapping[str, Any]] = (),
    run_id: str | None = None,
    emitted_at: str | None = None,
) -> dict[str, Any]:
    status_value = status.value if isinstance(status, Status) else str(status)
    status_object = Status(status_value)
    if status_object is not Status.OK:
        data, data_schema_id = None, None
    elif data_schema_id is not None:
        validate_contract(data, data_schema_id)
    envelope = {
        "schema_id": SCHEMA_IDS["result-envelope"], "schema_version": "1.0.0",
        "command": command, "status": status_value, "exit_code": int(status_object.exit_code),
        "run_id": run_id or str(uuid.uuid4()),
        "emitted_at": emitted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_schema_id": data_schema_id, "data": data,
        "issues": [normalize_issue(item) for item in issues],
        "artifacts": [artifact_ref(item) for item in artifacts],
    }
    validate_contract(envelope, "result-envelope")
    return envelope


def validate_registry() -> tuple[str, ...]:
    names = tuple(sorted(path.name.removesuffix(".schema.json") for path in SCHEMA_DIRECTORY.glob("*.schema.json")))
    if set(names) != set(SCHEMA_IDS):
        raise _error("Schema registry is not closed", "schema_registry_mismatch", expected=sorted(SCHEMA_IDS), observed=list(names))
    for name in names:
        load_schema(name)
    return names


__all__ = ["SCHEMA_DIRECTORY", "artifact_ref", "load_schema", "make_artifact", "make_envelope", "normalize_issue", "validate_contract", "validate_registry"]
