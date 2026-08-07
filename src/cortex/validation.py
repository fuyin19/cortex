"""OKF conformance, Cortex profile, health, and closure validation."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .canonical import _native_path, sha256_digest, tree_manifest
from .contracts import make_artifact
from .errors import CortexError, Status
from .okf import (
    RESERVED_FILENAMES,
    SUPPORTED_OKF_VERSION,
    discover_bundle_version,
    extract_markdown_links,
    parse_concept,
    parse_root_index,
    resolve_internal_link,
)
from .paths import collision_key, normalize_relative_path
from .policy import EffectivePolicy, TagSchema, load_tag_schema, resolve_effective_policy, resolve_tag_value


AUTHORING_PROFILE = "cortex-authoring-v1"
IMPORT_PROFILE = "cortex-import-v1"
NATIVE_PROFILE = "cortex-native-v1"
_PROFILE_REQUIRED_PATHS = (
    "index.md",
    "log.md",
    "references",
    "concepts",
    "entities",
    "outputs",
    "assets",
    "profiles",
    "profiles/cortex-v1.md",
)
_DATE_HEADING = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
_H1 = re.compile(r"^#\s+\S")
_PROFILE_MARKER = re.compile(r"^Profile:\s*(\S+)\s*$", re.MULTILINE)
_REFERENCE_DATE_SUFFIX = re.compile(r"-(\d{8})$")


def _issue(
    code: str,
    message: str,
    *,
    path: str | None,
    severity: str = "error",
    rule_id: str = "okf",
    concept_id: str | None = None,
    hint: str | None = None,
    details: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "code": code,
        "severity": severity,
        "message": message,
        "path": path,
        "concept_id": concept_id,
        "operation_id": None,
        "hint": hint,
        "details": list(details),
    }


def _read_text(path: Path, relative: str) -> str:
    try:
        # Staging trees can push long reference filenames past Windows MAX_PATH.
        with open(_native_path(path), "rb") as stream:
            raw = stream.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise UnicodeError("BOM")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise CortexError(
            "Markdown file is not strict UTF-8",
            status=Status.VALIDATION_BLOCKED,
            code="invalid_utf8",
            details={"path": relative},
        ) from exc


def _reserved_issues(root: Path, path: Path, relative: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        text = _read_text(path, relative)
    except CortexError:
        return [_issue("invalid_utf8", "Reserved document is not strict UTF-8", path=relative)]
    body = text
    if path.name.casefold() == "index.md":
        if relative == "index.md":
            try:
                _, body = parse_root_index(root)
            except CortexError as exc:
                return [_issue(exc.code, str(exc), path=relative)]
        elif text.startswith("---"):
            issues.append(_issue("nested_index_frontmatter", "Only root index.md may have frontmatter", path=relative))
        if not any(_H1.match(line) for line in body.splitlines()):
            issues.append(_issue("invalid_index_structure", "index.md must contain at least one H1 section", path=relative))
    else:
        headings = [line for line in body.splitlines() if line.startswith("#")]
        if not headings or not _H1.match(headings[0]):
            issues.append(_issue("invalid_log_structure", "log.md must start with an H1 heading", path=relative))
        for line in headings[1:]:
            if line.startswith("##"):
                match = _DATE_HEADING.match(line)
                if match is None:
                    issues.append(_issue("invalid_log_date", "Log H2 headings must be YYYY-MM-DD", path=relative))
                    continue
                try:
                    date.fromisoformat(match.group(1))
                except ValueError:
                    issues.append(_issue("invalid_log_date", "Log date heading is not a real date", path=relative))
    return issues


def _profile_issues(frontmatter: Mapping[str, Any], relative: str, profile: str | None) -> list[dict[str, Any]]:
    if profile is None or profile in {IMPORT_PROFILE, NATIVE_PROFILE}:
        return []
    if profile != AUTHORING_PROFILE:
        return [_issue("unknown_profile", "Unknown Cortex validation profile", path=relative, rule_id="profile")]
    issues: list[dict[str, Any]] = []
    for field in ("title", "description", "timestamp"):
        value = frontmatter.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(
                _issue(
                    "missing_profile_field",
                    f"Authoring profile requires non-empty {field}",
                    path=relative,
                    rule_id="profile",
                    details=[{"name": "field", "value": field}],
                )
            )
    timestamp = frontmatter.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, (datetime, date)):
        try:
            datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            issues.append(_issue("invalid_timestamp", "timestamp must be ISO 8601", path=relative, rule_id="profile"))
    return issues


def _native_profile_document_issues(frontmatter: Mapping[str, Any], body: str, relative: str) -> list[dict[str, Any]]:
    if relative != "profiles/cortex-v1.md":
        return []
    issues: list[dict[str, Any]] = []
    expected_fields = {"type", "title", "description", "tags", "timestamp"}
    if set(frontmatter) != expected_fields:
        issues.append(_issue("invalid_native_profile", "Native profile frontmatter must have exactly five fields", path=relative, rule_id="profile"))
    if frontmatter.get("type") != "profile" or not isinstance(frontmatter.get("title"), str) or not str(frontmatter.get("title")).strip():
        issues.append(_issue("invalid_native_profile", "Native profile type/title are invalid", path=relative, rule_id="profile"))
    if frontmatter.get("description") != "" or frontmatter.get("tags") != [] or str(frontmatter.get("timestamp")) != "2026-07-17":
        issues.append(_issue("invalid_native_profile", "Native profile fixed metadata is invalid", path=relative, rule_id="profile"))
    for marker in ("Profile: cortex-native-v1", "Tag schema: profiles/policy/tag-schema.json"):
        if marker not in body.splitlines():
            issues.append(_issue("invalid_native_profile_marker", "Native profile marker is missing", path=relative, rule_id="profile", details=[{"name":"marker","value":marker}]))
    return issues


def _policy_issues(frontmatter: Mapping[str, Any], relative: str, policy: EffectivePolicy) -> list[dict[str, Any]]:
    """Apply only generic, package-declared policy requirements.

    Domain naming, tag vocabulary, and registry semantics intentionally stay in
    the portable package rather than being hard-coded in Cortex.
    """

    if policy.state != "valid" or policy.manifest is None:
        return []
    issues: list[dict[str, Any]] = []
    for field in policy.manifest.get("required_frontmatter_fields", []):
        value = frontmatter.get(str(field))
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(
                _issue(
                    "missing_policy_field",
                    f"Policy package requires non-empty {field}",
                    path=relative,
                    rule_id="policy",
                    details=[{"name": "field", "value": str(field)}],
                )
            )
    return issues


def _canonical_reference_issues(
    frontmatter: Mapping[str, Any],
    relative: str,
    schema: TagSchema | None,
) -> list[dict[str, Any]]:
    if schema is None or schema.statuses.get("reference") != "active" or not relative.startswith("references/"):
        return []
    issues: list[dict[str, Any]] = []
    expected_fields = {"type", "title", "description", "tags", "timestamp"}
    if set(frontmatter) != expected_fields:
        issues.append(_issue("noncanonical_frontmatter", "Reference frontmatter must have exactly five fields", path=relative, rule_id="policy"))
    stem = Path(relative).stem
    if frontmatter.get("type") != "reference":
        issues.append(_issue("invalid_reference_type", "Reference type must be canonical lowercase reference", path=relative, rule_id="policy"))
    if frontmatter.get("title") != stem:
        issues.append(_issue("invalid_reference_title", "Reference title must equal the filename stem", path=relative, rule_id="policy"))
    if frontmatter.get("description") != "":
        issues.append(_issue("invalid_reference_description", "Reference description must be empty", path=relative, rule_id="policy"))
    timestamp = frontmatter.get("timestamp")
    valid_timestamp = False
    if isinstance(timestamp, datetime):
        valid_timestamp = timestamp.tzinfo is not None and timestamp.utcoffset() is not None
    elif isinstance(timestamp, date):
        valid_timestamp = True
    elif isinstance(timestamp, str) and timestamp.strip():
        try:
            text = timestamp.strip()
            if "T" in text or " " in text:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                valid_timestamp = parsed.tzinfo is not None and parsed.utcoffset() is not None
            else:
                date.fromisoformat(text)
                valid_timestamp = True
        except ValueError:
            valid_timestamp = False
    if not valid_timestamp:
        issues.append(_issue("invalid_timestamp", "Reference timestamp must be an ISO date or timezone-aware datetime", path=relative, rule_id="policy"))
    tags = frontmatter.get("tags")
    project = next((item for item in schema.dimensions if item.name == "project"), None)
    if project is None or not isinstance(tags, list) or len(tags) != 2 or any(not isinstance(item, str) for item in tags):
        issues.append(_issue("invalid_reference_tags", "Reference tags must be the exact canonical pair", path=relative, rule_id="policy"))
    else:
        try:
            selected = resolve_tag_value(project, tags[0])
            if tags != [selected.tag, *selected.derived_tags]:
                raise ValueError
            if not stem.startswith(selected.tag + "-"):
                issues.append(_issue("invalid_reference_filename", "Reference filename must begin with its identifier tag", path=relative, rule_id="policy"))
        except (CortexError, ValueError):
            issues.append(_issue("invalid_reference_tags", "Reference tags do not match the tag schema", path=relative, rule_id="policy"))
    match = _REFERENCE_DATE_SUFFIX.search(stem)
    if match is None:
        issues.append(_issue("invalid_reference_filename", "Reference filename must end in YYYYMMDD", path=relative, rule_id="policy"))
    else:
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError:
            issues.append(_issue("invalid_reference_filename", "Reference filename date is not a real date", path=relative, rule_id="policy"))
    return issues


def _shape_issues(root: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for relative in _PROFILE_REQUIRED_PATHS:
        target = root.joinpath(*relative.split("/"))
        if not target.exists():
            issues.append(_issue("missing_profile_path", "Cortex bundle profile path is missing", path=relative, rule_id="profile"))
    return issues


def discover_cortex_profile(root: str | Path) -> str:
    """Return the validation profile declared by the canonical bundle.

    Bundles created before the explicit marker was introduced retain the
    authoring profile.  Imported bundles declare ``cortex-import-v1`` in the
    self-describing profile document.
    """

    bundle = Path(root)
    marker_path = bundle / "profiles" / "cortex-v1.md"
    if not marker_path.is_file() or marker_path.is_symlink():
        return AUTHORING_PROFILE
    text = _read_text(marker_path, "profiles/cortex-v1.md")
    match = _PROFILE_MARKER.search(text)
    return match.group(1) if match is not None else AUTHORING_PROFILE


def _closure_issues(root: Path, markdown_paths: list[Path], *, profile: str | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for path in markdown_paths:
        relative = path.relative_to(root).as_posix()
        try:
            text = _read_text(path, relative)
        except CortexError:
            continue
        for destination in extract_markdown_links(text):
            try:
                target = resolve_internal_link(root, relative, destination)
            except CortexError as exc:
                severity = "warning" if profile == IMPORT_PROFILE and relative not in {"index.md", "log.md"} else "error"
                issues.append(_issue(exc.code, str(exc), path=relative, severity=severity, rule_id="closure"))
                continue
            if target is None:
                continue
            native_target = _native_path(target)
            if not os.path.exists(native_target) or os.path.islink(native_target):
                severity = "warning" if profile == IMPORT_PROFILE and relative not in {"index.md", "log.md"} else "error"
                issues.append(
                    _issue(
                        "broken_internal_link",
                        "Internal bundle link does not resolve to a real file",
                        path=relative,
                        severity=severity,
                        rule_id="closure",
                        details=[{"name": "target", "value": destination}],
                    )
                )
    return issues


def _logical_target(root: Path, content_digest: str, target_id: str | None) -> dict[str, str]:
    identifier = target_id or re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "bundle"
    return {
        "id": identifier,
        "format": "okf-0.1",
        "path": f"bundles/{identifier}",
        "adapter": "okf-native-v1",
        "content_digest": content_digest,
        "state_namespace": f"{identifier}--sha256-{content_digest}",
    }


def validate_bundle(
    bundle_root: str | Path,
    *,
    profile: str | None = None,
    check_closure: bool = False,
    strict_health: bool = False,
    target_id: str | None = None,
    effective_policy: EffectivePolicy | None = None,
) -> dict[str, Any]:
    """Return a content-addressed ValidationReport for one local OKF bundle.

    Broken links are tolerated by plain OKF conformance, as required by the
    upstream specification.  They become hard errors only for closure or
    strict-health validation.
    """

    root = Path(bundle_root)
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise CortexError(
            "Bundle root must be an existing real directory",
            status=Status.VALIDATION_BLOCKED,
            code="invalid_bundle_root",
            details={"path": str(root)},
        )
    root = root.resolve()
    policy = effective_policy or resolve_effective_policy(root)
    issues: list[dict[str, Any]] = []
    tag_schema: TagSchema | None = None
    if policy.state == "invalid":
        issues.append(
            _issue(
                "invalid_policy_package",
                policy.error or "Policy package is invalid",
                path=policy.manifest_path,
                severity="blocker",
                rule_id="policy",
            )
        )
    elif policy.state == "valid":
        try:
            tag_schema = load_tag_schema(root, policy)
        except CortexError as exc:
            issues.append(_issue(exc.code, str(exc), path=policy.manifest_path, severity="blocker", rule_id="policy"))
    try:
        manifest = tree_manifest(root)
        content_digest = manifest["tree_digest"]
    except CortexError as exc:
        content_digest = sha256_digest(b"")
        issues.append(_issue(exc.code, str(exc), path=exc.details.get("path"), severity="blocker", rule_id="health"))

    # Package-owned Markdown (for example a naming guide) is policy data, not
    # an OKF concept.  Its existence and bytes are already authenticated by
    # the native manifest, so do not impose concept frontmatter on it.
    policy_paths = {
        str(item["path"])
        for item in (policy.manifest or {}).get("files", ())
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    markdown_paths = sorted(
        (path for path in root.rglob("*.md") if path.relative_to(root).as_posix() not in policy_paths),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    )
    collision_keys: dict[str, str] = {}
    for path in markdown_paths:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            issues.append(_issue("symlink_escape", "Symbolic links are forbidden", path=relative, severity="blocker", rule_id="health"))
            continue
        try:
            normalized = normalize_relative_path(relative)
        except CortexError as exc:
            issues.append(_issue(exc.code, str(exc), path=relative, severity="blocker", rule_id="health"))
            continue
        if normalized != relative or unicodedata.normalize("NFC", relative) != relative:
            issues.append(_issue("noncanonical_path", "Bundle paths must already be NFC POSIX paths", path=relative, rule_id="health"))
        key = collision_key(relative)
        previous = collision_keys.get(key)
        if previous is not None and previous != relative:
            issues.append(
                _issue(
                    "path_collision",
                    "Bundle paths collide under cross-platform case folding",
                    path=relative,
                    severity="blocker",
                    rule_id="health",
                    details=[{"name": "other", "value": previous}],
                )
            )
        collision_keys[key] = relative
        if path.name.casefold() in RESERVED_FILENAMES:
            issues.extend(_reserved_issues(root, path, relative))
            continue
        try:
            concept = parse_concept(path, bundle_root=root)
        except CortexError as exc:
            issues.append(_issue(exc.code, str(exc), path=relative, rule_id="okf"))
            continue
        issues.extend(_profile_issues(concept.frontmatter, relative, profile))
        if profile == NATIVE_PROFILE:
            issues.extend(_native_profile_document_issues(concept.frontmatter, concept.body, relative))
        issues.extend(_policy_issues(concept.frontmatter, relative, policy))
        issues.extend(_canonical_reference_issues(concept.frontmatter, relative, tag_schema))

    try:
        version = discover_bundle_version(root)
        if version is not None and version != SUPPORTED_OKF_VERSION:
            issues.append(
                _issue(
                    "unsupported_okf_version",
                    f"Declared OKF version {version} is not supported",
                    path="index.md",
                    severity="error",
                    rule_id="version",
                )
            )
    except CortexError as exc:
        issues.append(_issue(exc.code, str(exc), path="index.md", rule_id="okf"))

    if profile is not None:
        issues.extend(_shape_issues(root))
    if profile == NATIVE_PROFILE:
        expected_profile_files = {
            "profiles/cortex-v1.md",
            "profiles/policy-package.json",
            "profiles/policy/tag-schema.json",
        }
        observed_profile_files = {
            path.relative_to(root).as_posix()
            for path in (root / "profiles").rglob("*")
            if path.is_file()
        }
        for extra in sorted(observed_profile_files - expected_profile_files):
            issues.append(_issue("unexpected_policy_file", "Native profile contains a legacy or duplicate policy file", path=extra, rule_id="policy"))
        for missing in sorted(expected_profile_files - observed_profile_files):
            issues.append(_issue("missing_policy_file", "Native profile file is missing", path=missing, rule_id="policy"))
    if check_closure or strict_health:
        issues.extend(_closure_issues(root, markdown_paths, profile=profile))
    for forbidden in (root / ".cortex", root / "cortex.yaml"):
        if forbidden.exists():
            issues.append(
                _issue(
                    "managed_state_in_bundle",
                    "Bundle must remain self-describing without Cortex managed state",
                    path=forbidden.relative_to(root).as_posix(),
                    severity="blocker",
                    rule_id="closure",
                )
            )

    errors = sum(issue["severity"] in {"blocker", "error"} for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    infos = sum(issue["severity"] == "info" for issue in issues)
    if profile is not None and (check_closure or strict_health):
        mode = "full"
    elif profile is not None:
        mode = "profile"
    elif check_closure:
        mode = "closure"
    elif strict_health:
        mode = "health"
    else:
        mode = "okf"
    rules = ["okf-0.1"]
    if profile:
        rules.append(profile)
    if check_closure:
        rules.append("standalone-closure")
    if strict_health:
        rules.append("strict-health")
    payload = {
        "target": _logical_target(root, content_digest, target_id),
        "validated_content_digest": content_digest,
        "effective_policy": policy.report_value(),
        "mode": mode,
        "rules": rules,
        "outcome": "fail" if errors else "pass",
        "counts": {"errors": errors, "warnings": warnings, "infos": infos, "files": len(markdown_paths)},
        "issues": issues,
    }
    return make_artifact("validation-report", payload)


def require_valid_bundle(bundle_root: str | Path, **options: Any) -> dict[str, Any]:
    """Validate and raise a stable validation-blocked error on failure."""

    report = validate_bundle(bundle_root, **options)
    if report["outcome"] != "pass":
        raise CortexError(
            "Bundle validation failed",
            status=Status.VALIDATION_BLOCKED,
            code="bundle_validation_failed",
            details={"report": report["artifact_id"], "errors": report["counts"]["errors"]},
        )
    return report


__all__ = [
    "AUTHORING_PROFILE",
    "IMPORT_PROFILE",
    "NATIVE_PROFILE",
    "discover_cortex_profile",
    "require_valid_bundle",
    "validate_bundle",
]
