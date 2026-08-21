"""Normative no-follow Cortex unit-tree inventory and digest."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import io_error, validation_error
from .native import is_reparse_metadata, native_path, require_safe_component

_DOMAIN = b"CORTEX_UNIT_TREE_V2\0"
_U64 = (1 << 64) - 1


@dataclass(frozen=True)
class ManifestEntry:
    relative: str
    kind: str
    size: int | None = None


@dataclass(frozen=True)
class TreeInventory:
    sha256: str
    manifest: tuple[ManifestEntry, ...]


def _encoded(value: str) -> bytes:
    try:
        result = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise validation_error("Tree path is not strict UTF-8", "invalid_tree_utf8", path=value) from exc
    if len(result) > _U64:
        raise validation_error("Tree path exceeds u64 length", "tree_value_too_large", path=value)
    return result


def _u64(value: int) -> bytes:
    if type(value) is not int or value < 0 or value > _U64:
        raise validation_error("Tree size exceeds u64", "tree_value_too_large")
    return value.to_bytes(8, "big")


def _scan(root: Path) -> tuple[ManifestEntry, ...]:
    entries: list[ManifestEntry] = []
    seen: set[bytes] = set()
    def visit(directory: Path, prefix: str = "") -> None:
        try:
            children = list(os.scandir(native_path(directory)))
        except OSError as exc:
            raise io_error("Unit tree could not be inventoried", "tree_unreadable", path=prefix or ".", os_error=str(exc)) from exc
        keyed: list[tuple[bytes, os.DirEntry[str], str]] = []
        for child in children:
            rel = f"{prefix}/{child.name}" if prefix else child.name
            require_safe_component(child.name, label=rel)
            raw = _encoded(rel)
            if raw in seen:
                raise validation_error("Tree contains duplicate strict UTF-8 path bytes", "duplicate_tree_path", path=rel)
            seen.add(raw)
            keyed.append((raw, child, rel))
        for _raw, child, rel in sorted(keyed, key=lambda item: item[0]):
            try:
                meta = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise io_error("Unit entry could not be inspected", "tree_unreadable", path=rel, os_error=str(exc)) from exc
            if is_reparse_metadata(meta):
                raise validation_error("Links and reparse points are forbidden", "reparse_path", path=rel)
            if stat.S_ISDIR(meta.st_mode):
                entries.append(ManifestEntry(rel, "directory"))
                visit(Path(child.path), rel)
            elif stat.S_ISREG(meta.st_mode):
                entries.append(ManifestEntry(rel, "file", meta.st_size))
            else:
                raise validation_error("Unit contains a nonregular entry", "nonregular_entry", path=rel)
    visit(root)
    return tuple(sorted(entries, key=lambda item: _encoded(item.relative)))


def _digest(partition: str, unit_name: str, root: Path, manifest: tuple[ManifestEntry, ...]) -> str:
    require_safe_component(partition, allow_profiles=False, label=partition)
    require_safe_component(unit_name, allow_profiles=False, label=unit_name)
    raw_partition = _encoded(partition)
    raw_name = _encoded(unit_name)
    digest = hashlib.sha256()
    digest.update(_DOMAIN)
    digest.update(_u64(len(raw_partition)))
    digest.update(raw_partition)
    digest.update(_u64(len(raw_name)))
    digest.update(raw_name)
    for item in manifest:
        raw = _encoded(item.relative.replace("\\", "/"))
        if item.kind == "directory":
            digest.update(b"D" + _u64(len(raw)) + raw)
            continue
        assert item.kind == "file" and item.size is not None
        digest.update(b"F" + _u64(len(raw)) + raw + _u64(item.size))
        try:
            with open(native_path(root.joinpath(*item.relative.split("/"))), "rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
        except OSError as exc:
            raise io_error("Unit file could not be hashed", "tree_unreadable", path=item.relative, os_error=str(exc)) from exc
    return digest.hexdigest()


def inventory_unit(root: Path, partition: str, unit_name: str) -> TreeInventory:
    """Return the second-pass manifest iff two complete passes are identical."""
    first = _scan(root)
    first_digest = _digest(partition, unit_name, root, first)
    second = _scan(root)
    second_digest = _digest(partition, unit_name, root, second)
    if first != second or first_digest != second_digest:
        raise validation_error("Unit tree changed during authorization", "tree_changed_during_read", path=unit_name)
    return TreeInventory(second_digest, second)


__all__ = ["ManifestEntry", "TreeInventory", "inventory_unit"]
