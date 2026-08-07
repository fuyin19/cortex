"""Versioned, deterministic naming for project-owned OKF references."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import CortexError, Status


REFERENCE_NAMING_POLICY = "okf-reference-naming-v1"
REFERENCE_TAG_POLICY = "okf-reference-tags-v1"
POSITIVE_18A_TAG = "chapter-18a"
NEGATIVE_18A_TAG = "not-chapter-18a"
LEGACY_NON_18A_TAG = "chapter-8-05"

_PROJECT_TAG = re.compile(r"^project-[a-z0-9][a-z0-9-]*$")
_LEADING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])")
_INVALID_FILENAME = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
_STRUCTURAL_SPACE = re.compile(r"\s*-\s*")
_REPEATED_HYPHEN = re.compile(r"-{2,}")
_TITLE_SEPARATOR = re.compile(r"\s+")


def _policy_error(message: str, code: str, **details: object) -> CortexError:
    return CortexError(message, status=Status.POLICY_BLOCKED, code=code, details=dict(details))


def _string_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Sequence[Any] = [part for part in value.split(",") if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise _policy_error("Reference tags must be a string array", "invalid_reference_tags")
    tags = [unicodedata.normalize("NFC", str(item).strip()) for item in values if str(item).strip()]
    if len(tags) != len(set(tags)):
        raise _policy_error("Reference tags must be unique", "duplicate_reference_tags")
    return tags


def _compact_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value or "").strip()
    match = _ISO_DATE.match(text)
    if match is None:
        return None
    try:
        parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _basename(value: str) -> str:
    text = value.strip().replace("\\", "/")
    parsed = urlparse(text)
    if parsed.scheme == "file":
        text = unquote(parsed.path)
    return PurePosixPath(text).name


def _sanitize_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().strip(".")
    normalized = _INVALID_FILENAME.sub("-", normalized)
    normalized = _STRUCTURAL_SPACE.sub("-", normalized)
    normalized = _REPEATED_HYPHEN.sub("-", normalized).strip("- ").rstrip(".")
    if not normalized:
        raise _policy_error("Reference filename stem is empty", "empty_reference_stem")
    return normalized


def _legacy_stem(source_path: str, project_tag: str) -> str:
    stem = PurePosixPath(source_path).stem
    stem = _LEADING_DATE.sub("", stem)
    prefix = project_tag + "-"
    if stem.casefold().startswith(prefix.casefold()):
        stem = stem[len(prefix) :]
    tokens = stem.split("-")
    retained: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].casefold()
        if token == "post" and index + 1 < len(tokens) and tokens[index + 1].casefold() == "a1":
            index += 2
            continue
        if token == "a1":
            index += 1
            continue
        retained.append(tokens[index])
        index += 1
    return _sanitize_stem("-".join(retained))


def normalized_filename_key(filename: str) -> str:
    """Return a cross-platform collision key; confusables remain review-only."""

    return unicodedata.normalize("NFKC", filename).rstrip(" .").casefold()


def normalize_script_title(value: str) -> str:
    """Normalize an approved draft title without inventing semantic text."""

    if not isinstance(value, str):
        raise _policy_error("Draft title must be a string", "invalid_draft_title")
    normalized = unicodedata.normalize("NFC", value).strip()
    normalized = "".join(
        character.lower() if "LATIN" in unicodedata.name(character, "") else character
        for character in normalized
    )
    normalized = _INVALID_FILENAME.sub("-", normalized)
    normalized = _TITLE_SEPARATOR.sub("-", normalized)
    normalized = _REPEATED_HYPHEN.sub("-", normalized).strip("-. ")
    if not normalized:
        raise _policy_error("Draft title is empty after normalization", "empty_draft_title")
    return normalized


def script_reference_filename(identifier_tag: str, title: str, timestamp: Any) -> str:
    """Return ``<identifier>-<normalized title>-<YYYYMMDD>.md`` exactly."""

    identifier = unicodedata.normalize("NFC", str(identifier_tag)).strip()
    if not _PROJECT_TAG.fullmatch(identifier):
        raise _policy_error("Reference identifier tag is invalid", "invalid_identifier_tag", tag=identifier)
    compact = _compact_date(timestamp)
    if compact is None:
        raise _policy_error("Reference timestamp is invalid", "invalid_reference_timestamp")
    return f"{identifier}-{normalize_script_title(title)}-{compact}.md"


def project_reference_policy_requested(metadata: Mapping[str, Any]) -> bool:
    """Return whether metadata explicitly opts into the project policy."""

    if metadata.get("naming_policy") == REFERENCE_NAMING_POLICY or metadata.get("tag_policy") == REFERENCE_TAG_POLICY:
        return True
    try:
        tags = _string_tags(metadata.get("tags"))
    except CortexError:
        return False
    has_project = any(tag.startswith("project-") for tag in tags)
    has_rule = any(tag in {POSITIVE_18A_TAG, NEGATIVE_18A_TAG, LEGACY_NON_18A_TAG} for tag in tags)
    return has_project and has_rule


@dataclass(frozen=True, slots=True)
class ReferenceIdentity:
    project_tag: str
    listing_rule_tag: str
    original_stem: str
    date_compact: str
    filename: str
    naming_source: str
    original_filename: str | None
    legacy_filename: str | None


def project_reference_identity(
    metadata: Mapping[str, Any],
    source_path: str,
    *,
    captured_source_name: str | None = None,
) -> ReferenceIdentity | None:
    """Return v1 identity for a project reference, or ``None`` for generic OKF."""

    tags = _string_tags(metadata.get("tags"))
    project_tags = [tag for tag in tags if tag.startswith("project-")]
    if not project_tags:
        return None
    if len(project_tags) != 1 or not _PROJECT_TAG.fullmatch(project_tags[0]):
        raise _policy_error(
            "Project references require exactly one valid project-* tag",
            "invalid_project_tag",
            path=source_path,
            project_tags=project_tags,
        )
    has_positive = POSITIVE_18A_TAG in tags
    has_legacy_negative = LEGACY_NON_18A_TAG in tags
    has_explicit_negative = NEGATIVE_18A_TAG in tags
    if has_positive and (has_legacy_negative or has_explicit_negative):
        raise _policy_error("18A status is contradictory", "ambiguous_18a_status", path=source_path)
    if not has_positive and not (has_legacy_negative or has_explicit_negative):
        raise _policy_error("Project reference has no explicit 18A status", "missing_18a_status", path=source_path)
    listing_rule_tag = POSITIVE_18A_TAG if has_positive else NEGATIVE_18A_TAG

    date_compact = _compact_date(metadata.get("created")) or _compact_date(metadata.get("converted_at"))
    if date_compact is None:
        raise _policy_error(
            "Project reference requires a valid created or converted_at date",
            "missing_reference_date",
            path=source_path,
        )

    original_value = str(metadata.get("original_filename") or "").strip()
    source_value = str(metadata.get("source") or "").strip()
    actual_name = _basename(original_value or source_value or captured_source_name or "")
    if actual_name:
        original_stem = _sanitize_stem(PurePosixPath(actual_name).stem)
        naming_source = "source"
        original_filename = actual_name
        legacy_filename = None
    else:
        recorded_legacy = str(metadata.get("legacy_filename") or "").strip()
        legacy_name = recorded_legacy or PurePosixPath(source_path).name
        original_stem = _legacy_stem(legacy_name, project_tags[0])
        naming_source = "legacy-filename"
        original_filename = None
        legacy_filename = legacy_name

    filename = f"{project_tags[0]}-{original_stem}-{date_compact}.md"
    return ReferenceIdentity(
        project_tag=project_tags[0],
        listing_rule_tag=listing_rule_tag,
        original_stem=original_stem,
        date_compact=date_compact,
        filename=filename,
        naming_source=naming_source,
        original_filename=original_filename,
        legacy_filename=legacy_filename,
    )


def apply_project_reference_policy(metadata: MutableMapping[str, Any], identity: ReferenceIdentity) -> None:
    metadata["type"] = "reference"
    metadata["tags"] = [identity.project_tag, identity.listing_rule_tag]
    metadata["naming_policy"] = REFERENCE_NAMING_POLICY
    metadata["tag_policy"] = REFERENCE_TAG_POLICY
    metadata["naming_source"] = identity.naming_source
    if metadata.get("timestamp") in (None, ""):
        metadata["timestamp"] = metadata.get("updated") or metadata.get("created") or metadata.get("converted_at")
    if identity.original_filename is not None:
        metadata["original_filename"] = identity.original_filename
        metadata.pop("legacy_filename", None)
    else:
        metadata["legacy_filename"] = identity.legacy_filename
        metadata.pop("original_filename", None)


def validate_project_reference(metadata: Mapping[str, Any], filename: str) -> list[str]:
    """Return stable policy issue codes for one opted-in project reference."""

    try:
        if not project_reference_policy_requested(metadata):
            return []
        identity = project_reference_identity(metadata, filename)
    except CortexError as exc:
        return [exc.code]
    assert identity is not None
    issues: list[str] = []
    if filename != identity.filename:
        issues.append("invalid_reference_filename")
    if list(_string_tags(metadata.get("tags"))) != [identity.project_tag, identity.listing_rule_tag]:
        issues.append("invalid_reference_tags")
    if metadata.get("naming_policy") != REFERENCE_NAMING_POLICY:
        issues.append("invalid_naming_policy")
    if metadata.get("tag_policy") != REFERENCE_TAG_POLICY:
        issues.append("invalid_tag_policy")
    return issues


__all__ = [
    "LEGACY_NON_18A_TAG",
    "NEGATIVE_18A_TAG",
    "POSITIVE_18A_TAG",
    "REFERENCE_NAMING_POLICY",
    "REFERENCE_TAG_POLICY",
    "ReferenceIdentity",
    "apply_project_reference_policy",
    "normalized_filename_key",
    "normalize_script_title",
    "project_reference_policy_requested",
    "project_reference_identity",
    "validate_project_reference",
    "script_reference_filename",
]
