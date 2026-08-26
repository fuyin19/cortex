from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]


def _packaged(skill: str, *args: str) -> subprocess.CompletedProcess[str]:
    skill_root = ROOT / "skills" / skill
    environment = dict(os.environ)
    environment["CORTEX_PYTHON"] = os.path.abspath(sys.executable)
    return subprocess.run(
        [sys.executable, "-I", str(skill_root / "scripts" / "run_cortex.py"), *args],
        cwd=skill_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_sc027_actual_producer_to_packaged_cortex(tmp_path: Path) -> None:
    configured = os.environ.get("FILE_PROCESSING_WORKTREE")
    if not configured:
        pytest.skip("set FILE_PROCESSING_WORKTREE for the cross-repository acceptance")
    producer_root = Path(configured)
    producer = producer_root / "skills" / "markdown-conversion" / "scripts" / "pipeline.py"
    assert producer.is_file()

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for index, (name, message) in enumerate(
        (("ReCoRd.md", "record.json"), (".CoRtEx-item.md", "reserved Cortex name"))
    ):
        rejected_source = tmp_path / name
        rejected_source.write_bytes(b"# rejected before conversion\n")
        rejected_output = tmp_path / f"rejected-{index}"
        rejected = subprocess.run(
            [
                sys.executable,
                str(producer),
                "--input",
                str(rejected_source),
                "--output-mode",
                "bundle",
                "--output-dir",
                str(rejected_output),
                "--overwrite",
                "--timestamp",
                "2026-08-26T00:00:00Z",
            ],
            cwd=producer_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert rejected.returncode == 1
        assert message in rejected.stderr
        assert not rejected_output.exists()

    source = tmp_path / "memo.md"
    source.write_bytes(b"# actual producer\n")
    output = tmp_path / "produced"
    produced = subprocess.run(
        [
            sys.executable,
            str(producer),
            "--input",
            str(source),
            "--output-mode",
            "bundle",
            "--output-dir",
            str(output),
            "--timestamp",
            "2026-08-26T00:00:00Z",
        ],
        cwd=producer_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert produced.returncode == 0, produced.stderr
    conversion = output / "memo"
    assert hashlib.sha256((conversion / "AGENTS.md").read_bytes()).hexdigest() == (
        "2067837a839ba3a9a452504a1f85bcff738eb7a181a77458105a8096a33f1bcc"
    )

    workspace = tmp_path / "bundle"
    initialized = _packaged("cortex-kb-build", "--json", "--workspace", str(workspace), "manage", "init")
    assert initialized.returncode == 0, initialized.stdout
    profiles = {
        "tags": {
            "version": 2,
            "groups": [{"name": "project", "tags": [{"tag": "project-alpha", "description": "Alpha"}]}],
        },
        "layout": {
            "version": 5,
            "partition_tag_group": "project",
            "partition_name_strategy": "tag",
            "unit_name_strategy": "tag-title-date",
            "max_component_length": 96,
            "duplicate_name_strategy": "reject",
        },
    }
    for name, value in profiles.items():
        operand = tmp_path / f"{name}.json"
        operand.write_text(json.dumps(value), encoding="utf-8")
        configured_result = _packaged(
            "cortex-kb-build",
            "--json",
            "--workspace",
            str(workspace),
            "manage",
            "config",
            "set",
            "--profile",
            name,
            "--file",
            str(operand),
        )
        assert configured_result.returncode == 0, configured_result.stdout

    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "title": "Actual producer",
                "timestamp": "2026-08-26T00:00:00Z",
                "tags": ["project-alpha"],
            }
        ),
        encoding="utf-8",
    )
    before_rejections = {
        path.relative_to(workspace).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in workspace.rglob("*")
    }
    reserved_inputs = []
    reserved_root = tmp_path / "reserved-root"
    reserved_root.mkdir()
    (reserved_root / ".CoRtEx-item.md").write_bytes(b"reserved root representation\n")
    reserved_inputs.append(reserved_root)
    reserved_asset = tmp_path / "reserved-asset"
    (reserved_asset / "assets/.CORTEX").mkdir(parents=True)
    (reserved_asset / "memo.md").write_bytes(b"ordinary representation\n")
    (reserved_asset / "assets/.CORTEX/payload.bin").write_bytes(b"reserved payload\n")
    reserved_inputs.append(reserved_asset)
    for conversion_input in reserved_inputs:
        rejected_by_cortex = _packaged(
            "cortex-kb-ingest",
            "--json",
            "--workspace",
            str(workspace),
            "record",
            "add",
            "--conversion",
            str(conversion_input),
            "--metadata",
            str(metadata),
        )
        assert rejected_by_cortex.returncode == 3
        assert json.loads(rejected_by_cortex.stdout)["issues"][0]["code"] == "reserved_cortex_name"
        assert before_rejections == {
            path.relative_to(workspace).as_posix(): None if path.is_dir() else path.read_bytes()
            for path in workspace.rglob("*")
        }

    added = _packaged(
        "cortex-kb-ingest",
        "--json",
        "--workspace",
        str(workspace),
        "record",
        "add",
        "--conversion",
        str(conversion),
        "--metadata",
        str(metadata),
    )
    assert added.returncode == 0, added.stdout
    result = json.loads(added.stdout)
    unit = workspace / result["data"]["partition"] / result["data"]["record"]
    assert (unit / "record.json").is_file()
    assert (unit / "memo.md").read_bytes().startswith(b"---\n")
    assert (unit / "src" / "memo.md").read_bytes() == source.read_bytes()
    assert (unit / "AGENTS.md").read_bytes() == (conversion / "AGENTS.md").read_bytes()
