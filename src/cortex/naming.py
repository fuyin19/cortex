"""Deterministic record-folder naming."""

from __future__ import annotations

import unicodedata

from .errors import validation_error
from .native import is_windows_device_name


_WINDOWS_FORBIDDEN = set('<>:"/\\|?*')


def truncate_utf8(value: str, maximum: int) -> str:
    used = 0
    output: list[str] = []
    for character in value:
        width = len(character.encode("utf-8"))
        if used + width > maximum:
            break
        output.append(character)
        used += width
    return "".join(output)


def title_slug(title: str, maximum: int) -> str:
    value = unicodedata.normalize("NFC", title).strip().lower()
    output: list[str] = []
    hyphen = False
    for character in value:
        replace = character.isspace() or unicodedata.category(character) == "Cc" or character in _WINDOWS_FORBIDDEN
        if replace or character == "-":
            if output and not hyphen:
                output.append("-")
                hyphen = True
            continue
        output.append(character)
        hyphen = False
    slug = "".join(output).strip(". -")
    if not slug:
        raise validation_error("Title does not produce a record-folder name", "empty_title_slug")
    if is_windows_device_name(slug):
        slug = "_" + slug
    slug = truncate_utf8(slug, maximum).strip(". -")
    if not slug:
        raise validation_error("Title does not fit the folder component limit", "empty_title_slug")
    return slug


def semantic_title(title: str) -> str:
    """Normalize title text for the partition-title-date strategy."""

    value = unicodedata.normalize("NFC", title).strip().lower()
    output: list[str] = []
    hyphen = False
    for character in value:
        replace = character.isspace() or unicodedata.category(character) == "Cc" or character in _WINDOWS_FORBIDDEN
        if replace or character == "-":
            if output and not hyphen:
                output.append("-")
                hyphen = True
            continue
        output.append(character)
        hyphen = False
    normalized = "".join(output).strip(". -")
    if not normalized:
        raise validation_error("Title does not produce a record-folder name", "empty_title_slug")
    return normalized


def partition_title_date_name(partition: str, title: str, timestamp: str, maximum: int) -> str:
    """Build the opt-in composite unit name without altering the partition tag."""

    date_stamp = timestamp[:10].replace("-", "")
    fixed = len(partition.encode("utf-8")) + 2 + len(date_stamp.encode("ascii"))
    available = maximum - fixed
    if available < 1:
        raise validation_error(
            "Layout component limit leaves no room for a semantic title",
            "insufficient_unit_name_capacity",
        )
    middle = truncate_utf8(semantic_title(title), available).strip(". -")
    if not middle:
        raise validation_error(
            "Layout component limit cannot retain a semantic title codepoint",
            "insufficient_unit_name_capacity",
        )
    folder = f"{partition}-{middle}-{date_stamp}"
    if len(folder.encode("utf-8")) > maximum:
        raise validation_error(
            "Record-folder name exceeds max_component_length",
            "insufficient_unit_name_capacity",
        )
    return folder


def suffixed_name(base: str, number: int, maximum: int) -> str:
    suffix = f"-{number}"
    stem = truncate_utf8(base, maximum - len(suffix.encode("utf-8"))).strip(". -")
    if not stem:
        raise validation_error("Folder suffix leaves no room for the title slug", "folder_name_too_short")
    return stem + suffix
