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


def suffixed_name(base: str, number: int, maximum: int) -> str:
    suffix = f"-{number}"
    stem = truncate_utf8(base, maximum - len(suffix.encode("utf-8"))).strip(". -")
    if not stem:
        raise validation_error("Folder suffix leaves no room for the title slug", "folder_name_too_short")
    return stem + suffix
