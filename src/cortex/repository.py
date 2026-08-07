"""Guarded filesystem primitives used by Cortex transactions.

The repository deliberately exposes only workspace-relative paths.  All
mutations pass through the same path and immutable-source checks, making it
impossible for a malformed plan to escape the authorized root.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unicodedata
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator
from urllib.parse import unquote

from .errors import CortexError, Status


def _native_replace_path(path: str | os.PathLike[str]) -> str:
    """Return an extended-length absolute path for Windows atomic replace."""

    value = str(Path(path).resolve(strict=False))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


_WINDOWS_DEVICES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _policy_error(message: str, code: str, **details: object) -> CortexError:
    return CortexError(
        message,
        status=Status.POLICY_BLOCKED,
        code=code,
        details=dict(details),
    )


def normalize_repository_path(value: str | os.PathLike[str]) -> str:
    """Return a normalized POSIX relative path or reject it.

    The guard is intentionally platform-independent so plans produced on one
    operating system cannot become unsafe when applied on Windows.
    """

    raw_value = value.as_posix() if isinstance(value, Path) else os.fspath(value)
    raw = unicodedata.normalize("NFC", raw_value)
    if not raw or raw == ".":
        return "."
    if "\\" in raw:
        raise _policy_error("Backslash separators are forbidden", "windows_separator", path=raw)
    decoded_once = unquote(raw)
    decoded_twice = unquote(decoded_once)
    if decoded_twice != decoded_once:
        raise _policy_error("Double percent-encoded path is forbidden", "double_percent_decode", path=raw)
    candidate = decoded_once
    if candidate.startswith("/") or candidate.startswith("//"):
        raise _policy_error("Absolute or UNC paths are forbidden", "absolute_path", path=raw)
    if len(candidate) >= 2 and candidate[1] == ":":
        raise _policy_error("Drive-qualified paths are forbidden", "drive_path", path=raw)

    parts = candidate.split("/")
    normalized: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise _policy_error("Parent traversal is forbidden", "path_escape", path=raw)
        if part.endswith((".", " ")):
            raise _policy_error("Windows trailing dot/space is forbidden", "windows_path", path=raw)
        stem = part.split(".", 1)[0].rstrip(" .").upper()
        if stem in _WINDOWS_DEVICES:
            raise _policy_error("Windows device names are forbidden", "windows_device", path=raw)
        if any(ord(character) < 32 or ord(character) == 127 for character in part) or ":" in part:
            raise _policy_error("Control characters and colons are forbidden", "invalid_path", path=raw)
        normalized.append(unicodedata.normalize("NFC", part))
    return "/".join(normalized) or "."


class SafeRepository:
    """Filesystem repository constrained to one authorized root."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        protected_prefixes: Iterable[str] = ("sources", "00-Raw"),
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.protected_prefixes = tuple(
            PurePosixPath(normalize_repository_path(prefix)).parts
            for prefix in protected_prefixes
        )

    def normalize(self, relative: str | os.PathLike[str]) -> str:
        return normalize_repository_path(relative)

    def resolve(
        self,
        relative: str | os.PathLike[str],
        *,
        reject_symlinks: bool = True,
    ) -> Path:
        normalized = self.normalize(relative)
        parts = () if normalized == "." else PurePosixPath(normalized).parts
        candidate = self.root.joinpath(*parts)

        current = self.root
        if reject_symlinks:
            for part in parts:
                current = current / part
                if current.exists() and current.is_symlink():
                    raise _policy_error(
                        "Symbolic links are not allowed in repository paths",
                        "symlink_escape",
                        path=normalized,
                    )
        resolved = candidate.resolve(strict=False)
        try:
            if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
                raise ValueError
        except ValueError as exc:
            raise _policy_error("Path escapes authorized repository", "path_escape", path=normalized) from exc
        return candidate

    def _is_protected(self, relative: str | os.PathLike[str]) -> bool:
        parts = PurePosixPath(self.normalize(relative)).parts
        # Protect both captured descendants and any ancestor operation that
        # would recursively move/delete a protected subtree.
        return any(
            parts[: len(prefix)] == prefix or prefix[: len(parts)] == parts
            for prefix in self.protected_prefixes
        )

    def _protect_existing(self, relative: str | os.PathLike[str], action: str) -> None:
        path = self.resolve(relative)
        if self._is_protected(relative) and os.path.exists(_native_replace_path(path)):
            raise _policy_error(
                "Captured sources are immutable",
                "immutable_source",
                path=self.normalize(relative),
                action=action,
            )

    def exists(self, relative: str | os.PathLike[str]) -> bool:
        return os.path.exists(_native_replace_path(self.resolve(relative)))

    def read_bytes(self, relative: str | os.PathLike[str]) -> bytes:
        path = self.resolve(relative)
        native = _native_replace_path(path)
        if not os.path.isfile(native):
            raise _policy_error("Repository file does not exist", "missing_path", path=self.normalize(relative))
        with open(native, "rb") as stream:
            return stream.read()

    def read_text(self, relative: str | os.PathLike[str], *, encoding: str = "utf-8") -> str:
        try:
            return self.read_bytes(relative).decode(encoding)
        except UnicodeDecodeError as exc:
            raise CortexError(
                "Repository text is not valid UTF-8",
                status=Status.VALIDATION_BLOCKED,
                code="invalid_text_encoding",
                details={"path": self.normalize(relative)},
            ) from exc

    def mkdir(self, relative: str | os.PathLike[str]) -> Path:
        path = self.resolve(relative)
        os.makedirs(_native_replace_path(path), exist_ok=True)
        return path

    def write_bytes(
        self,
        relative: str | os.PathLike[str],
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> Path:
        path = self.resolve(relative)
        if os.path.exists(_native_replace_path(path)) and not overwrite:
            raise CortexError(
                "Destination already exists",
                status=Status.CONFLICT,
                code="destination_exists",
                details={"path": self.normalize(relative)},
            )
        self._protect_existing(relative, "replace")
        os.makedirs(_native_replace_path(path.parent), exist_ok=True)
        # Keep the temporary component compact: content-addressed filenames
        # can already be 69+ characters, and repeating them in the prefix can
        # cross Windows MAX_PATH inside a transaction staging tree.
        fd, temporary = tempfile.mkstemp(
            prefix=".w-",
            suffix=".tmp",
            dir=_native_replace_path(path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(_native_replace_path(temporary), _native_replace_path(path))
        except BaseException:
            try:
                os.unlink(_native_replace_path(temporary))
            except FileNotFoundError:
                pass
            raise
        return path

    def write_text(
        self,
        relative: str | os.PathLike[str],
        text: str,
        *,
        overwrite: bool = True,
    ) -> Path:
        return self.write_bytes(relative, text.encode("utf-8"), overwrite=overwrite)

    def copy_from(
        self,
        source_repository: "SafeRepository",
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        return self.write_bytes(destination, source_repository.read_bytes(source), overwrite=overwrite)

    def move(
        self,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        self._protect_existing(source, "move")
        self._protect_existing(destination, "replace")
        source_path = self.resolve(source)
        destination_path = self.resolve(destination)
        if not source_path.exists():
            raise _policy_error("Move source does not exist", "missing_path", path=self.normalize(source))
        if destination_path.exists() and not overwrite:
            raise CortexError(
                "Move destination already exists",
                status=Status.CONFLICT,
                code="destination_exists",
                details={"path": self.normalize(destination)},
            )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            os.replace(source_path, destination_path)
        else:
            source_path.rename(destination_path)
        return destination_path

    def delete(self, relative: str | os.PathLike[str], *, recursive: bool = False) -> None:
        self._protect_existing(relative, "delete")
        path = self.resolve(relative)
        if not path.exists():
            return
        if path.is_dir():
            if recursive:
                shutil.rmtree(path)
            else:
                path.rmdir()
        else:
            path.unlink()

    def iter_files(self, relative: str | os.PathLike[str] = ".") -> Iterator[str]:
        base = self.resolve(relative)
        if not base.exists():
            return
        for path in sorted(base.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")):
            if path.is_symlink():
                raise _policy_error("Symbolic links are not allowed", "symlink_escape", path=str(path))
            if path.is_file():
                yield path.relative_to(self.root).as_posix()

    def copy_tree_to(
        self,
        destination: str | os.PathLike[str],
        *,
        exclude_prefixes: Iterable[str] = (),
    ) -> Path:
        destination_path = Path(destination)
        if destination_path.exists():
            raise CortexError(
                "Copy destination already exists",
                status=Status.CONFLICT,
                code="destination_exists",
                details={"path": str(destination_path)},
            )
        excluded = tuple(PurePosixPath(normalize_repository_path(item)).parts for item in exclude_prefixes)
        os.makedirs(_native_replace_path(destination_path), exist_ok=False)
        # Empty directories are part of the Cortex authoring profile even
        # though canonical tree digests intentionally cover files only.
        # Preserve them during copy-on-write so mutation plans need not carry
        # synthetic mkdir operations (and true zero-operation plans stay zero).
        for path in sorted(self.root.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")):
            if path.is_symlink():
                raise _policy_error("Symbolic links are not allowed", "symlink_escape", path=str(path))
            if not path.is_dir():
                continue
            relative = path.relative_to(self.root).as_posix()
            parts = PurePosixPath(relative).parts
            if any(parts[: len(prefix)] == prefix for prefix in excluded):
                continue
            os.makedirs(_native_replace_path(destination_path.joinpath(*parts)), exist_ok=True)
        for relative in self.iter_files():
            parts = PurePosixPath(relative).parts
            if any(parts[: len(prefix)] == prefix for prefix in excluded):
                continue
            target = destination_path.joinpath(*parts)
            os.makedirs(_native_replace_path(target.parent), exist_ok=True)
            # Staging trees sit deeper than the portable bundle root; long
            # reference filenames can otherwise cross Windows MAX_PATH here.
            shutil.copy2(
                _native_replace_path(self.resolve(relative)),
                _native_replace_path(target),
                follow_symlinks=False,
            )
        return destination_path

    def tree_digest(self, relative: str | os.PathLike[str] = ".") -> str:
        """Digest raw file bytes and paths, excluding no data implicitly."""

        base = self.resolve(relative)
        if not base.exists():
            from .canonical import canonical_json_bytes

            return hashlib.sha256(canonical_json_bytes([])).hexdigest()
        if base.is_dir():
            from .canonical import tree_manifest

            return str(tree_manifest(base)["tree_digest"])
        entries: list[dict[str, object]] = []
        data = base.read_bytes()
        entries.append({"path": base.name, "kind": "file", "size_bytes": len(data), "digest": hashlib.sha256(data).hexdigest()})
        payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class WorkspaceLock(AbstractContextManager["WorkspaceLock"]):
    """The single cross-process workspace lock.

    The lock file is persistent and always located at
    ``.cortex/locks/workspace.lock``.  Locking is performed by the OS rather
    than by checking file existence, so a crashed process cannot leave a stale
    lock behind.
    """

    _process_guard = threading.Lock()

    def __init__(self, workspace_root: str | os.PathLike[str], *, timeout: float = 0.0) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.path = self.workspace_root / ".cortex" / "locks" / "workspace.lock"
        self.timeout = max(0.0, timeout)
        self._stream: object | None = None
        self._locked = False

    def _try_lock(self) -> bool:
        assert self._stream is not None
        if os.name == "nt":  # pragma: no cover - exercised on Windows runners
            import msvcrt

            stream = self._stream
            stream.seek(0)  # type: ignore[attr-defined]
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except (BlockingIOError, OSError):
            return False
        return True

    def acquire(self) -> "WorkspaceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+b")
        self._stream.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        if self._stream.tell() == 0:  # type: ignore[attr-defined]
            self._stream.write(b"\0")  # type: ignore[attr-defined]
            self._stream.flush()  # type: ignore[attr-defined]
        deadline = time.monotonic() + self.timeout
        while not self._try_lock():
            if time.monotonic() >= deadline:
                self._stream.close()  # type: ignore[attr-defined]
                self._stream = None
                raise CortexError(
                    "Workspace is locked by another process",
                    status=Status.CONFLICT,
                    code="workspace_locked",
                    details={"lock": str(self.path)},
                )
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        self._locked = True
        return self

    def release(self) -> None:
        if not self._locked or self._stream is None:
            return
        if os.name == "nt":  # pragma: no cover - exercised on Windows runners
            import msvcrt

            self._stream.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        self._stream.close()  # type: ignore[attr-defined]
        self._stream = None
        self._locked = False

    def __enter__(self) -> "WorkspaceLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


__all__ = ["SafeRepository", "WorkspaceLock", "normalize_repository_path"]
