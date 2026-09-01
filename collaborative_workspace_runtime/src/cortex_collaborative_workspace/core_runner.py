"""Strict subprocess adapter for anti-entropy Core's workspace contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ABI = "anti-entropy-core.runner/v1"
RUNNER_ENV = "ANTI_ENTROPY_CORE_RUNNER"
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
        raw = configured if configured is not None else os.environ.get(RUNNER_ENV)
        if not raw:
            raise CoreFailure("core_runner_required", status="usage_error")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise CoreFailure("core_runner_not_absolute", status="usage_error")
        self.path = Path(os.path.abspath(candidate))

    def invoke(self, command: str, request: dict[str, Any], *, allow_invalid: bool = False) -> dict[str, Any]:
        wire = (json.dumps({"command": command, "request": request}, ensure_ascii=False,
                           separators=(",", ":")) + "\n").encode("utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(self.path)], input=wire,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CoreFailure("core_runner_start_failed") from exc
        if completed.returncode != 0:
            raise CoreFailure("core_runner_process_failed", data={"process_exit_code": completed.returncode})
        try:
            value = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoreFailure("core_protocol_error") from exc
        required = {"abi", "status", "exit_code", "command", "data", "issues"}
        if not isinstance(value, dict) or set(value) != required:
            raise CoreFailure("core_protocol_error")
        status_codes = {"ok": 0, "usage_error": 2, "validation_error": 3, "busy": 5, "io_error": 6}
        if (
            value.get("abi") != ABI or value.get("command") != command
            or not isinstance(value.get("status"), str)
            or type(value.get("exit_code")) is not int
            or status_codes.get(value["status"]) != value["exit_code"]
            or not isinstance(value.get("data"), dict)
            or not isinstance(value.get("issues"), list)
            or any(not isinstance(item, dict) or not isinstance(item.get("code"), str)
                   or not isinstance(item.get("message"), str) for item in value["issues"])
        ):
            raise CoreFailure("core_protocol_error")
        if value["status"] != "ok" and not allow_invalid:
            first = value["issues"][0]["code"] if value["issues"] else "core_contract_rejected"
            raise CoreFailure(first, status=value["status"],
                              data={"core_command": command, "core_issues": value["issues"]})
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


__all__ = ["ABI", "INNER_CONTRACT", "OUTER_CONTRACT", "CoreFailure", "CoreRunner", "RUNNER_ENV"]
