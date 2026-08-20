"""Small, explicit filesystem safety seam for Cortex 6."""

from __future__ import annotations

import os
import shutil
import stat
import time
import unicodedata
from pathlib import Path
from .errors import CortexError, io_error, validation_error


_FORBIDDEN = set('<>:"/\\|?*')
_IS_WINDOWS = os.name == "nt"
_WINDOWS_RENAME_RETRY_DELAYS = (0.05, 0.10, 0.20)
_DEVICES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
    *(f"com{i}" for i in "¹²³"),
    *(f"lpt{i}" for i in "¹²³"),
}


def native_path(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _metadata(path: Path) -> os.stat_result:
    try:
        return os.lstat(native_path(path))
    except OSError as exc:
        raise io_error("Filesystem entry could not be inspected", "path_unreadable", path=str(path), os_error=str(exc)) from exc


def is_reparse_metadata(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def exists(path: Path) -> bool:
    try:
        os.lstat(native_path(path))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def require_real_directory(path: Path, *, code: str = "directory_required") -> None:
    metadata = _metadata(path)
    if is_reparse_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise validation_error("Path must be a real directory", code, path=str(path))


def require_regular_file(path: Path, *, code: str = "ordinary_file_required") -> None:
    metadata = _metadata(path)
    if is_reparse_metadata(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise validation_error("Path must be an ordinary file", code, path=str(path))


def reject_reparse_ancestry(path: Path) -> None:
    current = Path(os.path.abspath(path))
    existing: list[Path] = []
    while True:
        if exists(current):
            existing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for candidate in reversed(existing):
        metadata = _metadata(candidate)
        if is_reparse_metadata(metadata):
            raise validation_error("Reparse-point traversal is forbidden", "reparse_path", path=str(candidate))


def is_windows_device_name(component: str) -> bool:
    stem = component.rstrip(" .").split(".", 1)[0].casefold()
    return stem in _DEVICES


def component_problem(component: str, *, allow_profiles: bool = True) -> tuple[str, str] | None:
    if not component or component in {".", ".."}:
        return "unsafe_component", "Path component is empty or relative"
    if component.casefold().startswith(".cortex-"):
        return "reserved_staging_name", "Cortex staging names are reserved"
    if component.endswith((".", " ")):
        return "unsafe_component", "Trailing dots and spaces are forbidden"
    if any(character in _FORBIDDEN or unicontrol(character) for character in component):
        return "unsafe_component", "Path component contains a forbidden character"
    if is_windows_device_name(component):
        return "windows_device_name", "Windows device names are forbidden"
    if not allow_profiles and component.casefold() == "profiles":
        return "reserved_partition_name", "Partition tags must not use the reserved profiles name"
    try:
        component.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return "unsafe_component", "Path component is not valid UTF-8 text"
    return None


def unicontrol(character: str) -> bool:
    return unicodedata.category(character) == "Cc"


def require_safe_component(component: str, *, allow_profiles: bool = True, label: str | None = None) -> None:
    problem = component_problem(component, allow_profiles=allow_profiles)
    if problem is not None:
        code, message = problem
        raise validation_error(message, code, path=label or component)


def checked_scandir(directory: Path) -> list[os.DirEntry[str]]:
    try:
        return sorted(os.scandir(native_path(directory)), key=lambda entry: entry.name.encode("utf-8", errors="surrogatepass"))
    except OSError as exc:
        raise io_error("Directory could not be read", "directory_unreadable", path=str(directory), os_error=str(exc)) from exc


def inspect_conversion(path: Path) -> tuple[str, list[tuple[str, Path, bool]]]:
    """Return kind and ordered (relative, source, is_dir) entries."""

    metadata = _metadata(path)
    if is_reparse_metadata(metadata):
        raise validation_error("Conversion must not be a symlink or reparse point", "reparse_path", path=str(path))
    if stat.S_ISREG(metadata.st_mode):
        require_safe_component(path.name, label=path.name)
        return "file", [(path.name, path, False)]
    if not stat.S_ISDIR(metadata.st_mode):
        raise validation_error("Conversion must be an ordinary file or real directory", "conversion_type", path=str(path))

    entries: list[tuple[str, Path, bool]] = []
    collisions: dict[str, str] = {}

    def visit(directory: Path, prefix: str = "") -> None:
        for entry in checked_scandir(directory):
            require_safe_component(entry.name, label=f"{prefix}/{entry.name}".strip("/"))
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            key = relative.casefold()
            previous = collisions.get(key)
            if previous is not None and previous != relative:
                raise validation_error(
                    "Conversion paths collide under case folding",
                    "conversion_casefold_collision",
                    paths=[previous, relative],
                )
            collisions[key] = relative
            try:
                child_metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise io_error("Conversion entry could not be inspected", "path_unreadable", path=relative, os_error=str(exc)) from exc
            if is_reparse_metadata(child_metadata):
                raise validation_error("Conversion contains a symlink or reparse point", "reparse_path", path=relative)
            source = Path(entry.path)
            if stat.S_ISDIR(child_metadata.st_mode):
                entries.append((relative, source, True))
                visit(source, relative)
            elif stat.S_ISREG(child_metadata.st_mode):
                entries.append((relative, source, False))
            else:
                raise validation_error("Conversion contains a nonregular entry", "nonregular_entry", path=relative)

    visit(path)
    return "directory", entries


def copy_regular(source: Path, destination: Path) -> None:
    require_regular_file(source)
    try:
        with open(native_path(source), "rb") as incoming, open(native_path(destination), "xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
    except OSError as exc:
        raise io_error("File bytes could not be copied", "copy_failed", path=str(source), os_error=str(exc)) from exc


def copy_conversion(entries: list[tuple[str, Path, bool]], destination: Path) -> None:
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for relative, source, is_directory in entries:
            target = destination.joinpath(*relative.split("/"))
            if is_directory:
                target.mkdir(exist_ok=False)
            else:
                copy_regular(source, target)
    except CortexError:
        raise
    except OSError as exc:
        raise io_error("Conversion tree could not be copied", "copy_failed", path=str(destination), os_error=str(exc)) from exc


def remove_tree_best_effort(path: Path) -> None:
    try:
        if exists(path):
            shutil.rmtree(native_path(path))
    except OSError:
        pass


def rename_no_replace(source: Path, destination: Path) -> None:
    if exists(destination):
        raise FileExistsError(str(destination))
    if _IS_WINDOWS:
        last_error: PermissionError | None = None
        for attempt in range(len(_WINDOWS_RENAME_RETRY_DELAYS) + 1):
            if attempt:
                time.sleep(_WINDOWS_RENAME_RETRY_DELAYS[attempt - 1])
                if exists(destination):
                    raise FileExistsError(str(destination))
            try:
                os.rename(native_path(source), native_path(destination))
                return
            except PermissionError as exc:
                last_error = exc
            except OSError as exc:
                raise io_error(
                    "Directory could not be published without replacement",
                    "publish_failed",
                    path=str(destination),
                    os_error=str(exc),
                ) from exc
        assert last_error is not None
        raise io_error(
            "Directory could not be published without replacement",
            "publish_failed",
            path=str(destination),
            os_error=str(last_error),
        ) from last_error
    if os.name == "posix":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameat2.restype = ctypes.c_int
            result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number == getattr(os, "EEXIST", 17):
                raise FileExistsError(str(destination))
            raise io_error(
                "Directory could not be published without replacement",
                "publish_failed",
                path=str(destination),
                os_error=os.strerror(error_number),
            )
        renamex = getattr(libc, "renamex_np", None)
        if renamex is not None:
            renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex.restype = ctypes.c_int
            result = renamex(os.fsencode(source), os.fsencode(destination), 4)
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number == 17:
                raise FileExistsError(str(destination))
            raise io_error(
                "Directory could not be published without replacement",
                "publish_failed",
                path=str(destination),
                os_error=os.strerror(error_number),
            )
        raise io_error(
            "This POSIX host does not expose an atomic no-replace directory rename",
            "no_replace_unsupported",
            path=str(destination),
        )
    try:
        os.rename(native_path(source), native_path(destination))
    except OSError as exc:
        raise io_error("Directory could not be published without replacement", "publish_failed", path=str(destination), os_error=str(exc)) from exc


__all__ = [
    "checked_scandir",
    "component_problem",
    "copy_conversion",
    "copy_regular",
    "exists",
    "inspect_conversion",
    "is_reparse_metadata",
    "is_windows_device_name",
    "native_path",
    "reject_reparse_ancestry",
    "remove_tree_best_effort",
    "rename_no_replace",
    "require_real_directory",
    "require_regular_file",
    "require_safe_component",
]
