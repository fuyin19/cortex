"""OKF 0.1 document parsing and portable link resolution."""

from __future__ import annotations

import io
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .canonical import _native_path
from .errors import CortexError, Status
from .paths import normalize_relative_path, safe_join, validate_concept_id


SUPPORTED_OKF_VERSION = "0.1"
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")


def _okf_error(message: str, code: str, **details: object) -> CortexError:
    return CortexError(message, status=Status.VALIDATION_BLOCKED, code=code, details=dict(details))


@dataclass(slots=True)
class ConceptDocument:
    """One parsed non-reserved OKF concept."""

    path: Path
    concept_id: str
    frontmatter: CommentedMap
    body: str
    raw_text: str


def _round_trip_yaml() -> YAML:
    yaml = YAML(typ="rt", pure=True)
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False
    yaml.width = 4096
    return yaml


def split_frontmatter(text: str) -> tuple[CommentedMap, str]:
    """Parse a leading YAML block while retaining its round-trip AST."""

    if text.startswith("\ufeff"):
        raise _okf_error("UTF-8 BOM is forbidden in OKF documents", "utf8_bom")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise _okf_error("Concept is missing a leading YAML frontmatter block", "missing_frontmatter")
    yaml = _round_trip_yaml()
    try:
        loaded = yaml.load(match.group("yaml")) or CommentedMap()
    except Exception as exc:
        raise _okf_error("Concept frontmatter is not parseable YAML", "invalid_frontmatter") from exc
    if not isinstance(loaded, Mapping):
        raise _okf_error("Concept frontmatter must be a mapping", "invalid_frontmatter")
    if not isinstance(loaded, CommentedMap):
        loaded = CommentedMap(loaded)
    return loaded, text[match.end() :]


def parse_concept(
    path: str | Path,
    *,
    bundle_root: str | Path | None = None,
) -> ConceptDocument:
    """Read one UTF-8 concept and derive its normalized bundle-relative ID."""

    document_path = Path(path)
    if document_path.name.casefold() in RESERVED_FILENAMES:
        raise _okf_error("Reserved documents are not concepts", "reserved_document", path=str(document_path))
    try:
        with open(_native_path(document_path), "rb") as stream:
            raw = stream.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise _okf_error("Concept cannot be read as UTF-8", "invalid_utf8", path=str(document_path)) from exc
    root = Path(bundle_root).resolve() if bundle_root is not None else document_path.parent.resolve()
    try:
        relative = document_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise _okf_error("Concept lies outside its bundle", "concept_path_escape", path=str(document_path)) from exc
    if not relative.endswith(".md"):
        raise _okf_error("Concept must use the .md suffix", "invalid_concept_suffix", path=relative)
    concept_id = validate_concept_id(relative[:-3])
    frontmatter, body = split_frontmatter(text)
    concept_type = frontmatter.get("type")
    if not isinstance(concept_type, str) or not concept_type.strip():
        raise _okf_error("Concept type must be a non-empty string", "missing_type", path=relative)
    return ConceptDocument(
        path=document_path,
        concept_id=concept_id,
        frontmatter=frontmatter,
        body=body,
        raw_text=text,
    )


def render_concept(document: ConceptDocument) -> str:
    """Render a concept while preserving unknown YAML and round-trip metadata."""

    yaml = _round_trip_yaml()
    stream = io.StringIO()
    yaml.dump(document.frontmatter, stream)
    frontmatter = stream.getvalue()
    if frontmatter.endswith("...\n"):
        frontmatter = frontmatter[:-4]
    if not frontmatter.endswith("\n"):
        frontmatter += "\n"
    return f"---\n{frontmatter}---\n{document.body}"


def iter_concepts(bundle_root: str | Path) -> Iterator[ConceptDocument]:
    """Yield all concepts in deterministic UTF-8 path order."""

    root = Path(bundle_root).resolve()
    paths = [
        path
        for path in root.rglob("*.md")
        if path.name.casefold() not in RESERVED_FILENAMES
    ]
    paths.sort(key=lambda item: item.relative_to(root).as_posix().encode("utf-8"))
    for path in paths:
        yield parse_concept(path, bundle_root=root)


def parse_root_index(bundle_root: str | Path) -> tuple[dict[str, Any] | None, str]:
    """Return optional root-index frontmatter and the remaining Markdown."""

    path = Path(bundle_root) / "index.md"
    if not path.exists():
        return None, ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _okf_error("Root index cannot be read as UTF-8", "invalid_utf8", path="index.md") from exc
    match = _FRONTMATTER.match(text)
    if match is None:
        return None, text
    yaml = _round_trip_yaml()
    try:
        value = yaml.load(match.group("yaml")) or {}
    except Exception as exc:
        raise _okf_error("Root index frontmatter is invalid", "invalid_index_frontmatter", path="index.md") from exc
    if not isinstance(value, Mapping):
        raise _okf_error("Root index frontmatter must be a mapping", "invalid_index_frontmatter", path="index.md")
    return dict(value), text[match.end() :]


def discover_bundle_version(bundle_root: str | Path) -> str | None:
    """Read the optional OKF version declaration from root index.md."""

    frontmatter, _ = parse_root_index(bundle_root)
    if frontmatter is None:
        return None
    version = frontmatter.get("okf_version")
    if version is None:
        return None
    return str(version)


def extract_markdown_links(text: str) -> tuple[str, ...]:
    """Extract Markdown destinations in source order."""

    destinations: list[str] = []
    for match in _MARKDOWN_LINK.finditer(text):
        value = match.group("target").strip()
        if value.startswith("<") and ">" in value:
            value = value[1 : value.index(">")]
        else:
            value = value.split(maxsplit=1)[0]
        destinations.append(value)
    return tuple(destinations)


def _resolve_relative(current: PurePosixPath, destination: str) -> str:
    if destination.startswith("/"):
        parts: list[str] = []
        incoming = destination.lstrip("/").split("/")
    else:
        parts = list(current.parent.parts)
        incoming = destination.split("/")
    for part in incoming:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise _okf_error("Link escapes the bundle root", "link_escape", target=destination)
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise _okf_error("Link has no bundle-relative target", "invalid_link", target=destination)
    return normalize_relative_path("/".join(parts))


def resolve_internal_link(
    bundle_root: str | Path,
    source_relative: str,
    destination: str,
) -> Path | None:
    """Resolve an internal link safely; return None for external/fragment links."""

    if not destination or destination.startswith("#"):
        return None
    once = unquote(destination)
    if unquote(once) != once:
        raise _okf_error("Double percent-encoded link is forbidden", "double_percent_decode", target=destination)
    parsed = urlsplit(once)
    if parsed.scheme or parsed.netloc:
        return None
    target_text = parsed.path
    if not target_text:
        return None
    normalized = _resolve_relative(PurePosixPath(normalize_relative_path(source_relative)), target_text)
    target = safe_join(bundle_root, normalized)
    native_target = _native_path(target)
    if os.path.exists(native_target) and os.path.isdir(native_target):
        return target / "index.md"
    if not target.suffix and not os.path.exists(native_target):
        markdown = target.with_suffix(".md")
        if os.path.exists(_native_path(markdown)):
            return markdown
    return target


__all__ = [
    "ConceptDocument",
    "RESERVED_FILENAMES",
    "SUPPORTED_OKF_VERSION",
    "discover_bundle_version",
    "extract_markdown_links",
    "iter_concepts",
    "parse_concept",
    "parse_root_index",
    "render_concept",
    "resolve_internal_link",
    "split_frontmatter",
]
