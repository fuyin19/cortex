"""Thin explicit subprocess adapter for the anti-entropy Core runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CortexError, Status, io_error, usage_error


ABI = "anti-entropy-core.runner/v1"
RUNNER_ENV = "ANTI_ENTROPY_CORE_RUNNER"


@dataclass(frozen=True)
class CoreResult:
    abi: str
    status: str
    exit_code: int
    command: str
    data: dict[str, Any]
    issues: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class CoreRunner:
    """Invoke one explicit absolute runner once for each JSONL request."""

    def __init__(self, runner: str | os.PathLike[str]) -> None:
        raw = os.fspath(runner)
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise usage_error(
                "Anti-entropy Core runner must be an explicit absolute path",
                "core_runner_not_absolute",
                runner=raw,
            )
        self.path = Path(os.path.abspath(candidate))

    @classmethod
    def from_config(cls, runner: str | os.PathLike[str] | None = None) -> "CoreRunner":
        selected = os.fspath(runner) if runner is not None else os.environ.get(RUNNER_ENV)
        if not selected:
            raise usage_error(
                f"Set {RUNNER_ENV} to the absolute Core runner path",
                "core_runner_required",
            )
        return cls(selected)

    def _invoke(self, command: str, request: dict[str, Any]) -> CoreResult:
        wire = (
            json.dumps(
                {"command": command, "request": request},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(self.path)],
                input=wire,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise io_error(
                "Anti-entropy Core runner could not be started",
                "core_runner_start_failed",
                path=str(self.path),
                command=command,
                os_error=str(exc),
            ) from exc
        if completed.returncode != 0:
            raise io_error(
                "Anti-entropy Core runner process failed",
                "core_runner_process_failed",
                path=str(self.path),
                command=command,
                process_exit_code=completed.returncode,
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise io_error(
                "Anti-entropy Core runner emitted invalid JSON",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            ) from exc
        required = {"abi", "status", "exit_code", "command", "data", "issues"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise io_error(
                "Anti-entropy Core Result fields do not match the protocol",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            )
        if (
            not isinstance(payload["abi"], str)
            or not isinstance(payload["status"], str)
            or type(payload["exit_code"]) is not int
            or not isinstance(payload["command"], str)
            or not isinstance(payload["data"], dict)
            or not isinstance(payload["issues"], list)
        ):
            raise io_error(
                "Anti-entropy Core Result has invalid semantic field types",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            )
        if payload["abi"] != ABI or payload["command"] != command:
            raise io_error(
                "Anti-entropy Core Result identity does not match the request",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            )
        status_codes = {"ok": 0, "usage_error": 2, "validation_error": 3, "io_error": 6}
        if status_codes.get(payload["status"]) != payload["exit_code"]:
            raise io_error(
                "Anti-entropy Core Result status and exit code do not match",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            )
        issues = payload["issues"]
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("message"), str)
            for item in issues
        ):
            raise io_error(
                "Anti-entropy Core Result contains a malformed issue",
                "core_protocol_error",
                path=str(self.path),
                command=command,
            )
        return CoreResult(
            abi=payload["abi"],
            status=payload["status"],
            exit_code=payload["exit_code"],
            command=payload["command"],
            data=payload["data"],
            issues=issues,
        )

    @staticmethod
    def _raise(result: CoreResult) -> None:
        if result.ok:
            return
        first = result.issues[0] if result.issues else {
            "code": "core_runner_failed",
            "message": "Anti-entropy Core runner returned a non-ok Result",
        }
        status = {
            "usage_error": Status.USAGE_ERROR,
            "validation_error": Status.VALIDATION_ERROR,
            "busy": Status.BUSY,
            "io_error": Status.IO_ERROR,
        }.get(result.status, Status.IO_ERROR)
        raise CortexError(
            first["message"],
            status=status,
            code=first["code"],
            path=first.get("path"),
            details={"issues": result.issues, "core_command": result.command},
        )

    def require_success(self, result: CoreResult) -> dict[str, Any]:
        self._raise(result)
        return result.data

    @staticmethod
    def _request(path: Path, private_root_files: tuple[str, ...]) -> dict[str, Any]:
        request: dict[str, Any] = {"path": str(Path(os.path.abspath(path)))}
        if private_root_files:
            request["private_root_files"] = list(private_root_files)
        return request

    def inspect(self, path: Path, *, private_root_files: tuple[str, ...] = ()) -> dict[str, Any]:
        return self.require_success(self._invoke("inspect", self._request(path, private_root_files)))

    def probe_validate(self, path: Path, *, private_root_files: tuple[str, ...] = ()) -> CoreResult:
        return self._invoke("validate", self._request(path, private_root_files))

    def validate(self, path: Path, *, private_root_files: tuple[str, ...] = ()) -> dict[str, Any]:
        return self.require_success(self.probe_validate(path, private_root_files=private_root_files))

    def repair(self, path: Path, *, private_root_files: tuple[str, ...] = ()) -> dict[str, Any]:
        return self.require_success(self._invoke("repair", self._request(path, private_root_files)))

    def stage_complete(self, path: Path, *, private_root_files: tuple[str, ...] = ()) -> dict[str, Any]:
        return self.require_success(self._invoke("stage.complete", self._request(path, private_root_files)))


def require_core(core: CoreRunner | None) -> CoreRunner:
    return core if core is not None else CoreRunner.from_config()


__all__ = ["ABI", "CoreResult", "CoreRunner", "RUNNER_ENV", "require_core"]
