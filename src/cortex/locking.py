"""The one and only lock selected for a Cortex mutation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .constants import ROOT_LOCK_FILENAME
from .errors import CortexError, Status, io_error, validation_error
from .native import exists, require_regular_file


@contextmanager
def writer_lock(path: Path) -> Iterator[BinaryIO]:
    try:
        stream = path.open("r+b", buffering=0)
    except OSError as exc:
        raise io_error("Writer lock target could not be opened", "lock_target_unreadable", path=str(path), os_error=str(exc)) from exc
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise CortexError(
                "Another Cortex writer holds the selected lock",
                status=Status.BUSY,
                code="workspace_busy",
                path=str(path),
            ) from exc
        stream.seek(0)
        yield stream
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


def workspace_lock_path(workspace: Path) -> Path:
    root_lock = workspace.parent / ROOT_LOCK_FILENAME
    if exists(root_lock):
        require_regular_file(root_lock, code="invalid_root_lock")
        try:
            if root_lock.stat().st_size != 0:
                raise validation_error("KB-root lock must remain zero bytes", "invalid_root_lock", path=str(root_lock))
        except OSError as exc:
            raise io_error("KB-root lock could not be inspected", "lock_target_unreadable", path=str(root_lock), os_error=str(exc)) from exc
        return root_lock
    return workspace / "profiles" / "record-schema.json"


@contextmanager
def workspace_lock(workspace: Path) -> Iterator[BinaryIO]:
    with writer_lock(workspace_lock_path(workspace)) as stream:
        yield stream


__all__ = ["writer_lock", "workspace_lock", "workspace_lock_path"]
