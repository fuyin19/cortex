"""Thin explicit subprocess adapter for the anti-entropy Core runner."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CortexError, Status, io_error as _io_error, usage_error


ABI = "anti-entropy-core.runner/v1"
RUNNER_ENV = "ANTI_ENTROPY_CORE_RUNNER"
EXPECTED_CORE_VERSION = "1.2.1"
_DEFAULT_RUNNER: tuple[Path, Path] | None = None


def io_error(message: str, code: str, **details: Any) -> CortexError:
    diagnostic = {"actual_abi": "unknown", "actual_version": "unknown", "expected_abi": ABI,
                  "expected_version": EXPECTED_CORE_VERSION,
                  "remedy": "Check the selected runner and update Core and this consumer to their matching releases",
                  **details}
    return _io_error(message, code, **diagnostic)


def set_default_runner(runner: Path, skill_marker: Path) -> None:
    """Receive a lexical default from the actual skill launcher, never infer a wheel root."""
    global _DEFAULT_RUNNER
    _DEFAULT_RUNNER = (runner, skill_marker)


def _ordinary_chain(path: Path, *, selected_runner: Path | None = None) -> None:
    for node in reversed((path,) + tuple(path.parents)):
        try:
            info = node.lstat()
        except OSError as exc:
            raise usage_error("Core path is missing or unreadable; install the matching Core skill or set an absolute runner",
                              "core_runner_path_invalid", runner=str(selected_runner or path), actual=str(exc),
                              actual_abi="unknown", actual_version="unknown",
                              expected_abi=ABI, expected_version=EXPECTED_CORE_VERSION) from exc
        reparse = bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        ordinary = stat.S_ISREG(info.st_mode) if node == path else stat.S_ISDIR(info.st_mode)
        if stat.S_ISLNK(info.st_mode) or reparse or not ordinary:
            raise usage_error("Core path must be an ordinary file with ordinary ancestors; install the matching Core skill or set an absolute runner",
                              "core_runner_path_invalid", runner=str(selected_runner or path), actual=str(node), actual_abi="unknown", actual_version="unknown",
                              expected_abi=ABI, expected_version=EXPECTED_CORE_VERSION)



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

    def __init__(self, runner: str | os.PathLike[str], *, skill_marker: Path | None = None) -> None:
        raw = os.fspath(runner)
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise usage_error(
                "Core runner must be an absolute ordinary file; install Core 1.2.1 or set ANTI_ENTROPY_CORE_RUNNER",
                "core_runner_not_absolute", runner=raw, actual_abi="unknown", actual_version="unknown",
                expected_abi=ABI, expected_version=EXPECTED_CORE_VERSION,
            )
        _ordinary_chain(candidate)
        if skill_marker is not None:
            _ordinary_chain(skill_marker, selected_runner=candidate)
        self.path = Path(os.path.abspath(candidate))
        self._process: subprocess.Popen[bytes] | None = None
        result = self._invoke("capabilities", {}, timeout=30, preflight=True)
        self._raise(result)
        if result.data.get("version") != EXPECTED_CORE_VERSION:
            self._version_mismatch(result.abi, result.data.get("version", "unknown"))

    def _version_mismatch(self, actual_abi: object, actual_version: object) -> None:
        raise usage_error(
            "Anti-entropy Core ABI/version mismatch; update Core and this consumer to their matching releases",
            "core_version_mismatch", runner=str(self.path), actual_abi=actual_abi, actual_version=actual_version,
            expected_abi=ABI, expected_version=EXPECTED_CORE_VERSION,
        )

    @contextmanager
    def session(self):
        if self._process is not None:
            yield self
            return
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-I", str(self.path)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise io_error("Anti-entropy Core runner could not be started", "core_runner_start_failed", path=str(self.path), os_error=str(exc)) from exc
        try:
            yield self
            assert self._process.stdin is not None
            self._process.stdin.close()
            code = self._process.wait()
            if code != 0:
                raise io_error("Anti-entropy Core runner process failed", "core_runner_process_failed", path=str(self.path), process_exit_code=code)
        finally:
            process, self._process = self._process, None
            if process is not None and process.poll() is None:
                process.kill(); process.wait()

    @classmethod
    def from_config(cls, runner: str | os.PathLike[str] | None = None) -> "CoreRunner":
        if runner is not None:
            return cls(runner)
        if RUNNER_ENV in os.environ:
            return cls(os.environ[RUNNER_ENV])
        if _DEFAULT_RUNNER is not None:
            return cls(_DEFAULT_RUNNER[0], skill_marker=_DEFAULT_RUNNER[1])
        raise usage_error(
            f"Install anti-entropy-core beside cortex or set {RUNNER_ENV} to its absolute runner path",
            "core_runner_required", actual_abi="unknown", actual_version="unknown",
            expected_abi=ABI, expected_version=EXPECTED_CORE_VERSION,
        )

    def _invoke(self, command: str, request: dict[str, Any], *, timeout: int | None = None, preflight: bool = False) -> CoreResult:
        wire = (
            json.dumps(
                {"command": command, "request": request},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if self._process is not None:
            process = self._process
            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(wire); process.stdin.flush()
                stdout = process.stdout.readline()
            except OSError as exc:
                raise io_error("Anti-entropy Core runner protocol failed", "core_protocol_error", path=str(self.path), command=command) from exc
            if stdout == b"":
                raise io_error("Anti-entropy Core runner ended early", "core_protocol_error", path=str(self.path), command=command)
            return self._decode_result(command, stdout)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(self.path)],
                input=wire,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise io_error(
                "Anti-entropy Core runner could not be started or timed out",
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
        return self._decode_result(command, completed.stdout, preflight=preflight)

    def _decode_result(self, command: str, stdout: bytes, *, preflight: bool = False) -> CoreResult:
        try:
            payload = json.loads(stdout.decode("utf-8", errors="strict"))
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
        if payload["command"] != command:
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
        if payload["abi"] != ABI:
            if preflight:
                self._version_mismatch(payload["abi"], payload["data"].get("version", "unknown"))
            raise io_error(
                "Anti-entropy Core Result identity does not match the request", "core_protocol_error",
                path=str(self.path), command=command, actual_abi=payload["abi"],
                actual_version=payload["data"].get("version", "unknown"),
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


__all__ = ["ABI", "CoreResult", "CoreRunner", "RUNNER_ENV", "EXPECTED_CORE_VERSION", "require_core", "set_default_runner"]
