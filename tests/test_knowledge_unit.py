from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cortex.cli import main
from cortex.jsonio import json_bytes
from cortex.knowledge_unit import AGENTS_SHA256, CLAUDE_SHA256, navigation_bytes, validate_representation_names
from cortex.errors import CortexError
from cortex.validation import validate_workspace


PARTITION = "project-alpha"


def _write(path: Path, value: dict) -> Path:
    path.write_bytes(json_bytes(value))
    return path


def _invoke(capsys, *args: str):
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


def _bundle(tmp_path: Path, capsys) -> Path:
    root = tmp_path / "bundle"
    assert _invoke(capsys, "--workspace", str(root), "manage", "init")[0] == 0
    tags = {"version": 2, "groups": [{"name": "project", "tags": [{"tag": PARTITION, "description": "project"}]}]}
    layout = {"version": 5, "partition_tag_group": "project", "partition_name_strategy": "tag", "unit_name_strategy": "tag-title-date", "max_component_length": 96, "duplicate_name_strategy": "reject"}
    for name, value in (("tags", tags), ("layout", layout)):
        assert _invoke(capsys, "--workspace", str(root), "manage", "config", "set", "--profile", name, "--file", str(_write(tmp_path / f"{name}.json", value)))[0] == 0
    return root


def _metadata(tmp_path: Path, title: str) -> Path:
    return _write(tmp_path / f"{title}.json", {"title": title, "timestamp": "2026-08-26T00:00:00Z", "tags": [PARTITION]})


def _unit(bundle: Path, result: dict) -> Path:
    return bundle / result["data"]["partition"] / result["data"]["record"]


def test_sc001_exact_cross_repo_navigation_contract():
    agents, claude = navigation_bytes()
    assert len(agents) == 1695 and hashlib.sha256(agents).hexdigest() == AGENTS_SHA256
    assert claude == b"@AGENTS.md\n" and hashlib.sha256(claude).hexdigest() == CLAUDE_SHA256


def test_sc008_casefold_representation_contract_is_platform_independent():
    try:
        validate_representation_names(["memo.md", "memo.MD"])
    except CortexError as exc:
        assert exc.code == "representation_name_collision"
    else:
        raise AssertionError("case-folding collision was accepted")


def test_sc017_source_only_is_one_root_representation_with_empty_support_dirs(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    source = tmp_path / "native.pdf"; source.write_bytes(b"opaque native bytes")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--metadata", str(_metadata(tmp_path, "Source only")))
    assert code == 0
    unit = _unit(bundle, result)
    assert (unit / "native.pdf").read_bytes() == source.read_bytes()
    assert (unit / "src/.keep").read_bytes() == b"" and (unit / "assets/.keep").read_bytes() == b""
    assert validate_workspace(bundle).valid


def test_sc018_conversion_only_fills_missing_envelope(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    conversion = tmp_path / "conversion"; conversion.mkdir(); (conversion / "memo.md").write_bytes(b"# memo\n")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Conversion only")))
    assert code == 0
    unit = _unit(bundle, result)
    assert (unit / "memo.md").read_bytes() == b"# memo\n"
    assert (unit / "src/.keep").is_file() and (unit / "assets/.keep").is_file()
    assert validate_workspace(bundle).valid


def test_sc019_both_fills_empty_src_and_requires_exact_retained_source(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    source = tmp_path / "original.docx"; source.write_bytes(b"source bytes")
    conversion = tmp_path / "conversion"; conversion.mkdir(); (conversion / "memo.md").write_bytes(b"memo")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Both")))
    assert code == 0 and (_unit(bundle, result) / "src/original.docx").read_bytes() == source.read_bytes()
    mismatch = tmp_path / "mismatch"; mismatch.mkdir(); (mismatch / "other.md").write_bytes(b"memo"); (mismatch / "src").mkdir(); (mismatch / "src/original.docx").write_bytes(b"different")
    before = sorted(path.relative_to(bundle).as_posix() for path in bundle.rglob("*"))
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--source", str(source), "--conversion", str(mismatch), "--metadata", str(_metadata(tmp_path, "Mismatch")))
    assert code == 3 and result["issues"][0]["code"] == "conversion_source_mismatch"
    assert before == sorted(path.relative_to(bundle).as_posix() for path in bundle.rglob("*"))


def test_sc020_record_json_collision_and_guide_tamper_fail_closed(tmp_path, capsys):
    bundle = _bundle(tmp_path, capsys)
    conversion = tmp_path / "collision"; conversion.mkdir(); (conversion / "memo.md").write_bytes(b"memo"); (conversion / "record.json").write_bytes(b"{}\n")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--conversion", str(conversion), "--metadata", str(_metadata(tmp_path, "Collision")))
    assert code == 3 and result["issues"][0]["code"] == "reserved_record_metadata"
    clean = tmp_path / "clean"; clean.mkdir(); (clean / "memo.md").write_bytes(b"memo"); agents, _ = navigation_bytes(); (clean / "AGENTS.md").write_bytes(agents + b"tamper")
    code, result = _invoke(capsys, "--workspace", str(bundle), "record", "add", "--conversion", str(clean), "--metadata", str(_metadata(tmp_path, "Tamper")))
    assert code == 3 and result["issues"][0]["code"] == "navigation_guide_mismatch"
