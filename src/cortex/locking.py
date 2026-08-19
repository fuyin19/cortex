"""The one and only Cortex 5 writer lock."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .errors import CortexError, Status, io_error


@contextmanager
def workspace_lock(workspace: Path) -> Iterator[BinaryIO]:
    path = workspace / "profiles" / "record-schema.json"
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
                "Another Cortex writer holds the workspace lock",
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


__all__ = ["workspace_lock"]
