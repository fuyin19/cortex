"""Test-only JSONL protocol stub; anti-entropy Core owns envelope semantics."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


for raw in sys.stdin.buffer:
    frame = json.loads(raw.decode("utf-8")); command = frame["command"]; request = frame["request"]
    log = os.environ.get("FAKE_CORE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
    root = Path(request.get("path", ".")); fail = os.environ.get("FAKE_CORE_FAIL") == command
    invalid = command == "validate" and (root / ".core-invalid").exists()
    if command in {"repair", "stage.complete"} and not fail:
        (root / ".core-invalid").unlink(missing_ok=True)
        (root / "AGENTS.md").write_bytes(b"fake protocol fixture\n")
        (root / "CLAUDE.md").write_bytes(b"@AGENTS.md\n")
        for name in ("assets", "src"):
            support = root / name; support.mkdir(exist_ok=True)
            if not any(support.iterdir()): (support / ".keep").write_bytes(b"")
    problems = [{"code": "fake_core_rejected", "message": "Injected fake Core rejection"}] if fail or invalid else []
    result = {"status": "validation_error" if problems else "ok", "abi": "anti-entropy-core.runner/v1",
              "command": command, "exit_code": 3 if problems else 0, "issues": problems, "data": {"version": "1.2.1"} if command == "capabilities" else {}}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n"); sys.stdout.flush()
