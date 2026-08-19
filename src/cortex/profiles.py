"""Hard-coded Cortex 5 profile and record validators."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from .constants import RECORD_FIELDS, RECORD_SCHEMA
from .errors import issue, validation_error
from .native import component_problem


_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))$"
)


def _exact_keys(value: dict[str, Any], expected: Iterable[str], *, label: str) -> list[dict[str, Any]]:
    wanted = set(expected)
    actual = set(value)
    issues: list[dict[str, Any]] = []
    for key in sorted(wanted - actual):
        issues.append(issue("missing_field", f"Missing required field: {key}", path=label, field=key))
    for key in sorted(actual - wanted):
        issues.append(issue("unknown_field", f"Unknown field: {key}", path=label, field=key))
    return issues


def validate_record_schema(value: dict[str, Any], *, label: str = "profiles/record-schema.json") -> list[dict[str, Any]]:
    if value != RECORD_SCHEMA:
        return [issue("invalid_record_schema", "record-schema.json must equal the fixed Cortex 5 profile", path=label)]
    return []


def validate_tags_profile(value: dict[str, Any], *, label: str = "profiles/tags.json") -> list[dict[str, Any]]:
    issues = _exact_keys(value, ("version", "tags"), label=label)
    if type(value.get("version")) is not int or value.get("version") != 1:
        issues.append(issue("invalid_profile_version", "Tag profile version must be integer 1", path=label))
    tags = value.get("tags")
    if not isinstance(tags, list):
        issues.append(issue("invalid_tags", "tags must be an ordered array", path=label))
        return issues
    seen: set[str] = set()
    for index, item in enumerate(tags):
        item_label = f"{label}#/tags/{index}"
        if not isinstance(item, dict):
            issues.append(issue("invalid_tag", "Each tag entry must be an object", path=item_label))
            continue
        issues.extend(_exact_keys(item, ("tag", "description"), label=item_label))
        tag = item.get("tag")
        if not isinstance(tag, str) or not tag:
            issues.append(issue("invalid_tag", "tag must be a nonempty string", path=item_label))
        elif tag in seen:
            issues.append(issue("duplicate_tag", "Tag names must be exactly unique", path=item_label, tag=tag))
        else:
            seen.add(tag)
        if not isinstance(item.get("description"), str):
            issues.append(issue("invalid_tag_description", "description must be a string", path=item_label))
    return issues


def validate_layout_profile(value: dict[str, Any], *, label: str = "profiles/layout.json") -> list[dict[str, Any]]:
    issues = _exact_keys(
        value,
        ("version", "records_root", "folder_name_strategy", "max_component_length", "duplicate_name_strategy"),
        label=label,
    )
    if type(value.get("version")) is not int or value.get("version") != 1:
        issues.append(issue("invalid_profile_version", "Layout profile version must be integer 1", path=label))
    root = value.get("records_root")
    if not isinstance(root, str):
        issues.append(issue("invalid_records_root", "records_root must be one path component", path=label))
    else:
        problem = component_problem(root, allow_profiles=False)
        if problem is not None:
            issues.append(issue(problem[0], problem[1], path=f"{label}#/records_root"))
    if value.get("folder_name_strategy") != "title-slug":
        issues.append(issue("invalid_folder_name_strategy", "folder_name_strategy must be title-slug", path=label))
    maximum = value.get("max_component_length")
    if type(maximum) is not int or not 16 <= maximum <= 200:
        issues.append(issue("invalid_component_limit", "max_component_length must be an integer from 16 through 200", path=label))
    if value.get("duplicate_name_strategy") not in {"numeric-suffix", "reject"}:
        issues.append(issue("invalid_duplicate_strategy", "duplicate_name_strategy must be numeric-suffix or reject", path=label))
    return issues


def valid_rfc3339(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = _RFC3339.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(match.group(index)) for index in range(1, 7))
    try:
        date(year, month, day)
    except ValueError:
        return False
    if hour > 23 or minute > 59 or second > 59:
        return False
    if match.group(7) is not None:
        offset_hour = int(match.group(8))
        offset_minute = int(match.group(9))
        if offset_hour > 23 or offset_minute > 59:
            return False
    return True


def validate_record(value: dict[str, Any], registered: set[str], *, label: str) -> list[dict[str, Any]]:
    issues = _exact_keys(value, RECORD_FIELDS, label=label)
    title = value.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append(issue("invalid_title", "title must be a nonempty string", path=label))
    if not valid_rfc3339(value.get("timestamp")):
        issues.append(issue("invalid_timestamp", "timestamp must be timezone-aware RFC3339", path=label))
    tags = value.get("tags")
    if not isinstance(tags, list):
        issues.append(issue("invalid_record_tags", "tags must be an ordered string array", path=label))
    else:
        seen: set[str] = set()
        for index, tag in enumerate(tags):
            if not isinstance(tag, str):
                issues.append(issue("invalid_record_tag", "Record tags must be strings", path=f"{label}#/tags/{index}"))
                continue
            if tag in seen:
                issues.append(issue("duplicate_record_tag", "Record tags must not repeat", path=f"{label}#/tags/{index}", tag=tag))
            else:
                seen.add(tag)
            if tag not in registered:
                issues.append(issue("unregistered_tag", "Record tag is not registered", path=f"{label}#/tags/{index}", tag=tag))
    return issues


def require_valid_profile(profile: str, value: dict[str, Any]) -> None:
    problems = validate_tags_profile(value) if profile == "tags" else validate_layout_profile(value)
    if problems:
        first = problems[0]
        raise validation_error(first["message"], first["code"], path=first.get("path"), issues=problems)


__all__ = [
    "require_valid_profile",
    "valid_rfc3339",
    "validate_layout_profile",
    "validate_record",
    "validate_record_schema",
    "validate_tags_profile",
]
