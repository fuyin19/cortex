"""Strict subprocess adapter for anti-entropy Core's workspace contracts."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
import subprocess
import sys
from typing import Any


ABI = "anti-entropy-core.runner/v1"
RUNNER_ENV = "ANTI_ENTROPY_CORE_RUNNER"
EXPECTED_CORE_VERSION = "1.2.1"
_DEFAULT_RUNNER: tuple[Path, Path] | None = None


def set_default_runner(runner: Path, skill_marker: Path) -> None:
    """Receive the default from the actual skill launcher without searching wheel ancestors."""
    global _DEFAULT_RUNNER
    _DEFAULT_RUNNER = (runner, skill_marker)

OUTER_CONTRACT = "collaborative-workspace-envelope/v1"
INNER_CONTRACT = "agent-workbench-envelope/v1"


class CoreFailure(Exception):
    def __init__(self, code: str, *, status: str = "io_error", data: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.data = data or {}


class CoreRunner:
    def __init__(self, configured: str | None = None) -> None:
        marker: Path | None = None
        if configured is not None:
            raw = configured
        elif RUNNER_ENV in os.environ:
            raw = os.environ[RUNNER_ENV]
        elif _DEFAULT_RUNNER is not None:
            raw, marker = str(_DEFAULT_RUNNER[0]), _DEFAULT_RUNNER[1]
        else:
            raise CoreFailure("core_runner_required", status="usage_error", data=self._diagnostic(None))
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise CoreFailure("core_runner_not_absolute", status="usage_error", data=self._diagnostic(raw))
        self._ordinary_chain(candidate)
        if marker is not None:
            self._ordinary_chain(marker, selected_runner=candidate)
        self.path = Path(os.path.abspath(candidate))
        result = self.invoke("capabilities", {}, _preflight=True)
        if result["data"].get("version") != EXPECTED_CORE_VERSION:
            self._version_mismatch(result["abi"], result["data"].get("version", "unknown"))

    @staticmethod
    def _diagnostic(path: object) -> dict[str, Any]:
        return {"path": str(path) if path is not None else None, "actual_abi": "unknown", "actual_version": "unknown",
                "expected_abi": ABI, "expected_version": EXPECTED_CORE_VERSION,
                "remedy": "Install the matching anti-entropy-core skill beside cortex or set ANTI_ENTROPY_CORE_RUNNER to its absolute ordinary runner; update Core and consumer together"}

    @classmethod
    def _ordinary_chain(cls, path: Path, *, selected_runner: Path | None = None) -> None:
        for node in reversed((path,) + tuple(path.parents)):
            try:
                info = node.lstat()
            except OSError as exc:
                raise CoreFailure("core_runner_path_invalid", status="usage_error",
                                  data={**cls._diagnostic(selected_runner or path), "actual": str(exc)}) from exc
            reparse = bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            ordinary = stat.S_ISREG(info.st_mode) if node == path else stat.S_ISDIR(info.st_mode)
            if stat.S_ISLNK(info.st_mode) or reparse or not ordinary:
                raise CoreFailure("core_runner_path_invalid", status="usage_error",
                                  data={**cls._diagnostic(selected_runner or path), "actual": str(node)})

    def _version_mismatch(self, actual_abi: object, actual_version: object) -> None:
        raise CoreFailure("core_version_mismatch", status="usage_error",
                          data={**self._diagnostic(self.path), "actual_abi": actual_abi, "actual_version": actual_version})

    def invoke(self, command: str, request: dict[str, Any], *, allow_invalid: bool = False,
               _preflight: bool = False) -> dict[str, Any]:
        wire = (json.dumps({"command": command, "request": request}, ensure_ascii=False,
                           separators=(",", ":")) + "\n").encode("utf-8")
        diagnostic = self._diagnostic(self.path)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(self.path)], input=wire,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30 if _preflight else 120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CoreFailure("core_runner_start_failed", data=diagnostic) from exc
        if completed.returncode != 0:
            raise CoreFailure("core_runner_process_failed", data={**diagnostic, "process_exit_code": completed.returncode})
        try:
            value = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoreFailure("core_protocol_error", data=diagnostic) from exc
        required = {"abi", "status", "exit_code", "command", "data", "issues"}
        if not isinstance(value, dict) or set(value) != required:
            raise CoreFailure("core_protocol_error", data=diagnostic)
        status_codes = {"ok": 0, "usage_error": 2, "validation_error": 3, "busy": 5, "io_error": 6}
        if (
            not isinstance(value.get("abi"), str) or value.get("command") != command
            or not isinstance(value.get("status"), str)
            or type(value.get("exit_code")) is not int
            or status_codes.get(value["status"]) != value["exit_code"]
            or not isinstance(value.get("data"), dict)
            or not isinstance(value.get("issues"), list)
            or any(not isinstance(item, dict) or not isinstance(item.get("code"), str)
                   or not isinstance(item.get("message"), str) for item in value["issues"])
        ):
            raise CoreFailure("core_protocol_error", data=diagnostic)
        if value["abi"] != ABI:
            if _preflight:
                self._version_mismatch(value["abi"], value["data"].get("version", "unknown"))
            raise CoreFailure("core_protocol_error", data={**diagnostic, "actual_abi": value["abi"]})
        if value["status"] != "ok" and not allow_invalid:
            first = value["issues"][0]["code"] if value["issues"] else "core_contract_rejected"
            raise CoreFailure(first, status=value["status"],
                              data={**diagnostic, "core_command": command, "core_issues": value["issues"]})
        return value

    @staticmethod
    def _workspace_request(path: Path, contract: str) -> dict[str, str]:
        return {"path": str(Path(os.path.abspath(path))), "contract": contract}

    def workspace_inspect(self, path: Path, contract: str) -> dict[str, Any]:
        return self.invoke("collaborative_workspace.inspect", self._workspace_request(path, contract))["data"]

    def workspace_validate(self, path: Path, contract: str, *, allow_invalid: bool = False) -> dict[str, Any]:
        return self.invoke("collaborative_workspace.validate", self._workspace_request(path, contract),
                           allow_invalid=allow_invalid)

    def workspace_stage_complete(self, path: Path, contract: str) -> dict[str, Any]:
        return self.invoke("collaborative_workspace.stage.complete",
                           self._workspace_request(path, contract))["data"]

    def knowledge_unit_validate(self, path: Path, *, allow_invalid: bool = False) -> dict[str, Any]:
        private = ["record.json"] if (path / "record.json").is_file() and not (path / "record.json").is_symlink() else []
        return self.invoke("validate", {"path": str(Path(os.path.abspath(path))), "private_root_files": private},
                           allow_invalid=allow_invalid)

    def knowledge_unit_stage_complete(self, path: Path) -> dict[str, Any]:
        return self.invoke(
            "stage.complete",
            {"path": str(Path(os.path.abspath(path))), "private_root_files": []},
        )["data"]


__all__ = ["ABI", "INNER_CONTRACT", "OUTER_CONTRACT", "CoreFailure", "CoreRunner", "RUNNER_ENV", "EXPECTED_CORE_VERSION", "set_default_runner"]
