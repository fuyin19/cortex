"""Deterministic record-folder naming."""

from __future__ import annotations

import sys
import unicodedata

from .errors import validation_error
from .native import is_windows_device_name, require_safe_component


_WINDOWS_FORBIDDEN = set('<>:"/\\|?*')
REQUIRED_UNIDATA_VERSION = "14.0.0"


def require_naming_runtime() -> None:
    if sys.version_info[:2] != (3, 11):
        raise validation_error(
            "Layout 5 naming requires Python 3.11",
            "unsupported_python_version",
            required="3.11",
            actual=f"{sys.version_info[0]}.{sys.version_info[1]}",
        )
    if unicodedata.unidata_version != REQUIRED_UNIDATA_VERSION:
        raise validation_error(
            "Layout 5 naming requires Unicode database 14.0.0",
            "unsupported_unicode_database",
            required=REQUIRED_UNIDATA_VERSION,
            actual=unicodedata.unidata_version,
        )


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
    """Normalize title text for the tag-title-date strategy."""

    require_naming_runtime()
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


def tag_title_date_name(tag: str, title: str, timestamp: str, maximum: int) -> str:
    """Build the normative Layout 5 unit name without altering the selected tag."""

    date_stamp = timestamp[:10].replace("-", "")
    fixed = len(tag.encode("utf-8")) + 2 + len(date_stamp.encode("ascii"))
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
    folder = f"{tag}-{middle}-{date_stamp}"
    if len(folder.encode("utf-8")) > maximum:
        raise validation_error(
            "Record-folder name exceeds max_component_length",
            "insufficient_unit_name_capacity",
        )
    require_safe_component(folder, allow_profiles=False, label=folder)
    return folder


def suffixed_name(base: str, number: int, maximum: int) -> str:
    suffix = f"-{number}"
    stem = truncate_utf8(base, maximum - len(suffix.encode("utf-8"))).strip(". -")
    if not stem:
        raise validation_error("Folder suffix leaves no room for the title slug", "folder_name_too_short")
    return stem + suffix
