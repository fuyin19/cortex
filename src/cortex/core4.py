"""Deterministic Cortex 4 bundle, ingest, and transaction primitives."""

from __future__ import annotations

import base64
import contextlib
import difflib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
import posixpath
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from ruamel.yaml import YAML

from .canonical import canonical_json_bytes, sha256_digest, tree_manifest
from .constants import INDEX_BYTES, STATE_OWNER, TAG_SCHEMA_PATH
from .contracts import artifact_ref, make_artifact, validate_contract
from .errors import CortexError, Status
from .naming import normalize_script_title
from .native import (
    canonical_handle_path, copy_tree, durability_supported, exists as native_exists,
    flush_directory, is_dir as native_is_dir, is_file as native_is_file,
    is_reparse, native_path as _native_path, reject_reparse_ancestry,
    remove_tree, volume_identity,
)
from .paths import normalize_relative_path

_DIGEST_PLACEHOLDER = "0" * 64
_TEMP_SUFFIX_PLACEHOLDER = "." + "0" * 10 + "." + "0" * 32 + ".tmp"
_MAX_STDIN = 16 * 1024 * 1024
_DIMENSION = re.compile(r"^[a-z][a-z0-9_]*$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def error(message: str, code: str, status: Status = Status.VALIDATION_BLOCKED, **details: object) -> CortexError:
    return CortexError(message, status=status, code=code, details=dict(details))


def strict_json(raw: bytes, *, subject: str) -> Any:
    if len(raw) > _MAX_STDIN:
        raise error(f"{subject} exceeds the 16 MiB input limit", "input_too_large", observed=len(raw), limit=_MAX_STDIN)
    if raw.startswith(b"\xef\xbb\xbf"):
        raise error(f"{subject} must not contain a UTF-8 BOM", "invalid_text_encoding")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            normalized = unicodedata.normalize("NFC", key)
            if normalized in result:
                raise error(f"{subject} contains a duplicate object key", "duplicate_json_key", key=normalized)
            result[normalized] = value
        return result

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except CortexError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise error(f"{subject} is not strict UTF-8 JSON", "invalid_json") from exc


def read_json_operand(value: str, stream: BinaryIO | None = None, *, subject: str) -> Any:
    if value == "-":
        source = stream or sys.stdin.buffer
        if hasattr(source, "isatty") and source.isatty():
            raise error("Refusing to wait for JSON on an interactive terminal", "stdin_is_tty", Status.USAGE_ERROR)
        raw = source.read(_MAX_STDIN + 1)
        if not raw:
            raise error("JSON stdin reached EOF before a value", "stdin_empty", Status.USAGE_ERROR)
    else:
        path = Path(value)
        if not native_is_file(path) or is_reparse(path):
            raise error(f"{subject} must be a regular file", "input_unavailable", path=str(path))
        with open(_native_path(path), "rb") as handle:
            raw = handle.read(_MAX_STDIN + 1)
    return strict_json(raw, subject=subject)


def canonical_path_key(path: Path) -> str:
    normalized = canonical_handle_path(path)
    return ("windows:" if os.name == "nt" else "posix:") + normalized


def bundle_identity(path: Path) -> tuple[str, str]:
    key = canonical_path_key(path)
    digest = hashlib.sha256(b"cortex.bundle-path.v1\0" + key.encode("utf-8")).hexdigest()
    return key, digest


@dataclass(frozen=True)
class StatePaths:
    root: Path
    identity: str
    key: str

    @property
    def artifacts(self) -> Path: return self.root / "artifacts"
    @property
    def journals(self) -> Path: return self.root / "journals"
    @property
    def staging(self) -> Path: return self.root / "staging"
    @property
    def backups(self) -> Path: return self.root / "backups"
    @property
    def indexes(self) -> Path: return self.root / "indexes"
    @property
    def lock(self) -> Path: return self.root / "locks" / "bundle.lock"


def state_paths(workspace: Path) -> StatePaths:
    key, identity = bundle_identity(workspace)
    return StatePaths(workspace.parent / ".cortex" / f"b-{identity}", identity, key)


def _native_exists(path: Path) -> bool:
    return native_exists(path)


def ensure_state(workspace: Path) -> StatePaths:
    state = state_paths(workspace)
    parent = state.root.parent
    try:
        reject_reparse_ancestry(workspace)
        reject_reparse_ancestry(parent)
    except OSError as exc:
        raise error("Bundle or state ancestry contains a reparse point", "reparse_traversal", Status.POLICY_BLOCKED, path=str(workspace)) from exc
    if _native_exists(state.root):
        if is_reparse(state.root):
            raise error("Cortex state root cannot be a reparse point", "state_unowned", Status.POLICY_BLOCKED)
        try:
            owner = strict_json(open(_native_path(state.root / "owner.json"), "rb").read(), subject="state owner")
            identity = strict_json(open(_native_path(state.root / "identity.json"), "rb").read(), subject="state identity")
        except OSError as exc:
            raise error("Cortex state ownership cannot be verified", "state_unowned", Status.POLICY_BLOCKED) from exc
        expected_identity = {"schema_version": "1.0.0", "algorithm": "cortex.bundle-path.v1", "canonical_path_key": state.key, "bundle_identity": state.identity}
        if owner != STATE_OWNER or identity != expected_identity:
            raise error("Cortex state ownership or identity differs", "state_identity_collision", Status.POLICY_BLOCKED)
        return state
    os.makedirs(_native_path(parent), exist_ok=True)
    os.makedirs(_native_path(state.root), exist_ok=False)
    for path in (state.artifacts, state.journals, state.staging, state.backups, state.indexes, state.lock.parent):
        os.makedirs(_native_path(path), exist_ok=False)
    atomic_bytes(state.root / "owner.json", canonical_json_bytes(STATE_OWNER) + b"\n")
    identity_value = {"schema_version": "1.0.0", "algorithm": "cortex.bundle-path.v1", "canonical_path_key": state.key, "bundle_identity": state.identity}
    atomic_bytes(state.root / "identity.json", canonical_json_bytes(identity_value) + b"\n")
    return state


def fsync_dir(path: Path) -> None:
    flush_directory(path)


def atomic_bytes(path: Path, data: bytes) -> None:
    os.makedirs(_native_path(path.parent), exist_ok=True)
    temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(_native_path(temp), "xb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(_native_path(temp), _native_path(path))
        fsync_dir(path.parent)
    finally:
        if _native_exists(temp):
            os.unlink(_native_path(temp))


@contextlib.contextmanager
def bundle_lock(state: StatePaths) -> Iterator[None]:
    os.makedirs(_native_path(state.lock.parent), exist_ok=True)
    with open(_native_path(state.lock), "a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0); handle.write(b"0"); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise error("The bundle is locked by another operation", "bundle_locked", Status.CONFLICT) from exc
        try: yield
        finally:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def persist_artifact(state: StatePaths, artifact: Mapping[str, Any]) -> None:
    path = state.artifacts / f"{artifact['artifact_id']}.json"
    payload = canonical_json_bytes(artifact) + b"\n"
    if _native_exists(path):
        if open(_native_path(path), "rb").read() != payload:
            raise error("Artifact id collides with different bytes", "artifact_collision", Status.CONFLICT)
        return
    atomic_bytes(path, payload)


def load_artifact(state: StatePaths, artifact_id: str, expected: str | None = None) -> dict[str, Any]:
    if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
        raise error("Artifact operands accept content-addressed ids only", "artifact_id_required", Status.USAGE_ERROR)
    path = state.artifacts / f"{artifact_id}.json"
    try: value = strict_json(open(_native_path(path), "rb").read(), subject="artifact")
    except OSError as exc: raise error("Artifact is unavailable", "artifact_unavailable", Status.CONFLICT, artifact_id=artifact_id) from exc
    if not isinstance(value, dict): raise error("Artifact is malformed", "invalid_artifact", Status.CONFLICT)
    schema_name = str(value.get("schema_id", "")).split(":")[-2] if ":" in str(value.get("schema_id", "")) else ""
    if expected and not str(value.get("schema_id", "")).startswith(f"urn:cortex:schema:{expected}:"):
        if expected == "mutation-plan" and str(value.get("schema_id", "")).endswith(":1.0.0"):
            raise error("Legacy plans cannot be applied", "legacy_plan_unsupported", Status.UNSUPPORTED)
        raise error("Artifact has the wrong type", "artifact_type_mismatch", Status.CONFLICT)
    validate_contract(value, expected or schema_name)
    return value


def tree_digest(root: Path) -> str:
    return tree_manifest(root, exclude=(".cortex",))["tree_digest"]


def is_empty_directory(path: Path) -> bool:
    return native_is_dir(path) and not is_reparse(path) and next(os.scandir(_native_path(path)), None) is None


def _tag_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def validate_tag_schema(value: Any) -> dict[str, Any]:
    validate_contract(value, "tag-schema")
    if not isinstance(value, dict): raise error("Tag schema must be an object", "invalid_tag_schema", Status.POLICY_BLOCKED)
    types = value["types"]
    if types["reference"]["status"] not in {"active", "unconfigured"}:
        raise error("Reference policy status is invalid", "invalid_tag_schema", Status.POLICY_BLOCKED)
    for kind in ("concept", "entity"):
        if types[kind] != {"status": "unconfigured", "identifier_dimension": None, "dimensions": {}}:
            raise error("Concept and entity must remain explicitly unconfigured", "invalid_tag_schema", Status.POLICY_BLOCKED, type=kind)
    seen_tags: set[str] = set()
    for kind, policy in types.items():
        if policy["status"] == "unconfigured":
            if policy["identifier_dimension"] is not None or policy["dimensions"]:
                raise error("Unconfigured types cannot declare dimensions", "invalid_tag_schema", Status.POLICY_BLOCKED, type=kind)
            continue
        identifier = policy["identifier_dimension"]
        if identifier not in policy["dimensions"]:
            raise error("Identifier dimension is missing", "invalid_tag_schema", Status.POLICY_BLOCKED)
        for name, dimension in policy["dimensions"].items():
            if not _DIMENSION.fullmatch(name): raise error("Dimension name is invalid", "invalid_tag_schema", Status.POLICY_BLOCKED, dimension=name)
            minimum, maximum = dimension["cardinality"]["min"], dimension["cardinality"]["max"]
            values = dimension["values"]
            if minimum > maximum or maximum > len(values): raise error("Dimension cardinality is invalid", "invalid_tag_schema", Status.POLICY_BLOCKED, dimension=name)
            expected_assignment = "user_or_llm" if name == identifier else "derived"
            if dimension["assignment"] != expected_assignment or (name == identifier and (minimum, maximum) != (1, 1)):
                raise error("Dimension assignment contract is invalid", "invalid_tag_schema", Status.POLICY_BLOCKED, dimension=name)
            identities: dict[str, str] = {}
            for item in values:
                tag = item["tag"]
                if not _TAG.fullmatch(tag) or tag in seen_tags: raise error("Tags must be globally unique", "duplicate_tag", Status.POLICY_BLOCKED, tag=tag)
                seen_tags.add(tag)
                for candidate in (tag, item["label"], *item["aliases"]):
                    key = _tag_key(candidate)
                    if key in identities and identities[key] != tag: raise error("Tag identities collide", "tag_normalization_collision", Status.POLICY_BLOCKED, value=candidate)
                    identities[key] = tag
        id_dimension = policy["dimensions"][identifier]
        derived_dimensions = {name: dimension for name, dimension in policy["dimensions"].items() if name != identifier}
        derived_tags = {item["tag"] for dimension in derived_dimensions.values() for item in dimension["values"]}
        for item in id_dimension["values"]:
            declared = item["derived_tags"]
            if any(tag not in derived_tags for tag in declared): raise error("Identifier declares an unknown derived tag", "invalid_derived_tag", Status.POLICY_BLOCKED, tag=item["tag"])
            for name, dimension in derived_dimensions.items():
                count = sum(tag in {candidate["tag"] for candidate in dimension["values"]} for tag in declared)
                if not dimension["cardinality"]["min"] <= count <= dimension["cardinality"]["max"]:
                    raise error("Identifier derived tags violate cardinality", "invalid_derived_tag", Status.POLICY_BLOCKED, tag=item["tag"], dimension=name)
    return value


def tag_schema_bytes(value: Any) -> bytes:
    return canonical_json_bytes(validate_tag_schema(value)) + b"\n"


def load_tag_schema(workspace: Path) -> dict[str, Any]:
    path = workspace / "profiles" / "tag-schema.json"
    try: value = strict_json(open(_native_path(path), "rb").read(), subject="tag schema")
    except OSError as exc: raise error("Tag schema is missing", "tag_schema_required", Status.POLICY_BLOCKED) from exc
    return validate_tag_schema(value)


def tag_schema_digest(workspace: Path) -> str:
    return sha256_digest(tag_schema_bytes(load_tag_schema(workspace)))


def _type_policy(schema: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    policy = schema["types"]["reference"]
    if policy["status"] != "active": raise error("Reference ingest is unconfigured", "target_type_unconfigured", Status.POLICY_BLOCKED)
    identifier = policy["identifier_dimension"]
    return str(identifier), policy["dimensions"][identifier]


def resolve_tag(schema: Mapping[str, Any], value: str) -> Mapping[str, Any]:
    name, dimension = _type_policy(schema)
    wanted = _tag_key(value)
    matches = [item for item in dimension["values"] if wanted in {_tag_key(item["tag"]), _tag_key(item["label"]), *(_tag_key(alias) for alias in item["aliases"])}]
    if len(matches) != 1: raise error("Tag does not resolve to exactly one registered identifier", "unknown_tag_assignment", Status.POLICY_BLOCKED, dimension=name, value=value)
    return matches[0]


@dataclass(frozen=True)
class Draft:
    path: Path
    raw: bytes
    body: bytes
    source_id: str
    source_digest: str
    body_digest: str
    title: str
    timestamp: str


def _split_frontmatter(raw: bytes) -> tuple[dict[str, Any], bytes]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise error("Markdown must not contain a UTF-8 BOM", "invalid_text_encoding")
    if raw.startswith(b"---\n"):
        opening, delimiter = 4, b"\n---\n"
    elif raw.startswith(b"---\r\n"):
        opening, delimiter = 5, b"\r\n---\r\n"
    else:
        raise error("Markdown requires LF-delimited YAML frontmatter", "invalid_frontmatter")
    end = raw.find(delimiter, opening)
    if end < 0: raise error("Markdown frontmatter is not closed", "invalid_frontmatter")
    try: frontmatter_text = raw[opening:end].decode("utf-8", errors="strict")
    except UnicodeError as exc: raise error("Markdown must be strict UTF-8", "invalid_text_encoding") from exc
    yaml = YAML(typ="safe", pure=True); yaml.allow_duplicate_keys = False
    try: value = yaml.load(frontmatter_text)
    except Exception as exc: raise error("Markdown frontmatter is invalid", "invalid_frontmatter") from exc
    if not isinstance(value, dict): raise error("Markdown frontmatter must be a mapping", "invalid_frontmatter")
    return dict(value), raw[end + len(delimiter):]


def _valid_timestamp_text(value: object) -> bool:
    if not isinstance(value,str):return False
    try:
        if _DATE.fullmatch(value):date.fromisoformat(value);return True
        if not _DATETIME.fullmatch(value):return False
        parsed=datetime.fromisoformat(value[:-1]+"+00:00" if value.endswith("Z") else value)
        return parsed.utcoffset() is not None
    except ValueError:return False


def read_draft(path_value: str | Path) -> Draft:
    path = Path(path_value).absolute()
    if not native_is_file(path) or is_reparse(path) or path.suffix.casefold() != ".md": raise error("Source must be a regular Markdown file", "invalid_source", path=str(path))
    with open(_native_path(path), "rb") as handle: raw = handle.read()
    metadata, body = _split_frontmatter(raw)
    required = {"type", "title", "description", "tags", "timestamp"}
    if set(metadata) != required: raise error("Draft frontmatter must contain exactly five fields", "invalid_draft_frontmatter", missing=sorted(required-set(metadata)), unknown=sorted(set(metadata)-required))
    if metadata["type"] != "" or metadata["description"] != "" or metadata["tags"] != []: raise error("Draft type, description and tags must be empty", "prefilled_draft_metadata")
    title = metadata["title"]
    if not isinstance(title, str) or not title.strip(): raise error("Draft title is required", "missing_draft_title")
    timestamp = metadata["timestamp"]
    if not _valid_timestamp_text(timestamp): raise error("Timestamp must be a real ISO date or timezone-aware RFC3339 string", "invalid_draft_timestamp")
    digest = hashlib.sha256(raw).hexdigest()
    return Draft(path, raw, body, "urn:sha256:"+digest, "sha256:"+digest, "sha256:"+hashlib.sha256(body).hexdigest(), unicodedata.normalize("NFC", title).strip(), timestamp)


def context_for(workspace: Path, sources: Sequence[str], tags: Sequence[str]) -> tuple[dict[str, Any], list[Draft]]:
    schema = load_tag_schema(workspace); identifier, dimension = _type_policy(schema)
    drafts = [read_draft(value) for value in sources]
    if not drafts: raise error("At least one --source is required", "source_required", Status.USAGE_ERROR)
    if len({item.source_id for item in drafts}) != len(drafts): raise error("A source may appear only once", "duplicate_source", Status.USAGE_ERROR)
    requested = [resolve_tag(schema, tag) for tag in tags]
    if len({item["tag"] for item in requested}) > 1: raise error("A batch accepts at most one common identifier tag", "ambiguous_tag_assignment", Status.POLICY_BLOCKED)
    source_values = []
    for draft in drafts:
        inferred = [] if requested else [item for item in dimension["values"] if _tag_key(draft.path.stem) in {_tag_key(item["tag"]), _tag_key(item["label"]), *(_tag_key(alias) for alias in item["aliases"])} or _tag_key(draft.title) in {_tag_key(item["tag"]), _tag_key(item["label"]), *(_tag_key(alias) for alias in item["aliases"])}]
        selected = requested or inferred
        resolved = {identifier: selected[0]["tag"]} if len(selected) == 1 else {}
        unresolved = [] if resolved else [{
            "dimension": identifier,
            "candidates": [
                {"tag": item["tag"], "label": item["label"], "aliases": item["aliases"]}
                for item in dimension["values"]
            ],
        }]
        source_values.append({"source_id": draft.source_id,"source_path":str(draft.path),"source_digest":draft.source_digest,"body_digest":draft.body_digest,"title":draft.title,"timestamp":draft.timestamp,"resolved_assignments":resolved,"unresolved_dimensions":unresolved})
    state = state_paths(workspace)
    return make_artifact("ingest-context", {"bundle_identity":state.identity,"base_tree_digest":tree_digest(workspace),"tag_schema_digest":sha256_digest(tag_schema_bytes(schema)),"sources":source_values}), drafts


def proposal_assignments(context: Mapping[str, Any], proposal_input: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if proposal_input is not None:
        validate_contract(proposal_input, "ingest-proposal-input")
        if proposal_input["context_id"] != context["artifact_id"]: raise error("Proposal belongs to another context", "proposal_context_mismatch", Status.CONFLICT)
        incoming = {item["source_id"]: item["assignments"] for item in proposal_input["items"]}
    else: incoming = {}
    result = []
    for source in context["sources"]:
        unresolved = {item["dimension"]: item for item in source["unresolved_dimensions"]}
        supplied = incoming.get(source["source_id"], {})
        if set(supplied) != set(unresolved):
            if unresolved: raise error("Proposal must fill every unresolved dimension", "proposal_dimension_mismatch")
            if supplied: raise error("Proposal cannot replace resolved assignments", "proposal_dimension_mismatch")
        for name, value in supplied.items():
            allowed = {item["tag"] for item in unresolved[name]["candidates"]}
            if value not in allowed: raise error("Proposal assignment is not a supplied candidate", "unknown_tag_assignment", Status.POLICY_BLOCKED, dimension=name, value=value)
        result.append({"source_id":source["source_id"],"assignments":{**source["resolved_assignments"],**supplied}})
    if proposal_input is not None and set(incoming) != {item["source_id"] for item in context["sources"]}: raise error("Proposal must cover every source exactly once", "proposal_coverage_mismatch")
    return result


def publication_path(tag: str, title: str, timestamp: str) -> str:
    compact = timestamp[:10].replace("-", "")
    filename = f"{tag}-{normalize_script_title(title)}-{compact}.md"
    normalize_relative_path(f"references/{filename}")
    return f"references/{filename}"


def render_publication(draft: Draft, tag_value: Mapping[str, Any], body: bytes) -> bytes:
    path = publication_path(tag_value["tag"], draft.title, draft.timestamp)
    title = PurePosixPath(path).stem
    tags = [tag_value["tag"], *tag_value["derived_tags"]]
    return render_canonical_reference(title, tags, draft.timestamp, body)


def render_canonical_reference(title: str, tags: Sequence[str], timestamp: str, body: bytes) -> bytes:
    lines = ["---", "type: reference", f"title: {json.dumps(title, ensure_ascii=False)}", 'description: ""', "tags:"]
    lines.extend(f"  - {json.dumps(item, ensure_ascii=False)}" for item in tags)
    lines.extend([f"timestamp: {json.dumps(timestamp, ensure_ascii=False)}", "---", ""])
    return "\n".join(lines).encode("utf-8") + body


@dataclass(frozen=True)
class LinkToken:
    start: int; end: int; before: str; destination: str; label: str; kind: str
    destination_start: int | None = None
    destination_end: int | None = None


_MARKDOWN_LINK = re.compile(r"(?P<image>!)?\[(?P<label>[^\]\n]*)\]\((?P<dest>[^)\n]+)\)")
_WIKI_LINK = re.compile(r"(?P<image>!)?\[\[(?P<dest>[^\]|\n]+)(?:\|(?P<label>[^\]\n]*))?\]\]")
_REFERENCE_USE = re.compile(r"(?P<image>!)?\[(?P<label>[^\]\n]+)\]\[(?P<ref>[^\]\n]+)\]")
_HTML_ATTRIBUTE = r"[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^ \t\n\r\"'=<>`]+|'[^'\n\r]*'|\"[^\"\n\r]*\"))?"
_TYPE7_HTML_BLOCK = re.compile(
    rf" {{0,3}}(?:</[A-Za-z][A-Za-z0-9-]*[ \t]*>|<[A-Za-z][A-Za-z0-9-]*(?:[ \t]+{_HTML_ATTRIBUTE})*[ \t]*/?>)[ \t]*$"
)


@dataclass(frozen=True)
class ReferenceDefinition:
    start: int; end: int; label: str; destination: str; destination_start: int; destination_end: int


@dataclass(frozen=True)
class MarkdownAnalysis:
    opaque_spans: tuple[tuple[int,int],...]
    definitions: tuple[ReferenceDefinition,...]
    tokens: tuple[LinkToken,...]


def _reference_label_key(value: str) -> str:
    normalized=unicodedata.normalize("NFC",value).strip().casefold()
    return re.sub(r"\s+"," ",normalized)


def _parse_reference_destination(line: str, cursor: int) -> tuple[int, str] | None:
    if cursor>=len(line):return None
    if line[cursor]=="<":
        start=cursor+1;cursor=start
        while cursor<len(line):
            char=line[cursor]
            if char=="\\" and cursor+1<len(line):cursor+=2;continue
            if char==">":return cursor+1,line[start:cursor]
            if char in "<>\r\n":return None
            cursor+=1
        return None
    start=cursor;depth=0
    while cursor<len(line):
        char=line[cursor]
        if char=="\\" and cursor+1<len(line):cursor+=2;continue
        if char.isspace():break
        if char=="(":depth+=1
        elif char==")":
            if depth==0:return None
            depth-=1
        elif ord(char)<0x20:return None
        cursor+=1
    if cursor==start or depth:return None
    return cursor,line[start:cursor]


def _parse_reference_title(line: str, cursor: int) -> int | None:
    if cursor>=len(line) or line[cursor] not in "\"'(":return None
    opener=line[cursor];closer=")" if opener=="(" else opener;cursor+=1
    while cursor<len(line):
        char=line[cursor]
        if char=="\\" and cursor+1<len(line):cursor+=2;continue
        if char==closer:return cursor+1
        if char in "\r\n" or (opener=="(" and char=="("):return None
        cursor+=1
    return None


def _reference_definitions(text: str, protected: Sequence[tuple[int,int]]) -> list[ReferenceDefinition]:
    definitions:list[ReferenceDefinition]=[]
    line_matches=list(re.finditer(r".*(?:\r\n|\n|\r|$)",text))
    for line_index,line_match in enumerate(line_matches):
        raw=line_match.group(0);line=raw.rstrip("\r\n")
        if not line or _span_contains(protected,line_match.start()):continue
        opening=re.match(r" {0,3}\\*\[",line)
        if not opening or _punctuation_is_escaped(line,opening.end()-1):continue
        label_start=opening.end();cursor=label_start;label_end=-1
        while cursor<len(line):
            if line[cursor]=="\\" and cursor+1<len(line):cursor+=2;continue
            if line[cursor] in "[]":
                if line[cursor]=="]":label_end=cursor
                break
            cursor+=1
        if label_end<0 or label_end==label_start or label_end+1>=len(line) or line[label_end+1] != ":":continue
        label=line[label_start:label_end]
        if len(label)>999:continue
        cursor=label_end+2
        while cursor<len(line) and line[cursor] in " \t":cursor+=1
        destination_start=cursor
        parsed=_parse_reference_destination(line,cursor)
        if parsed is None:continue
        cursor,destination=parsed
        destination_end=cursor
        if line[destination_start:destination_start+1]=="<":
            destination_start+=1;destination_end-=1
        whitespace=cursor
        while cursor<len(line) and line[cursor] in " \t":cursor+=1
        definition_end=line_match.start()+len(line);inline_title=cursor<len(line)
        if inline_title:
            if cursor==whitespace:continue
            title_end=_parse_reference_title(line,cursor)
            if title_end is None:continue
            cursor=title_end
            while cursor<len(line) and line[cursor] in " \t":cursor+=1
        if cursor!=len(line):continue
        if not inline_title and line_index+1<len(line_matches):
            next_match=line_matches[line_index+1];next_line=next_match.group(0).rstrip("\r\n");next_open=re.match(r" {0,3}(?=[\"'(])",next_line)
            if next_open:
                title_end=_parse_reference_title(next_line,next_open.end())
                if title_end is not None and not next_line[title_end:].strip():definition_end=next_match.start()+len(next_line)
        definitions.append(ReferenceDefinition(line_match.start(),definition_end,label,destination,line_match.start()+destination_start,line_match.start()+destination_end))
    return definitions


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], end)
        else: merged.append([start, end])
    return [(start, end) for start, end in merged]


def _span_contains(spans: Sequence[tuple[int,int]], position: int) -> bool:
    return any(start <= position < end for start,end in spans)


_CONTAINER_PREFIX = re.compile(r" {0,3}(?:>[ \t]?|(?:[*+-]|\d+[.)])[ \t]+)")
_EXPLICIT_LINK_LIKE = re.compile(r"(?:!?\[\[|!?\[[^\]\r\n]*\]\s*(?:\(|\[)|\[[^\]\r\n]+\]:)")
_BRACKET_GROUP = re.compile(r"!?\[([^\]\r\n]+)\]")


def _punctuation_is_escaped(text: str, position: int) -> bool:
    backslashes=0;cursor=position-1
    while cursor>=0 and text[cursor]=="\\":backslashes+=1;cursor-=1
    return bool(backslashes%2)


def _candidate_opening(text: str, position: int) -> int | None:
    bracket=position+1 if text.startswith("![",position) else position
    if _punctuation_is_escaped(text,bracket):return None
    if bracket!=position and _punctuation_is_escaped(text,position):return bracket
    return position


def _link_kind(destination: str, image: bool) -> str:
    suffix=PurePosixPath(unquote(destination.split("#",1)[0].split("?",1)[0])).suffix.casefold()
    if image or suffix in {".png",".jpg",".jpeg",".gif",".webp",".svg"}:return "image"
    if suffix in {".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".zip",".csv"}:return "attachment"
    return "prose"


def _external_destination(destination: str) -> bool:
    return bool(urlsplit(destination).scheme or destination.startswith(("#","//")) or re.fullmatch(r"[^/@\s]+@[^/@\s]+",destination))


def _balanced_label(text: str, opening: int) -> tuple[int,str] | None:
    depth=1;cursor=opening+1
    while cursor<len(text):
        if text[cursor]=="\\":cursor+=2;continue
        if text[cursor] in "\r\n":return None
        if text[cursor]=="[":depth+=1
        elif text[cursor]=="]":
            depth-=1
            if depth==0:return cursor,text[opening+1:cursor]
        cursor+=1
    return None


def _inline_destination(text: str, opening: int) -> tuple[int,int,int,str] | None:
    cursor=opening+1
    if cursor>=len(text):return None
    if text[cursor]=="<":
        start=cursor+1;cursor=start
        while cursor<len(text) and text[cursor] not in ">\r\n":
            cursor+=2 if text[cursor]=="\\" and cursor+1<len(text) else 1
        if cursor>=len(text) or text[cursor]!=">":return None
        end=cursor;cursor+=1
    else:
        start=cursor;depth=0
        while cursor<len(text):
            char=text[cursor]
            if char=="\\" and cursor+1<len(text):cursor+=2;continue
            if char in "\r\n":return None
            if char=="(":depth+=1
            elif char==")":
                if depth==0:return cursor+1,start,cursor,text[start:cursor]
                depth-=1
            elif char.isspace() and depth==0:break
            cursor+=1
        end=cursor
    while cursor<len(text) and text[cursor] in " \t":cursor+=1
    if cursor<len(text) and text[cursor]==")":return cursor+1,start,end,text[start:end]
    if cursor>=len(text) or text[cursor] not in "\"'(":return None
    opener=text[cursor];closer=")" if opener=="(" else opener;cursor+=1;title_depth=1
    while cursor<len(text):
        char=text[cursor]
        if char=="\\" and cursor+1<len(text):cursor+=2;continue
        if char in "\r\n":return None
        if opener=="(" and char=="(":title_depth+=1
        elif char==closer:
            title_depth-=1
            if title_depth==0:cursor+=1;break
        cursor+=1
    else:return None
    while cursor<len(text) and text[cursor] in " \t":cursor+=1
    if cursor>=len(text) or text[cursor]!=")":return None
    return cursor+1,start,end,text[start:end]


def _analyze_markdown(body: bytes) -> MarkdownAnalysis:
    """Own the complete bounded Markdown decision for every Cortex consumer."""

    text=body.decode("utf-8",errors="strict");lines=list(re.finditer(r".*(?:\r\n|\n|\r|$)",text));opaque:list[tuple[int,int]]=[]
    html_blocks={"address","article","aside","base","basefont","blockquote","body","caption","center","col","colgroup","dd","details","dialog","dir","div","dl","dt","fieldset","figcaption","figure","footer","form","frame","frameset","h1","h2","h3","h4","h5","h6","head","header","hr","html","iframe","legend","li","link","main","menu","menuitem","nav","noframes","ol","optgroup","option","p","param","search","section","summary","table","tbody","td","tfoot","th","thead","title","tr","track","ul"}

    def fail(position:int,reason:str,syntax_kind:str)->None:
        line=text.count("\n",0,position)+1;line_start=text.rfind("\n",0,position)+1
        raise error("Markdown link syntax occurs in an unsupported or ambiguous context","unsupported_markdown_link_context",reason=reason,syntax_kind=syntax_kind,line=line,column=position-line_start+1,start_byte=len(text[:position].encode("utf-8")))

    def explicit_candidates(line:str)->list[tuple[int,str]]:
        found=[]
        for match in _EXPLICIT_LINK_LIKE.finditer(line):
            opening=_candidate_opening(line,match.start())
            if opening is not None:found.append((opening,"explicit"))
        return sorted(set(found))

    def shortcut_candidates(line:str,definition_labels:set[str])->list[tuple[int,str]]:
        found=[]
        for match in _BRACKET_GROUP.finditer(line):
            opening=_candidate_opening(line,match.start())
            if opening is not None and _reference_label_key(match.group(1)) in definition_labels:found.append((opening,"shortcut_reference"))
        return sorted(set(found))

    context_failures:list[tuple[int,int,str,str]]=[]
    shortcut_contexts:list[tuple[int,str,str,tuple[tuple[int,int],...]]]=[]

    def record_context(line_start:int,line:str,reason:str,ignored:Sequence[tuple[int,int]]=())->None:
        for local,kind in explicit_candidates(line):
            position=line_start+local
            if _span_contains(ignored,position):continue
            syntax_kind="reference_definition" if reason=="container" and re.match(r"!?\[[^]]+\]:",line[local:]) else kind
            context_failures.append((position,0,reason,syntax_kind))
        shortcut_contexts.append((line_start,line,reason,tuple(ignored)))

    container_active=False;blank_after_container=False;paragraph_active=False;ambiguous_html=False;index=0
    while index<len(lines):
        line_match=lines[index];line=line_match.group(0).rstrip("\r\n")
        if not line and line_match.start()>=len(text):break
        if not line.strip():
            if container_active:blank_after_container=True
            paragraph_active=False;ambiguous_html=False;index+=1;continue
        prefix=_CONTAINER_PREFIX.match(line);leading=len(line)-len(line.lstrip(" \t"))
        if container_active and blank_after_container and not prefix and leading==0:container_active=False;blank_after_container=False
        if prefix:container_active=True;blank_after_container=False
        if container_active:
            record_context(line_match.start(),line,"container")
            index+=1;continue

        indented_code=line.startswith("\t") or leading>=4
        content=line[leading:] if leading<=3 and not indented_code else line
        if indented_code:
            if paragraph_active:record_context(line_match.start(),line,"ambiguous_indentation")
            if not paragraph_active:opaque.append(line_match.span())
            index+=1;continue
        fence=re.match(r"(`{3,}|~{3,})",content)
        if fence:
            marker=fence.group(1);end=line_match.end();index+=1
            while index<len(lines):
                end=lines[index].end();closing=lines[index].group(0).rstrip("\r\n")
                index+=1
                if re.match(rf" {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$",closing):break
            opaque.append((line_match.start(),end));paragraph_active=False;ambiguous_html=False;continue
        raw_special=re.match(r"<(script|pre|style|textarea)(?:\s|>|$)",content,re.I)
        block=re.match(r"</?([A-Za-z][A-Za-z0-9-]*)(?:\s|/?>|$)",content)
        interrupting_raw=(raw_special is not None) or (block is not None and block.group(1).casefold() in html_blocks) or content.startswith(("<!--","<?","<![CDATA["))
        type7_root=bool(_TYPE7_HTML_BLOCK.fullmatch(content))
        if interrupting_raw or (type7_root and not paragraph_active):
            end=line_match.end();index+=1
            closing=re.compile(rf"</{raw_special.group(1)}\s*>",re.I) if raw_special else None
            while index<len(lines):
                current=lines[index].group(0).rstrip("\r\n")
                if not current.strip():break
                end=lines[index].end();index+=1
                if closing and closing.search(current):break
                if content.startswith("<!--") and "-->" in current:break
            opaque.append((line_match.start(),end));paragraph_active=False;ambiguous_html=False;continue
        if type7_root and paragraph_active:ambiguous_html=True

        inline=[]
        for match in re.finditer(r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![A-Z].*?>|</?[A-Za-z][^>\r\n]*>",line):inline.append((line_match.start()+match.start(),line_match.start()+match.end()))
        cursor=0
        while cursor<len(line):
            if line[cursor]!="`":cursor+=1;continue
            end_run=cursor
            while end_run<len(line) and line[end_run]=="`":end_run+=1
            marker=line[cursor:end_run];closing=line.find(marker,end_run)
            if closing>=0:inline.append((line_match.start()+cursor,line_match.start()+closing+len(marker)));cursor=closing+len(marker)
            else:cursor=end_run
        opaque.extend(inline)
        if ambiguous_html:record_context(line_match.start(),line,"ambiguous_html",inline)
        paragraph_active=True;index+=1

    protected=_merge_spans(opaque)
    definition_matches=_reference_definitions(text,protected);definitions:dict[str,str]={};tokens:list[LinkToken]=[]
    for definition in definition_matches:definitions.setdefault(_reference_label_key(definition.label),definition.destination)
    definition_labels=set(definitions)
    for line_start,line,reason,ignored in shortcut_contexts:
        for local,kind in shortcut_candidates(line,definition_labels):
            position=line_start+local
            if not _span_contains(ignored,position):context_failures.append((position,1,reason,kind))
    if context_failures:
        position,_,reason,kind=min(context_failures,key=lambda item:(item[0],item[1]))
        fail(position,reason,kind)
    cross_line_signatures=(
        (r"!?\[\[[^\]]*(?:\r\n|\n|\r)[^\]]*\]\]","wiki"),
        (r"!?\[[^\]]*(?:\r\n|\n|\r)[^\]]*\][ \t]*\(","inline"),
        (r"!?\[[^\]\r\n]*\]\([^\)]*(?:\r\n|\n|\r)[^\)]*\)","inline"),
        (r"!?\[[^\]]*(?:\r\n|\n|\r)[^\]]*\][ \t]*\[[^\]]*\]","reference"),
        (r"!?\[[^\]\r\n]*\]\[[^\]]*(?:\r\n|\n|\r)[^\]]*\]","reference"),
    )
    for pattern,kind in cross_line_signatures:
        for match in re.finditer(pattern,text):
            opening=_candidate_opening(text,match.start())
            if opening is not None and not _span_contains(protected,opening):fail(opening,"cross_line",kind)
    definition_spans=[(definition.start,definition.end) for definition in definition_matches]
    cursor=0
    while cursor<len(text):
        if _span_contains(protected,cursor) or _span_contains(definition_spans,cursor):cursor+=1;continue
        if text[cursor]=="!" and _punctuation_is_escaped(text,cursor):cursor+=1;continue
        image=text.startswith("![[",cursor) or text.startswith("![",cursor)
        start=cursor
        if text.startswith("![[",cursor):opening=cursor+1
        elif text.startswith("[[",cursor):opening=cursor
        else:opening=-1
        if opening>=0 and _punctuation_is_escaped(text,opening):cursor=opening+1;continue
        if opening>=0:
            closing=text.find("]]",opening+2)
            if closing<0:raise error("Markdown contains malformed wiki link syntax","malformed_internal_link",start=start)
            inner=text[opening+2:closing];destination,separator,label=inner.partition("|");label=label if separator else destination
            end=closing+2;dest_start=opening+2;dest_end=dest_start+len(destination)
            if not _external_destination(destination):tokens.append(LinkToken(start,end,text[start:end],destination,label,_link_kind(destination,image),dest_start,dest_end))
            cursor=end;continue
        if image and cursor+1<len(text):opening=cursor+1
        elif text[cursor]=="[":opening=cursor
        else:cursor+=1;continue
        if _punctuation_is_escaped(text,opening):cursor=opening+1;continue
        balanced=_balanced_label(text,opening)
        if balanced is None:
            if text.find("](",opening)>=0:raise error("Markdown contains malformed link label","malformed_internal_link",start=start)
            cursor=opening+1;continue
        label_end,label=balanced;after=label_end+1
        if after<len(text) and text[after]=="(":
            parsed=_inline_destination(text,after)
            if parsed is None:raise error("Markdown contains malformed inline link","malformed_internal_link",start=start)
            end,dest_start,dest_end,destination=parsed
            if not _external_destination(destination):tokens.append(LinkToken(start,end,text[start:end],destination,label,_link_kind(destination,image),dest_start,dest_end))
            cursor=end;continue
        if after<len(text) and text[after]=="[":
            ref_end=text.find("]",after+1)
            if ref_end<0:raise error("Markdown contains malformed reference link","malformed_internal_link",start=start)
            ref=text[after+1:ref_end] or label;destination=definitions.get(_reference_label_key(ref))
            if destination is None:raise error("Markdown reference link has no safe definition","malformed_internal_link",destination=ref,start=start)
            end=ref_end+1
            if not _external_destination(destination):tokens.append(LinkToken(start,end,text[start:end],destination,label,_link_kind(destination,image)))
            cursor=end;continue
        destination=definitions.get(_reference_label_key(label))
        if destination is not None:
            end=label_end+1
            if not _external_destination(destination):tokens.append(LinkToken(start,end,text[start:end],destination,label,_link_kind(destination,image)))
            cursor=end;continue
        cursor=label_end+1
    return MarkdownAnalysis(tuple(protected),tuple(definition_matches),tuple(sorted(tokens,key=lambda item:(item.start,item.end))))


def markdown_links(body: bytes) -> list[LinkToken]:
    return list(_analyze_markdown(body).tokens)


def resolve_link(source_path: str, destination: str, available: set[str]) -> bool:
    return resolved_link_path(source_path, destination, available) is not None


def resolved_link_path(source_path: str, destination: str, available: set[str]) -> str | None:
    dest = unquote(destination.split("#",1)[0].split("?",1)[0]).replace("\\","/")
    if not dest: return source_path
    base = PurePosixPath(source_path).parent
    parts=[]
    for part in (PurePosixPath(dest.lstrip("/")) if dest.startswith("/") else base/PurePosixPath(dest)).parts:
        if part in ("", "."): continue
        if part == "..":
            if not parts: return None
            parts.pop()
        else: parts.append(part)
    candidate="/".join(parts)
    variants={candidate, candidate+".md" if not PurePosixPath(candidate).suffix else candidate, candidate.rstrip("/")+"/index.md"}
    matches = variants & available
    return sorted(matches, key=lambda item: item.encode("utf-8"))[0] if matches else None


def rewrite_link_destinations(body: bytes, source_before: str, source_after: str, mapping: Mapping[str, str], available: set[str]) -> bytes:
    """Rewrite links affected by canonical path moves without touching prose."""

    text = body.decode("utf-8", errors="strict");analysis=_analyze_markdown(body)
    replacements: list[tuple[int, int, str]] = []
    for token in analysis.tokens:
        resolved = resolved_link_path(source_before, token.destination, available)
        if resolved is None:
            continue
        destination_target = mapping.get(resolved, resolved)
        source_moved = source_before != source_after
        if resolved not in mapping and not source_moved:
            continue
        new_destination = posixpath.relpath(destination_target, PurePosixPath(source_after).parent.as_posix())
        if token.destination.startswith("/"):
            new_destination = "/" + destination_target
        fragment = "#" + token.destination.split("#", 1)[1] if "#" in token.destination else ""
        new_destination += fragment
        if token.destination_start is None or token.destination_end is None:
            continue
        relative_start=token.destination_start-token.start;relative_end=token.destination_end-token.start
        rewritten = token.before[:relative_start]+new_destination+token.before[relative_end:]
        if rewritten != token.before:
            replacements.append((token.start, token.end, rewritten))
    # Reference definitions carry the destination while their use token does not.
    for definition in analysis.definitions:
        destination = definition.destination
        resolved = resolved_link_path(source_before, destination, available)
        if resolved is None: continue
        target = mapping.get(resolved, resolved)
        if resolved not in mapping and source_before == source_after: continue
        new_destination = posixpath.relpath(target, PurePosixPath(source_after).parent.as_posix())
        replacement = text[definition.start:definition.destination_start]+new_destination+text[definition.destination_end:definition.end]
        replacements.append((definition.start, definition.end, replacement))
    output = text
    for start, end, replacement in sorted(replacements, reverse=True):
        output = output[:start] + replacement + output[end:]
    return output.encode("utf-8")


def sanitize_body(body: bytes, source_path: str, available: set[str], enabled: bool) -> tuple[bytes,list[dict[str,Any]],list[dict[str,Any]]]:
    text=body.decode("utf-8"); unresolved=[]
    try:tokens=markdown_links(body)
    except CortexError as exc:
        if exc.code!="unsupported_markdown_link_context" or "path" in exc.details:raise
        raise error(str(exc),exc.code,exc.status,path=source_path,**exc.details) from exc
    for token in tokens:
        if not resolve_link(source_path, token.destination, available):
            after = token.label if token.kind=="prose" else f"[missing {token.kind}: {token.label}]"
            start_byte=len(text[:token.start].encode("utf-8")); end_byte=len(text[:token.end].encode("utf-8"))
            unresolved.append((token,start_byte,end_byte,after))
    transformations=[]
    for ordinal,(token,start,end,after) in enumerate(unresolved,1):
        transformations.append({"ordinal":ordinal,"kind":token.kind,"destination":token.destination,"start_byte":start,"end_byte":end,"before":token.before,"after":after})
    if unresolved and not enabled:
        issues=[{"rule_id":"source-link-closure","code":"source_link_closure_required","severity":"error","message":"Incoming draft contains an unresolved internal link","path":source_path,"hint":"Review the exact replacement and retry with --sanitize-links","details":{"destination":token.destination,"kind":token.kind,"start_byte":start,"end_byte":end,"before":token.before,"after":after}} for token,start,end,after in unresolved]
        return body,transformations,issues
    output=bytearray(body)
    for item in reversed(transformations): output[item["start_byte"]:item["end_byte"]]=item["after"].encode("utf-8")
    issues=[{"rule_id":"source-link-closure","code":"source_link_sanitized","severity":"warning","message":"Incoming unresolved link was deterministically sanitized","path":source_path,"hint":None,"details":{"destination":item["destination"],"kind":item["kind"],"start_byte":item["start_byte"],"end_byte":item["end_byte"]}} for item in transformations]
    return bytes(output),transformations,issues


def build_proposal(workspace:Path,context:Mapping[str,Any],proposal_input:Mapping[str,Any]|None,sanitize:bool)->tuple[dict[str,Any],list[dict[str,Any]],list[dict[str,Any]]]:
    if tree_digest(workspace)!=context["base_tree_digest"]: raise error("Bundle changed after context creation","stale_bundle_digest",Status.CONFLICT)
    schema=load_tag_schema(workspace)
    if sha256_digest(tag_schema_bytes(schema))!=context["tag_schema_digest"]: raise error("Tag schema changed after context creation","stale_tag_schema",Status.CONFLICT)
    assignments=proposal_assignments(context,proposal_input)
    drafts=[read_draft(source["source_path"]) for source in context["sources"]]
    for source,draft in zip(context["sources"],drafts,strict=True):
        if source["source_digest"]!=draft.source_digest: raise error("Source changed after context creation","stale_source_digest",Status.CONFLICT,path=str(draft.path))
    id_name,_=_type_policy(schema)
    publication_paths=[]; selected=[]
    for draft,item in zip(drafts,assignments,strict=True):
        tag_value=resolve_tag(schema,item["assignments"][id_name]); selected.append(tag_value); publication_paths.append(publication_path(tag_value["tag"],draft.title,draft.timestamp))
    available={entry["path"] for entry in tree_manifest(workspace,exclude=(".cortex",))["entries"]}|set(publication_paths)
    rewrites=[]; publications=[]; issues=[]
    for draft,path,tag_value in zip(drafts,publication_paths,selected,strict=True):
        output,transforms,source_issues=sanitize_body(draft.body,path,available,sanitize)
        rewrites.append({"source_id":draft.source_id,"input_body_digest":draft.body_digest,"output_body_digest":"sha256:"+hashlib.sha256(output).hexdigest(),"transformations":transforms})
        content=render_publication(draft,tag_value,output)
        publications.append({"source_id":draft.source_id,"path":path,"content_b64":base64.b64encode(content).decode("ascii"),"content_digest":hashlib.sha256(content).hexdigest()})
        issues.extend(source_issues)
    proposal=make_artifact("ingest-proposal",{"context_id":context["artifact_id"],"items":assignments,"link_policy":"sanitize" if sanitize else "reject","source_rewrites":rewrites,"publications":publications},parents=(context,))
    return proposal,issues,publications


def _issue(code:str,message:str,path:str|None=None,**details:object)->dict[str,Any]:
    return {"rule_id":"cortex4","code":code,"severity":"error","message":message,"path":path,"concept_id":None,"operation_id":None,"hint":None,"details":[{"name":str(key),"value":value if value is None or isinstance(value,(str,int,float,bool)) else canonical_json_bytes(value).decode("utf-8")} for key,value in sorted(details.items())]}


def validate_bundle(workspace:Path)->tuple[dict[str,Any],list[dict[str,Any]]]:
    issues=[]
    if not native_is_dir(workspace) or is_reparse(workspace): raise error("Workspace must be a real bundle directory","invalid_workspace")
    manifest=tree_manifest(workspace,exclude=(".cortex",)); files={entry["path"] for entry in manifest["entries"]}
    if ".cortex" in {part for path in files for part in PurePosixPath(path).parts}: issues.append(_issue("reserved_state_path","Portable bundle contains reserved .cortex state"))
    if "index.md" not in files: issues.append(_issue("missing_index","Bundle is missing index.md","index.md"))
    else:
        try:
            index_raw = open(_native_path(workspace / "index.md"), "rb").read()
            index_meta, index_body = _split_frontmatter(index_raw)
            if index_meta != {"okf_version": "0.1"}:
                issues.append(_issue("invalid_okf_version", "index.md must declare only okf_version 0.1", "index.md"))
            for token in markdown_links(index_body):
                if not resolve_link("index.md", token.destination, files):
                    issues.append(_issue("broken_internal_link", "Index contains unresolved internal link", "index.md", destination=token.destination))
        except CortexError as exc:
            issues.append(_issue(exc.code, str(exc), "index.md", **exc.details))
        except (OSError,UnicodeError,TypeError,ValueError) as exc:
            issues.append(_issue("invalid_index",str(exc),"index.md"))
    if TAG_SCHEMA_PATH not in files: issues.append(_issue("missing_tag_schema","Bundle is missing profiles/tag-schema.json",TAG_SCHEMA_PATH))
    try: schema=load_tag_schema(workspace)
    except CortexError as exc:
        issues.append(_issue(exc.code,str(exc),TAG_SCHEMA_PATH,**exc.details)); schema=None
    available=set(files)
    if schema is not None:
        reference_paths = sorted(path for path in files if path.startswith("references/") and path.endswith(".md"))
        if schema["types"]["reference"]["status"] != "active" and reference_paths:
            issues.append(_issue("target_type_unconfigured", "Reference policy must be active when references exist", TAG_SCHEMA_PATH))
            reference_paths = []
        for relative in reference_paths:
            path=workspace.joinpath(*PurePosixPath(relative).parts)
            try:
                raw=open(_native_path(path),"rb").read(); metadata,body=_split_frontmatter(raw)
                if set(metadata)!={"type","title","description","tags","timestamp"} or metadata["type"]!="reference" or metadata["description"]!="": raise error("Canonical reference frontmatter is invalid","invalid_reference_frontmatter")
                tags=metadata["tags"]
                if not isinstance(tags,list) or not tags or any(not isinstance(item,str) for item in tags): raise error("Canonical reference tags are invalid","invalid_reference_tags")
                tag_value=resolve_tag(schema,tags[0])
                if tags != [tag_value["tag"],*tag_value["derived_tags"]]: raise error("Canonical reference tag order is invalid","invalid_reference_tags")
                title, timestamp = metadata["title"], metadata["timestamp"]
                if not isinstance(title, str) or title != PurePosixPath(relative).stem: raise error("Canonical title must equal the filename stem", "noncanonical_reference_title")
                if not _valid_timestamp_text(timestamp): raise error("Canonical timestamp is invalid", "invalid_reference_timestamp")
                compact = timestamp[:10].replace("-", "")
                prefix, suffix = tags[0] + "-", "-" + compact
                if not title.startswith(prefix) or not title.endswith(suffix): raise error("Canonical reference path does not match identifier and date", "noncanonical_reference_path")
                normalized_title = title[len(prefix):-len(suffix)]
                if not normalized_title or normalize_script_title(normalized_title) != normalized_title: raise error("Canonical reference title segment is invalid", "noncanonical_reference_path")
                if raw != render_canonical_reference(title, tags, timestamp, body): raise error("Canonical frontmatter bytes are not deterministic", "noncanonical_reference_frontmatter")
                for token in markdown_links(body):
                    if not resolve_link(relative,token.destination,available): issues.append(_issue("broken_internal_link","Reference contains unresolved internal link",relative,destination=token.destination))
            except CortexError as exc: issues.append(_issue(exc.code,str(exc),relative,**exc.details))
            except (OSError,UnicodeError) as exc: issues.append(_issue("invalid_text_encoding",str(exc),relative))
            except (TypeError,ValueError,KeyError) as exc: issues.append(_issue("invalid_reference_frontmatter",str(exc),relative))
    unexpected_markdown = sorted(path for path in files if path.endswith(".md") and path != "index.md" and not path.startswith("references/"))
    for relative in unexpected_markdown:
        issues.append(_issue("unsupported_document_type", "Cortex 4 MVP authors references only", relative))
    unexpected_profiles = sorted(path for path in files if path.startswith("profiles/") and path != TAG_SCHEMA_PATH)
    for relative in unexpected_profiles:
        issues.append(_issue("unexpected_profile_file", "TagSchema2 is the sole portable profile", relative))
    report=make_artifact("validation-report",{"bundle_identity":state_paths(workspace).identity,"validated_tree_digest":manifest["tree_digest"],"outcome":"fail" if any(item["severity"]=="error" for item in issues) else "pass","counts":{"errors":sum(item["severity"]=="error" for item in issues),"warnings":sum(item["severity"]=="warning" for item in issues)},"issues":issues})
    return report,issues


def path_preflight(workspace:Path,state:StatePaths,paths:Sequence[str])->list[dict[str,Any]]:
    operation_paths = list(paths) or ["index.md"]
    phase_paths: dict[str, list[Path]] = {
        "live": [workspace.joinpath(*PurePosixPath(path).parts) for path in operation_paths],
        "stage": [(state.staging / _DIGEST_PLACEHOLDER / "bundle").joinpath(*PurePosixPath(path).parts) for path in operation_paths],
        "backup": [(state.backups / _DIGEST_PLACEHOLDER / "bundle").joinpath(*PurePosixPath(path).parts) for path in operation_paths],
        "artifact": [
            state.artifacts / ("mutation-plan@" + _DIGEST_PLACEHOLDER + ".json"),
            state.artifacts / (".mutation-plan@" + _DIGEST_PLACEHOLDER + ".json" + _TEMP_SUFFIX_PLACEHOLDER),
            state.artifacts / ("verification-receipt@" + _DIGEST_PLACEHOLDER + ".json"),
            state.root / (".identity.json" + _TEMP_SUFFIX_PLACEHOLDER),
            state.root / (".owner.json" + _TEMP_SUFFIX_PLACEHOLDER),
        ],
        "journal": [
            state.journals / _DIGEST_PLACEHOLDER / "journal.json",
            state.journals / _DIGEST_PLACEHOLDER / (".journal.json" + _TEMP_SUFFIX_PLACEHOLDER),
        ],
        "index": [
            state.indexes / _DIGEST_PLACEHOLDER / "index.json",
            state.indexes / _DIGEST_PLACEHOLDER / (".index.json" + _TEMP_SUFFIX_PLACEHOLDER),
        ],
    }

    def nearest_existing(candidate: Path) -> Path:
        current = candidate
        while not _native_exists(current):
            if current.parent == current:
                raise error("Filesystem capacity cannot be determined", "path_capacity_unknown", Status.POLICY_BLOCKED, logical_path=str(candidate))
            current = current.parent
        return current

    checks: list[dict[str, Any]] = []
    for phase, candidates in phase_paths.items():
        for candidate in candidates:
            native = _native_path(candidate)
            if os.name == "nt":
                observed, limit, metric = len(native.encode("utf-16-le")) // 2, 32760, "utf16_code_units"
            else:
                owner = nearest_existing(candidate.parent)
                try:
                    limit = int(os.pathconf(_native_path(owner), "PC_PATH_MAX"))
                except (OSError, ValueError) as exc:
                    raise error("Filesystem path capacity cannot be determined", "path_capacity_unknown", Status.POLICY_BLOCKED, logical_path=str(candidate), phase=phase) from exc
                observed, metric = len(os.fsencode(str(candidate))), "path_bytes"
            if observed > limit:
                raise error("A planned path exceeds filesystem capacity", "path_capacity_exceeded", logical_path=str(candidate), native_path=native, phase=phase, metric=metric, observed=observed, limit=limit, suggestion="shorten the bundle parent, title, or identifier")
            checks.append({"phase":phase,"logical_path":str(candidate),"native_path":native,"metric":metric,"observed":observed,"limit":limit})
    for relative in operation_paths:
        for component in PurePosixPath(relative).parts:
            metrics = (("component_utf8_bytes", len(component.encode("utf-8"))), ("component_utf16_units", len(component.encode("utf-16-le")) // 2))
            for metric, observed in metrics:
                if observed > 255:
                    raise error("A planned path component exceeds portable capacity", "path_capacity_exceeded", logical_path=relative, native_path=relative, phase="live", metric=metric, observed=observed, limit=255, suggestion="shorten the title or identifier")
            if os.name != "nt":
                owner = nearest_existing(workspace)
                try: name_limit = int(os.pathconf(_native_path(owner), "PC_NAME_MAX"))
                except (OSError, ValueError) as exc: raise error("Filesystem name capacity cannot be determined", "path_capacity_unknown", Status.POLICY_BLOCKED, logical_path=relative) from exc
                observed = len(os.fsencode(component))
                if observed > name_limit: raise error("A planned component exceeds filesystem capacity", "path_capacity_exceeded", logical_path=relative, native_path=relative, phase="live", metric="component_fs_bytes", observed=observed, limit=name_limit, suggestion="shorten the title or identifier")
    try:
        if volume_identity(workspace.parent) != volume_identity(state.root):
            raise error("Bundle and state must share a volume", "cross_volume_state", Status.POLICY_BLOCKED)
    except OSError as exc:
        raise error("Bundle/state volume identity cannot be verified", "volume_identity_unavailable", Status.POLICY_BLOCKED) from exc
    return checks


def _simulate_tree(workspace:Path|None,operations:Sequence[Mapping[str,Any]],temp:Path)->str:
    if _native_exists(temp):
        raise error("Planning scratch unexpectedly exists", "scratch_collision", Status.CONFLICT, path=str(temp))
    if workspace is not None and _native_exists(workspace): copy_tree(workspace,temp)
    else: os.makedirs(_native_path(temp))
    apply_operations(temp,operations)
    return tree_digest(temp)


@contextlib.contextmanager
def _owned_scratch(state: StatePaths, prefix: str) -> Iterator[Path]:
    """Create one invocation-owned, collision-free state scratch directory."""

    os.makedirs(_native_path(state.staging), exist_ok=True)
    created = Path(tempfile.mkdtemp(prefix=prefix, dir=_native_path(state.staging)))
    try:
        yield created
    finally:
        if _native_exists(created):
            remove_tree(created)
            fsync_dir(state.staging)


def operation(kind:str,path:str,content:bytes|None=None,expected:bytes|None=None,index:int=0)->dict[str,Any]:
    return {"id":f"op-{index:04d}","kind":kind,"path":normalize_relative_path(path),"content_b64":None if content is None else base64.b64encode(content).decode("ascii"),"expected_sha256":None if expected is None else hashlib.sha256(expected).hexdigest(),"output_sha256":None if content is None else hashlib.sha256(content).hexdigest()}


def make_plan(workspace:Path,route:str,operations:Sequence[Mapping[str,Any]],parents:Sequence[Mapping[str,Any]]=(),base_digest:str|None=None,tag_digest:str|None=None)->dict[str,Any]:
    state=state_paths(workspace); paths=[str(item["path"]) for item in operations]
    preflight=path_preflight(workspace,state,paths)
    state=ensure_state(workspace)
    with _owned_scratch(state, "plan-check-") as scratch:
        simulation=scratch/"bundle"
        expected=_simulate_tree(workspace if base_digest is not None else None,operations,simulation)
        report, issues = validate_bundle(simulation)
        if report["outcome"] != "pass":
            raise error(
                "The planned snapshot would not be a valid OKF bundle",
                "planned_snapshot_invalid",
                Status.VALIDATION_BLOCKED,
                issues=issues,
            )
    request_digest=sha256_digest(canonical_json_bytes({"route":route,"operations":list(operations)}))
    return make_artifact("mutation-plan",{"route":route,"bundle_identity":state.identity,"base_tree_digest":base_digest,"tag_schema_digest":tag_digest,"request_digest":request_digest,"operations":list(operations),"destructive_operation_ids":[item["id"] for item in operations if item["kind"] in {"replace","move","delete"}],"expected_tree_digest":expected,"path_preflight":preflight},parents=parents)


def apply_operations(root:Path,operations:Sequence[Mapping[str,Any]])->None:
    for item in operations:
        relative=normalize_relative_path(item["path"]); path=root.joinpath(*PurePosixPath(relative).parts)
        if item["kind"]=="mkdir":
            os.makedirs(_native_path(path),exist_ok=True); fsync_dir(path); fsync_dir(path.parent); continue
        existing=open(_native_path(path),"rb").read() if native_is_file(path) else None
        if item["expected_sha256"] is not None and (existing is None or hashlib.sha256(existing).hexdigest()!=item["expected_sha256"]): raise error("Operation preimage changed","operation_preimage_mismatch",Status.CONFLICT,path=relative)
        if item["kind"]=="delete":
            if native_is_dir(path): os.rmdir(_native_path(path))
            elif _native_exists(path): os.unlink(_native_path(path))
            fsync_dir(path.parent)
            continue
        content=base64.b64decode(item["content_b64"],validate=True)
        os.makedirs(_native_path(path.parent),exist_ok=True); atomic_bytes(path,content)


def _journal_snapshot(plan:Mapping[str,Any],state_name:str,current:str|None,events:list[dict[str,Any]])->dict[str,Any]:
    return make_artifact("apply-journal",{"plan_id":plan["artifact_id"],"state":state_name,"base_tree_digest":plan["base_tree_digest"],"current_tree_digest":current,"events":events},parents=(plan,))


class SimulatedCrash(RuntimeError):
    """Test-only interruption that deliberately leaves the journal resumable."""


def _fault(point: str) -> None:
    if os.environ.get("CORTEX_TEST_FAULT") == point:
        raise SimulatedCrash(point)


def _event(events: Sequence[Mapping[str, Any]], name: str, **details: Any) -> list[dict[str, Any]]:
    return [*map(dict, events), {"sequence": len(events) + 1, "event": name, **details}]


def _write_journal(state: StatePaths, path: Path, plan: Mapping[str, Any], state_name: str, current: str | None, events: list[dict[str, Any]]) -> dict[str, Any]:
    journal = _journal_snapshot(plan, state_name, current, events)
    persist_artifact(state, journal)
    atomic_bytes(path, canonical_json_bytes(journal) + b"\n")
    fsync_dir(path.parent)
    return journal


def _tree_or_none(path: Path) -> str | None:
    return tree_digest(path) if native_is_dir(path) and not is_reparse(path) else None


_NAMESPACE_MARKER = "owner.json"


def _namespace_token(events: Sequence[Mapping[str,Any]], namespace_name: str) -> str | None:
    property_name=f"{namespace_name}_ownership_token"
    values=[str(item[property_name]) for item in events if property_name in item]
    if len(set(values))>1:raise error("Journal contains conflicting namespace ownership evidence","journal_namespace_mismatch",Status.CONFLICT,namespace=namespace_name)
    return values[0] if values else None


def _namespace_marker_bytes(plan: Mapping[str,Any], namespace_name: str, token: str) -> bytes:
    return canonical_json_bytes({"schema_version":"1.0.0","plan_id":plan["artifact_id"],"namespace":namespace_name,"ownership_token":token})+b"\n"


def _namespace_problem(namespace: Path, namespace_name: str, plan: Mapping[str,Any], events: Sequence[Mapping[str,Any]]) -> str | None:
    if not _native_exists(namespace):return None
    token=_namespace_token(events,namespace_name)
    if token is None:return "authorization_missing"
    if not native_is_dir(namespace) or is_reparse(namespace):return "namespace_not_directory"
    names=sorted((entry.name for entry in os.scandir(_native_path(namespace))),key=lambda item:item.encode("utf-8"))
    if any(name not in {_NAMESPACE_MARKER,"bundle"} for name in names):return "unknown_entries"
    marker=namespace/_NAMESPACE_MARKER
    if not native_is_file(marker) or is_reparse(marker):return "marker_missing"
    try:observed=open(_native_path(marker),"rb").read()
    except OSError:return "marker_unreadable"
    if observed!=_namespace_marker_bytes(plan,namespace_name,token):return "marker_mismatch"
    bundle=namespace/"bundle"
    if "bundle" in names and (not native_is_dir(bundle) or is_reparse(bundle)):return "bundle_not_directory"
    return None


def _ambiguous(state: StatePaths, journal_path: Path, plan: Mapping[str, Any], events: list[dict[str, Any]], reason: str, workspace: Path, stage: Path, backup: Path) -> None:
    observed = {"live": _tree_or_none(workspace), "stage": _tree_or_none(stage), "backup": _tree_or_none(backup)}
    events = _event(events, "recovery_ambiguous", reason=reason, observed=observed)
    _write_journal(state, journal_path, plan, "recovery_ambiguous", observed["live"], events)
    raise error("Transaction trees cannot be recovered without overwriting uncertain state", "recovery_ambiguous", Status.INTERRUPTED, reason=reason, observed=observed)


def _require_owned_namespaces(state: StatePaths, journal_path: Path, plan: Mapping[str, Any], events: list[dict[str, Any]], workspace: Path, stage: Path, backup: Path) -> None:
    problems={name:problem for name,path in (("staging",stage.parent),("backup",backup.parent)) if (problem:=_namespace_problem(path,name,plan,events)) is not None}
    if problems:_ambiguous(state,journal_path,plan,events,"transaction_namespace_ownership_unproven",workspace,stage,backup)


def _authorize_namespace(state: StatePaths, journal_path: Path, plan: Mapping[str,Any], state_name: str, current: str | None, events: list[dict[str,Any]], namespace_name: str) -> list[dict[str,Any]]:
    if _namespace_token(events,namespace_name) is None:raise error("Journal lacks namespace ownership authority","journal_namespace_mismatch",Status.CONFLICT,namespace=namespace_name,state=state_name,current=current)
    return events


def _claim_namespace(state: StatePaths, journal_path: Path, plan: Mapping[str,Any], events: list[dict[str,Any]], namespace_name: str, namespace: Path, workspace: Path, stage: Path, backup: Path) -> None:
    if _native_exists(namespace):
        _require_owned_namespaces(state,journal_path,plan,events,workspace,stage,backup);return
    token=_namespace_token(events,namespace_name)
    if token is None:raise error("Namespace creation lacks durable journal authority","journal_namespace_mismatch",Status.CONFLICT,namespace=namespace_name)
    try:os.makedirs(_native_path(namespace),exist_ok=False)
    except FileExistsError:
        _ambiguous(state,journal_path,plan,events,"transaction_namespace_creation_collision",workspace,stage,backup)
    marker=namespace/_NAMESPACE_MARKER
    try:
        with open(_native_path(marker),"xb") as handle:
            handle.write(_namespace_marker_bytes(plan,namespace_name,token));handle.flush();os.fsync(handle.fileno())
        fsync_dir(namespace);fsync_dir(namespace.parent)
    except (FileExistsError,OSError):
        _ambiguous(state,journal_path,plan,events,"transaction_namespace_marker_collision",workspace,stage,backup)
    _require_owned_namespaces(state,journal_path,plan,events,workspace,stage,backup)


def _delete_owned_namespace(state: StatePaths, journal_path: Path, plan: Mapping[str,Any], events: list[dict[str,Any]], namespace: Path, workspace: Path, stage: Path, backup: Path) -> None:
    if not _native_exists(namespace):return
    _require_owned_namespaces(state,journal_path,plan,events,workspace,stage,backup)
    remove_tree(namespace);fsync_dir(namespace.parent)


def apply_plan(workspace:Path,plan:Mapping[str,Any])->tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    state=ensure_state(workspace); digest=plan["digest"]; run=state.journals/digest; journal_path=run/"journal.json"; stage=state.staging/digest/"bundle"; backup=state.backups/digest/"bundle"
    with bundle_lock(state):
        if plan["bundle_identity"]!=state.identity: raise error("Plan belongs to another bundle path","plan_bundle_mismatch",Status.CONFLICT)
        path_preflight(workspace,state,[item["path"] for item in plan["operations"]])
        if not all(durability_supported(path) for path in (state.root, state.journals, state.staging, state.backups, workspace.parent)):
            raise error("Filesystem cannot provide the required durable publication barriers", "durable_publish_unsupported", Status.UNSUPPORTED)
        base_exists = plan["base_tree_digest"] is not None
        if _native_exists(journal_path):
            existing=strict_json(open(_native_path(journal_path),"rb").read(),subject="apply journal")
            validate_contract(existing, "apply-journal")
            if existing["plan_id"]!=plan["artifact_id"]: raise error("Journal belongs to another plan","journal_plan_mismatch",Status.CONFLICT)
            events = list(existing["events"])
            _require_owned_namespaces(state, journal_path, plan, events, workspace, stage, backup)
            if existing["state"] in {"completed","failed","aborted"}:
                if existing["state"] == "completed":
                    live, staged, parked = _tree_or_none(workspace), _tree_or_none(stage), _tree_or_none(backup)
                    safe_stage = staged in {None, plan["expected_tree_digest"]}
                    safe_backup = parked in {None, plan["base_tree_digest"]}
                    if live != plan["expected_tree_digest"] or not safe_stage or not safe_backup:
                        _ambiguous(state, journal_path, plan, events, "completed_cleanup_mismatch", workspace, stage, backup)
                    _delete_owned_namespace(state,journal_path,plan,events,backup.parent,workspace,stage,backup)
                    _delete_owned_namespace(state,journal_path,plan,events,stage.parent,workspace,stage,backup)
                raise error("Plan was already consumed","plan_consumed",Status.CONFLICT,plan_id=plan["artifact_id"])
            if existing["state"] == "recovery_ambiguous": raise error("Transaction requires manual recovery", "recovery_ambiguous", Status.INTERRUPTED, plan_id=plan["artifact_id"])
            state_name = existing["state"]
        else:
            collisions = [str(namespace) for namespace in (stage.parent, backup.parent) if _native_exists(namespace)]
            if collisions:
                raise error("Transaction scratch namespace already exists without its journal", "scratch_collision", Status.CONFLICT, paths=collisions)
            if plan["route"] != "manage.init" and base_exists and plan.get("tag_schema_digest") is not None:
                try: observed_schema = tag_schema_digest(workspace)
                except CortexError: observed_schema = None
                if observed_schema != plan["tag_schema_digest"]:
                    raise error("Tag schema changed after planning", "stale_tag_schema", Status.CONFLICT, expected=plan["tag_schema_digest"], observed=observed_schema)
            os.makedirs(_native_path(run),exist_ok=False)
            fsync_dir(run.parent); events=_event([], "claimed", base_tree_digest=plan["base_tree_digest"],staging_ownership_token=hashlib.sha256(os.urandom(32)).hexdigest(),backup_ownership_token=hashlib.sha256(os.urandom(32)).hexdigest())
            _write_journal(state, journal_path, plan, "claimed", plan["base_tree_digest"], events)
            state_name = "claimed"; _fault("after_claim")

        live_digest, stage_digest, backup_digest = _tree_or_none(workspace), _tree_or_none(stage), _tree_or_none(backup)
        if state_name == "claimed":
            if live_digest != plan["base_tree_digest"]:
                _ambiguous(state, journal_path, plan, events, "claimed_live_mismatch", workspace, stage, backup)
            if stage_digest != plan["expected_tree_digest"]:
                events=_authorize_namespace(state,journal_path,plan,"claimed",plan["base_tree_digest"],events,"staging")
                _delete_owned_namespace(state,journal_path,plan,events,stage.parent,workspace,stage,backup)
                _claim_namespace(state,journal_path,plan,events,"staging",stage.parent,workspace,stage,backup)
                if base_exists: copy_tree(workspace, stage)
                else: os.makedirs(_native_path(stage), exist_ok=False)
                apply_operations(stage, plan["operations"])
                fsync_dir(stage)
                stage_digest = tree_digest(stage)
            if stage_digest != plan["expected_tree_digest"]:
                _ambiguous(state, journal_path, plan, events, "staged_tree_mismatch", workspace, stage, backup)
            report, issues = validate_bundle(stage)
            if report["outcome"] != "pass":
                events = _event(events, "failed", reason="staged_validation_failed")
                _write_journal(state, journal_path, plan, "failed", stage_digest, events)
                raise error("Staged bundle failed full validation", "staged_validation_failed", issues=issues)
            events=_event(events,"staged",tree_digest=stage_digest);_write_journal(state,journal_path,plan,"staged",stage_digest,events);state_name="staged";_fault("after_stage")

        live_digest, stage_digest, backup_digest = _tree_or_none(workspace), _tree_or_none(stage), _tree_or_none(backup)
        if state_name == "staged":
            if stage_digest != plan["expected_tree_digest"]:
                _ambiguous(state, journal_path, plan, events, "staged_artifact_missing_or_changed", workspace, stage, backup)
            if base_exists:
                if live_digest == plan["base_tree_digest"] and backup_digest is None:
                    events=_authorize_namespace(state,journal_path,plan,"staged",stage_digest,events,"backup")
                    _delete_owned_namespace(state,journal_path,plan,events,backup.parent,workspace,stage,backup)
                    _claim_namespace(state,journal_path,plan,events,"backup",backup.parent,workspace,stage,backup)
                    _fault("before_park"); os.replace(_native_path(workspace),_native_path(backup));fsync_dir(workspace.parent);_fault("after_park_effect")
                    backup_digest, live_digest = _tree_or_none(backup), _tree_or_none(workspace)
                if live_digest is not None or backup_digest != plan["base_tree_digest"]:
                    _ambiguous(state, journal_path, plan, events, "parked_tree_mismatch", workspace, stage, backup)
            elif live_digest is not None or backup_digest is not None:
                _ambiguous(state, journal_path, plan, events, "init_publication_root_appeared", workspace, stage, backup)
            events=_event(events,"parked",backup_tree_digest=backup_digest);_write_journal(state,journal_path,plan,"parked",backup_digest,events);state_name="parked";_fault("after_park")

        live_digest, stage_digest, backup_digest = _tree_or_none(workspace), _tree_or_none(stage), _tree_or_none(backup)
        if state_name == "parked":
            if live_digest == plan["expected_tree_digest"] and stage_digest is None:
                pass  # publish effect completed before its journal transition
            else:
                if live_digest is not None or stage_digest != plan["expected_tree_digest"] or (base_exists and backup_digest != plan["base_tree_digest"]):
                    _ambiguous(state, journal_path, plan, events, "publish_precondition_mismatch", workspace, stage, backup)
                _fault("before_publish");os.replace(_native_path(stage),_native_path(workspace));fsync_dir(workspace.parent);_fault("after_publish_effect")
                live_digest = _tree_or_none(workspace)
            if live_digest != plan["expected_tree_digest"]:
                _ambiguous(state, journal_path, plan, events, "published_tree_mismatch", workspace, stage, backup)
            events=_event(events,"published",tree_digest=live_digest);journal=_write_journal(state,journal_path,plan,"published",live_digest,events);state_name="published";_fault("after_publish")

        published, backup_digest = _tree_or_none(workspace), _tree_or_none(backup)
        if state_name != "published" or published != plan["expected_tree_digest"]:
            _ambiguous(state, journal_path, plan, events, "receipt_precondition_mismatch", workspace, stage, backup)
        if base_exists and backup_digest != plan["base_tree_digest"]:
            _ambiguous(state, journal_path, plan, events, "backup_changed_before_receipt", workspace, stage, backup)
        report,issues=validate_bundle(workspace)
        if report["outcome"]!="pass": _ambiguous(state,journal_path,plan,events,"published_validation_failed",workspace,stage,backup)
        persist_artifact(state,report)
        published_journal = _journal_snapshot(plan,"published",published,events); persist_artifact(state,published_journal)
        receipt=make_artifact("verification-receipt",{"plan_id":plan["artifact_id"],"bundle_identity":state.identity,"base_tree_digest":plan["base_tree_digest"],"published_tree_digest":published,"validation_report_ref":artifact_ref(report),"journal_ref":artifact_ref(published_journal),"lineage":list(plan.get("parents",[]))},parents=(plan,report,published_journal))
        persist_artifact(state,receipt);fsync_dir(state.artifacts);_fault("after_receipt")
        if base_exists and _tree_or_none(backup) != plan["base_tree_digest"]:
            _ambiguous(state,journal_path,plan,events,"backup_changed_after_receipt",workspace,stage,backup)
        events=_event(events,"completed",receipt_id=receipt["artifact_id"]);journal=_write_journal(state,journal_path,plan,"completed",published,events);_fault("after_terminal")
        _delete_owned_namespace(state,journal_path,plan,events,backup.parent,workspace,stage,backup)
        _delete_owned_namespace(state,journal_path,plan,events,stage.parent,workspace,stage,backup)
        return receipt,report,journal


def conflict_artifacts(workspace:Path,context:Mapping[str,Any],proposal:Mapping[str,Any])->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    conflicts=[]; operations=[]
    for index,pub in enumerate(proposal["publications"]):
        path=workspace.joinpath(*PurePosixPath(pub["path"]).parts); proposed=base64.b64decode(pub["content_b64"])
        if _native_exists(path):
            existing=open(_native_path(path),"rb").read()
            if existing==proposed: continue
            diff="".join(difflib.unified_diff(existing.decode("utf-8",errors="replace").splitlines(True),proposed.decode("utf-8").splitlines(True),fromfile=pub["path"],tofile=pub["path"]+" (proposed)"))
            conflict=make_artifact("ingest-conflict",{"context_ref":artifact_ref(context),"proposal_ref":artifact_ref(proposal),"path":pub["path"],"existing_digest":hashlib.sha256(existing).hexdigest(),"proposed_digest":hashlib.sha256(proposed).hexdigest(),"diff":diff},parents=(context,proposal)); conflicts.append(conflict)
        else: operations.append(operation("create",pub["path"],proposed,index=index))
    return conflicts,operations


def config_compatible(workspace:Path,new_schema:Mapping[str,Any])->None:
    state = ensure_state(workspace)
    with _owned_scratch(state, "config-check-") as scratch:
        temp = scratch / "bundle"
        copy_tree(workspace, temp); atomic_bytes(temp/"profiles"/"tag-schema.json",tag_schema_bytes(new_schema))
        report,_=validate_bundle(temp)
        if report["outcome"]!="pass": raise error("New tag schema invalidates current references","incompatible_tag_schema",Status.POLICY_BLOCKED,issues=report["issues"])


__all__=["StatePaths","apply_plan","bundle_identity","build_proposal","config_compatible","conflict_artifacts","context_for","ensure_state","error","load_artifact","load_tag_schema","make_plan","operation","path_preflight","persist_artifact","read_json_operand","state_paths","strict_json","tag_schema_bytes","tree_digest","validate_bundle","validate_tag_schema"]
