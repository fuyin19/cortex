"""Cortex-local implementation of the exact base knowledge-unit envelope.

The navigation resource bytes are deliberately vendored, not imported from a
converter repository.  Cross-repository compatibility is a conformance
contract, while each product retains its own ownership and release boundary.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
import os
from pathlib import Path
from typing import Iterable

from .errors import CortexError, validation_error
from .native import copy_regular, inspect_conversion, native_path


CONTRACT = "knowledge-unit-navigation/v1"
AGENTS_SHA256 = "2067837a839ba3a9a452504a1f85bcff738eb7a181a77458105a8096a33f1bcc"
CLAUDE_SHA256 = "336cc4fbf19beaada7ccf9986414fa91851a8d7a07dfb3ccbe800a69eed0ab49"
_RESOURCE_ROOT = files("cortex").joinpath("resources", "knowledge-unit")
_GUIDES = {"AGENTS.md", "CLAUDE.md"}
_CONTROL_FILES = {
    "agents.md",
    "agents.override.md",
    "claude.md",
    "claude.local.md",
    ".cursorrules",
    ".mcp.json",
}
_CONTROL_DIRECTORIES = {".claude", ".cursor"}


def _resource(name: str, digest: str, length: int) -> bytes:
    payload = (_RESOURCE_ROOT / name).read_bytes()
    if len(payload) != length or hashlib.sha256(payload).hexdigest() != digest:
        raise validation_error(
            "Vendored navigation resource does not match its contract",
            "navigation_resource_mismatch",
            path=name,
            contract=CONTRACT,
        )
    return payload


def navigation_bytes() -> tuple[bytes, bytes]:
    return _resource("AGENTS.md", AGENTS_SHA256, 1695), _resource("CLAUDE.md", CLAUDE_SHA256, 11)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raise(message: str, code: str, path: str | None = None, **details: object) -> None:
    raise validation_error(message, code, path=path, **details)


def _entry_maps(entries: Iterable[tuple[str, Path, bool]]) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    for relative, path, is_directory in entries:
        parts = relative.split("/")
        for index, component in enumerate(parts):
            folded = component.casefold()
            label = "/".join(parts[: index + 1])
            if folded in _CONTROL_DIRECTORIES:
                _raise("Instruction-control directory is forbidden", "instruction_control_path", label)
            if folded in _CONTROL_FILES:
                if not (len(parts) == 1 and component in _GUIDES):
                    _raise("Instruction-control file is forbidden", "instruction_control_path", label)
        if is_directory:
            directories.add(relative)
        else:
            files[relative] = path
    return files, directories


def _validate_assets(files: dict[str, Path], directories: set[str], *, complete: bool) -> None:
    if "assets" not in directories:
        if complete:
            _raise("assets/ is required", "missing_assets", "assets")
        return
    descendants = [name for name in (*files, *directories) if name.startswith("assets/")]
    if not descendants:
        if complete:
            _raise("Semantically empty assets/ must contain zero-byte .keep", "missing_empty_marker", "assets/.keep")
        return
    if "assets/.keep" in files:
        if len(descendants) != 1 or files["assets/.keep"].stat().st_size != 0:
            _raise("assets/.keep is valid only as the sole zero-byte descendant", "invalid_empty_marker", "assets/.keep")
        return
    for directory in sorted(name for name in directories if name.startswith("assets/")):
        prefix = directory + "/"
        if not any(name.startswith(prefix) for name in (*files, *directories)):
            _raise("Empty nested asset directories are forbidden", "empty_nested_directory", directory)


def _validate_src(files: dict[str, Path], directories: set[str], *, complete: bool) -> Path | None:
    if "src" not in directories:
        if complete:
            _raise("src/ is required", "missing_src", "src")
        return None
    nested_dirs = [name for name in directories if name.startswith("src/")]
    nested_files = [name for name in files if name.startswith("src/") and name.count("/") > 1]
    if nested_dirs or nested_files:
        _raise("src/ permits one direct ordinary file only", "invalid_source_directory", "src")
    direct = [name for name in files if name.startswith("src/") and name.count("/") == 1]
    if not direct:
        if complete:
            _raise("Semantically empty src/ must contain zero-byte .keep", "missing_empty_marker", "src/.keep")
        return None
    if len(direct) != 1:
        _raise("src/ permits at most one source file", "invalid_source_directory", "src")
    relative = direct[0]
    if Path(relative).name == ".keep":
        if files[relative].stat().st_size != 0:
            _raise("src/.keep must be zero-byte", "invalid_empty_marker", relative)
        return None
    return files[relative]


def validate_representation_names(names: list[str]) -> str:
    if not names:
        _raise("At least one root representation file is required", "missing_representation")
    folded_names: dict[str, str] = {}
    for name in names:
        key = name.casefold()
        if key in folded_names:
            _raise(
                "Representation names collide under case folding",
                "representation_name_collision",
                name,
                names=[folded_names[key], name],
            )
        folded_names[key] = name
    stems = {Path(name).stem for name in names}
    if len(stems) != 1 or any(Path(name).suffix == "" for name in names):
        _raise(
            "Root representations must share one exact stem and have extensions",
            "representation_stem_mismatch",
        )
    suffixes: dict[str, str] = {}
    for name in names:
        suffix = Path(name).suffix
        key = suffix.casefold()
        if key in suffixes:
            _raise(
                "Representation extensions collide under case folding",
                "representation_extension_collision",
                name,
                extensions=[suffixes[key], suffix],
            )
        suffixes[key] = suffix
    return next(iter(stems))


def validate_entries(
    entries: list[tuple[str, Path, bool]],
    *,
    complete: bool,
    cortex_record: bool,
) -> tuple[str, Path | None]:
    """Validate an already no-follow-inventoried tree and return stem/src payload."""
    files, directories = _entry_maps(entries)
    root_files = {name: path for name, path in files.items() if "/" not in name}
    root_dirs = {name for name in directories if "/" not in name}
    unknown_dirs = root_dirs - {"assets", "src"}
    if unknown_dirs:
        _raise("Unexpected root directory", "unexpected_record_entry", sorted(unknown_dirs)[0])
    record_matches = [name for name in root_files if name.casefold() == "record.json"]
    if cortex_record:
        if record_matches != ["record.json"]:
            _raise("Cortex record metadata must be exact root record.json", "missing_record_entry", "record.json")
    elif record_matches:
        _raise(
            "Converter payload must not contain reserved Cortex record.json",
            "reserved_record_metadata",
            record_matches[0],
        )
    agents, claude = navigation_bytes()
    for name, expected in (("AGENTS.md", agents), ("CLAUDE.md", claude)):
        path = root_files.get(name)
        if path is None:
            if complete:
                _raise("Navigation guide is required", "missing_navigation_guide", name)
        elif path.read_bytes() != expected:
            _raise("Navigation guide bytes do not match the contract", "navigation_guide_mismatch", name)
    representations = [
        name for name in root_files
        if name not in _GUIDES and name != "record.json"
    ]
    stem = validate_representation_names(representations)
    _validate_assets(files, directories, complete=complete)
    source = _validate_src(files, directories, complete=complete)
    return stem, source


def inspect_input(path: Path) -> tuple[list[tuple[str, Path, bool]], str, Path | None]:
    kind, entries = inspect_conversion(path)
    if kind != "directory":
        _raise("--conversion must name a real directory", "conversion_directory_required", str(path))
    stem, source = validate_entries(entries, complete=False, cortex_record=False)
    return entries, stem, source


def validate_complete_directory(path: Path, *, cortex_record: bool = True) -> str:
    kind, entries = inspect_conversion(path)
    if kind != "directory":
        _raise("Knowledge-unit root must be a real directory", "real_directory_required", str(path))
    stem, _ = validate_entries(entries, complete=True, cortex_record=cortex_record)
    return stem


def _write_exact(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            _raise("Navigation guide bytes do not match the contract", "navigation_guide_mismatch", path.name)
        return
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()


def finalize_staged(path: Path, *, source: Path | None) -> str:
    """Fill only missing envelope state in one newly owned Cortex stage."""
    agents, claude = navigation_bytes()
    _write_exact(path / "AGENTS.md", agents)
    _write_exact(path / "CLAUDE.md", claude)
    assets = path / "assets"
    assets.mkdir(exist_ok=True)
    if not any(assets.iterdir()):
        (assets / ".keep").write_bytes(b"")
    src = path / "src"
    src.mkdir(exist_ok=True)
    children = list(src.iterdir())
    if source is not None:
        if not children:
            copy_regular(source, src / source.name)
        elif len(children) == 1 and children[0].name == ".keep":
            if children[0].stat().st_size != 0:
                _raise("src/.keep must be zero-byte", "invalid_empty_marker", "src/.keep")
            children[0].unlink()
            copy_regular(source, src / source.name)
        elif len(children) == 1 and children[0].is_file():
            retained = children[0]
            if retained.name != source.name or _sha256(retained) != _sha256(source):
                _raise(
                    "--source must equal retained conversion source by basename and SHA-256",
                    "conversion_source_mismatch",
                    f"src/{retained.name}",
                )
        else:
            _raise("src/ permits at most one source file", "invalid_source_directory", "src")
    elif not children:
        (src / ".keep").write_bytes(b"")
    return validate_complete_directory(path, cortex_record=True)


__all__ = [
    "AGENTS_SHA256",
    "CLAUDE_SHA256",
    "CONTRACT",
    "finalize_staged",
    "inspect_input",
    "navigation_bytes",
    "validate_complete_directory",
    "validate_entries",
    "validate_representation_names",
]
