"""Stable statuses, exit codes, and expected failures."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    USAGE_ERROR = 2
    VALIDATION_ERROR = 3
    BUSY = 5
    IO_ERROR = 6


class Status(StrEnum):
    OK = "ok"
    USAGE_ERROR = "usage_error"
    VALIDATION_ERROR = "validation_error"
    BUSY = "busy"
    IO_ERROR = "io_error"

    @property
    def exit_code(self) -> ExitCode:
        return {
            Status.OK: ExitCode.OK,
            Status.USAGE_ERROR: ExitCode.USAGE_ERROR,
            Status.VALIDATION_ERROR: ExitCode.VALIDATION_ERROR,
            Status.BUSY: ExitCode.BUSY,
            Status.IO_ERROR: ExitCode.IO_ERROR,
        }[self]


def issue(code: str, message: str, *, path: str | None = None, **details: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        value["path"] = path
    if details:
        value["details"] = details
    return value


class CortexError(Exception):
    """An expected command failure with a public status and issue."""

    def __init__(
        self,
        message: str,
        *,
        status: Status = Status.VALIDATION_ERROR,
        code: str = "validation_error",
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.path = path
        self.details = details or {}

    def as_issue(self) -> dict[str, Any]:
        return issue(self.code, str(self), path=self.path, **self.details)


def validation_error(message: str, code: str, *, path: str | None = None, **details: Any) -> CortexError:
    return CortexError(message, status=Status.VALIDATION_ERROR, code=code, path=path, details=details)


def usage_error(message: str, code: str = "invalid_arguments", **details: Any) -> CortexError:
    return CortexError(message, status=Status.USAGE_ERROR, code=code, details=details)


def io_error(message: str, code: str = "filesystem_error", *, path: str | None = None, **details: Any) -> CortexError:
    return CortexError(message, status=Status.IO_ERROR, code=code, path=path, details=details)

