"""Hard-coded Cortex 5 profile and record validators."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from .constants import RECORD_FIELDS, RECORD_SCHEMA
from .errors import issue, validation_error


_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))$"
)


def _strict_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True


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
    issues = _exact_keys(value, ("version", "groups"), label=label)
    if type(value.get("version")) is not int or value.get("version") != 2:
        issues.append(issue("invalid_profile_version", "Tag profile version must be integer 2", path=label))
    groups = value.get("groups")
    if not isinstance(groups, list):
        issues.append(issue("invalid_groups", "groups must be an ordered array", path=label))
        return issues
    seen_groups: set[str] = set()
    seen_tags: set[str] = set()
    for group_index, group in enumerate(groups):
        group_label = f"{label}#/groups/{group_index}"
        if not isinstance(group, dict):
            issues.append(issue("invalid_tag_group", "Each group must be an object", path=group_label))
            continue
        issues.extend(_exact_keys(group, ("name", "tags"), label=group_label))
        name = group.get("name")
        if not _strict_text(name) or not name:
            issues.append(issue("invalid_group_name", "Group name must be a nonempty string", path=group_label))
        elif name in seen_groups:
            issues.append(issue("duplicate_group_name", "Group names must be exactly unique", path=group_label, group=name))
        else:
            seen_groups.add(name)
        tags = group.get("tags")
        if not isinstance(tags, list) or not tags:
            issues.append(issue("invalid_group_tags", "Configured groups must contain a nonempty ordered tags array", path=group_label))
            continue
        for tag_index, item in enumerate(tags):
            item_label = f"{group_label}/tags/{tag_index}"
            if not isinstance(item, dict):
                issues.append(issue("invalid_tag", "Each tag entry must be an object", path=item_label))
                continue
            issues.extend(_exact_keys(item, ("tag", "description"), label=item_label))
            tag = item.get("tag")
            if not _strict_text(tag) or not tag:
                issues.append(issue("invalid_tag", "tag must be a nonempty string", path=item_label))
            elif tag in seen_tags:
                issues.append(issue("duplicate_tag", "Tag names must be globally exactly unique", path=item_label, tag=tag))
            else:
                seen_tags.add(tag)
            if not _strict_text(item.get("description")):
                issues.append(issue("invalid_tag_description", "description must be a string", path=item_label))
    return issues


def validate_layout_profile(value: dict[str, Any], *, label: str = "profiles/layout.json") -> list[dict[str, Any]]:
    issues = _exact_keys(
        value,
        (
            "version",
            "partition_by",
            "partition_name_strategy",
            "unit_name_strategy",
            "max_component_length",
            "duplicate_name_strategy",
        ),
        label=label,
    )
    if type(value.get("version")) is not int or value.get("version") != 2:
        issues.append(issue("invalid_profile_version", "Layout profile version must be integer 2", path=label))
    partition_by = value.get("partition_by")
    if partition_by is not None and (not _strict_text(partition_by) or not partition_by):
        issues.append(issue("invalid_partition_group", "partition_by must be null or a nonempty group name", path=label))
    if value.get("partition_name_strategy") != "tag":
        issues.append(issue("invalid_partition_name_strategy", "partition_name_strategy must be tag", path=label))
    if value.get("unit_name_strategy") != "title-slug":
        issues.append(issue("invalid_unit_name_strategy", "unit_name_strategy must be title-slug", path=label))
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
    if not _strict_text(title) or not title.strip():
        issues.append(issue("invalid_title", "title must be a nonempty string", path=label))
    if not valid_rfc3339(value.get("timestamp")):
        issues.append(issue("invalid_timestamp", "timestamp must be timezone-aware RFC3339", path=label))
    tags = value.get("tags")
    if not isinstance(tags, list):
        issues.append(issue("invalid_record_tags", "tags must be an ordered string array", path=label))
    else:
        seen: set[str] = set()
        for index, tag in enumerate(tags):
            if not _strict_text(tag):
                issues.append(issue("invalid_record_tag", "Record tags must be strings", path=f"{label}#/tags/{index}"))
                continue
            if tag in seen:
                issues.append(issue("duplicate_record_tag", "Record tags must not repeat", path=f"{label}#/tags/{index}", tag=tag))
            else:
                seen.add(tag)
            if tag not in registered:
                issues.append(issue("unregistered_tag", "Record tag is not registered", path=f"{label}#/tags/{index}", tag=tag))
    return issues


def tag_groups(value: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {group["name"]: group["tags"] for group in value["groups"]}


def registered_tags(value: dict[str, Any]) -> set[str]:
    return {item["tag"] for group in value["groups"] for item in group["tags"]}


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
    "registered_tags",
    "tag_groups",
]
