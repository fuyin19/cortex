"""Stable public status and error mapping."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    USAGE_ERROR = 2
    VALIDATION_BLOCKED = 3
    POLICY_BLOCKED = 4
    CONFLICT = 5
    INTERRUPTED = 6
    UNSUPPORTED = 7


class Status(StrEnum):
    OK = "ok"
    USAGE_ERROR = "usage_error"
    VALIDATION_BLOCKED = "validation_blocked"
    POLICY_BLOCKED = "policy_blocked"
    CONFLICT = "conflict"
    INTERRUPTED = "interrupted"
    UNSUPPORTED = "unsupported"

    @property
    def exit_code(self) -> ExitCode:
        return {
            Status.OK: ExitCode.OK,
            Status.USAGE_ERROR: ExitCode.USAGE_ERROR,
            Status.VALIDATION_BLOCKED: ExitCode.VALIDATION_BLOCKED,
            Status.POLICY_BLOCKED: ExitCode.POLICY_BLOCKED,
            Status.CONFLICT: ExitCode.CONFLICT,
            Status.INTERRUPTED: ExitCode.INTERRUPTED,
            Status.UNSUPPORTED: ExitCode.UNSUPPORTED,
        }[self]


class CortexError(Exception):
    """An expected error whose public status is part of the CLI contract."""

    def __init__(
        self,
        message: str,
        *,
        status: Status,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}

