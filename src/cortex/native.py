"""Native filesystem seam used by every Cortex 4 path operation."""

from __future__ import annotations

import ctypes
import os
import shutil
import stat
import unicodedata
from pathlib import Path
from typing import Iterator


def native_path(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def exists(path: str | os.PathLike[str]) -> bool:
    return os.path.exists(native_path(path))


def is_file(path: str | os.PathLike[str]) -> bool:
    return os.path.isfile(native_path(path))


def is_dir(path: str | os.PathLike[str]) -> bool:
    return os.path.isdir(native_path(path))


def is_reparse(path: str | os.PathLike[str]) -> bool:
    try:
        metadata = os.lstat(native_path(path))
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def reject_reparse_ancestry(path: str | os.PathLike[str]) -> None:
    current = Path(os.path.abspath(os.fspath(path)))
    existing: list[Path] = []
    while True:
        if exists(current):
            existing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for candidate in reversed(existing):
        if is_reparse(candidate):
            raise OSError(f"reparse traversal is forbidden: {candidate}")


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _FlushFileBuffers = _kernel32.FlushFileBuffers
    _FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _FlushFileBuffers.restype = wintypes.BOOL
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _OPEN_EXISTING = 3
    _FILE_SHARE_ALL = 1 | 2 | 4
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _VOLUME_NAME_GUID = 0x1


def _win_handle(path: str | os.PathLike[str], *, write: bool = False):
    access = 0x40000000 if write else 0
    handle = _CreateFileW(
        native_path(path), access, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _win_final(path: str | os.PathLike[str]) -> str:
    handle = _win_handle(path)
    try:
        needed = _GetFinalPathNameByHandleW(handle, None, 0, _VOLUME_NAME_GUID)
        if not needed:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(needed + 1)
        written = _GetFinalPathNameByHandleW(handle, buffer, len(buffer), _VOLUME_NAME_GUID)
        if not written or written >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value
    finally:
        _CloseHandle(handle)


def canonical_handle_path(path: str | os.PathLike[str]) -> str:
    """Return a final handle-derived path, appending only absent descendants."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    reject_reparse_ancestry(absolute)
    missing: list[str] = []
    existing = absolute
    while not exists(existing):
        missing.append(existing.name)
        if existing.parent == existing:
            raise OSError(f"no existing ancestor for {absolute}")
        existing = existing.parent
    if os.name == "nt":
        base = _win_final(existing)
        separator = "\\"
    else:
        base = os.path.realpath(existing)
        separator = "/"
    for component in reversed(missing):
        base = base.rstrip("\\/") + separator + unicodedata.normalize("NFC", component)
    normalized = unicodedata.normalize("NFC", base)
    return normalized.casefold() if os.name == "nt" else normalized


def volume_identity(path: str | os.PathLike[str]) -> str:
    absolute = Path(os.path.abspath(os.fspath(path)))
    while not exists(absolute):
        if absolute.parent == absolute:
            raise OSError("no existing ancestor")
        absolute = absolute.parent
    if os.name == "nt":
        final = _win_final(absolute)
        if final.startswith("\\\\?\\Volume{"):
            return final.split("\\", 4)[3].casefold()
        if final.startswith("\\\\?\\UNC\\"):
            return "unc:" + "\\".join(final[8:].split("\\")[:2]).casefold()
        return final[:3].casefold()
    return str(os.stat(native_path(absolute)).st_dev)


def require_directory_flush_success(succeeded: bool, error_code: int) -> None:
    """Reject every failed Windows directory flush, including errors 1 and 6."""

    if not succeeded:
        raise OSError(error_code, os.strerror(error_code))


def flush_directory(path: str | os.PathLike[str]) -> None:
    if os.name != "nt":
        descriptor = os.open(native_path(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    # A failed directory FlushFileBuffers is not a durability barrier.  In
    # particular ERROR_INVALID_FUNCTION and ERROR_INVALID_HANDLE must remain
    # hard failures so apply never claims a journal on an unsupported volume.
    handle = _win_handle(path, write=True)
    try:
        succeeded=bool(_FlushFileBuffers(handle))
        require_directory_flush_success(succeeded,ctypes.get_last_error() if not succeeded else 0)
    finally:
        _CloseHandle(handle)


def durability_supported(directory: str | os.PathLike[str]) -> bool:
    try:
        flush_directory(directory)
        return True
    except OSError:
        return False


def copy_tree(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
    """Copy a symlink-free tree using extended native paths throughout."""

    reject_reparse_ancestry(source)
    os.makedirs(native_path(destination), exist_ok=False)

    def visit(src: str, dst: str) -> None:
        for entry in os.scandir(src):
            target = os.path.join(dst, entry.name)
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise OSError(f"reparse tree entry is forbidden: {entry.path}")
            if stat.S_ISDIR(metadata.st_mode):
                os.makedirs(target, exist_ok=False)
                visit(entry.path, target)
                flush_directory(target)
            elif stat.S_ISREG(metadata.st_mode):
                shutil.copy2(entry.path, target, follow_symlinks=False)
                with open(target, "r+b") as handle:
                    os.fsync(handle.fileno())
            else:
                raise OSError(f"special tree entry is forbidden: {entry.path}")

    visit(native_path(source), native_path(destination))
    flush_directory(destination)


def remove_tree(path: str | os.PathLike[str]) -> None:
    if exists(path):
        shutil.rmtree(native_path(path))


__all__ = [
    "canonical_handle_path", "copy_tree", "durability_supported", "exists",
    "flush_directory", "is_dir", "is_file", "is_reparse", "native_path",
    "reject_reparse_ancestry", "remove_tree", "require_directory_flush_success", "volume_identity",
]
