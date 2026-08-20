#!/usr/bin/env python3
"""Noninstalled, nonpublic one-shot legacy-direct-unit to Cortex 6 builder.

This tool never mutates its source, performs cutover, or changes a Registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

_EXPECTED_VERSION = "6.0.0"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_SRC = (_REPOSITORY_ROOT / "src").resolve()
_EXPECTED_PACKAGE = (_REPOSITORY_SRC / "cortex").resolve()
_already_loaded = sys.modules.get("cortex")
if _already_loaded is not None:
    _loaded_file = getattr(_already_loaded, "__file__", None)
    if _loaded_file is None or Path(_loaded_file).resolve().parent != _EXPECTED_PACKAGE:
        raise RuntimeError("Refusing an ambient cortex package; run the repository-bound migration script in a clean process")
sys.path.insert(0, str(_REPOSITORY_SRC))

import cortex as _cortex_package
from cortex.constants import RECORD_FIELDS, RECORD_SCHEMA
from cortex.constants import VERSION as CORTEX_VERSION
from cortex.errors import CortexError, issue, validation_error
from cortex.jsonio import json_bytes, loads_object
from cortex.naming import require_naming_runtime, tag_title_date_name
from cortex.native import exists, is_reparse_metadata, native_path, reject_reparse_ancestry, rename_no_replace, require_real_directory, require_safe_component
from cortex.profiles import registered_tags, tag_groups, validate_layout_profile, validate_record, validate_tags_profile
from cortex.validation import validate_workspace

PLAN_VERSION = 1
_STAGE_ALLOCATION_ATTEMPTS = 8


def _require_repository_cortex() -> None:
    package_file = getattr(_cortex_package, "__file__", None)
    if package_file is None or Path(package_file).resolve().parent != _EXPECTED_PACKAGE:
        raise RuntimeError("Migration script is not bound to this repository's sibling src/cortex package")
    if CORTEX_VERSION != _EXPECTED_VERSION:
        raise RuntimeError(f"Migration script requires Cortex {_EXPECTED_VERSION}, found {CORTEX_VERSION}")


_require_repository_cortex()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        while block := stream.read(1024 * 1024): digest.update(block)
    return digest.hexdigest()


def _read_profile_operand(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    path = Path(os.path.abspath(path))
    try:
        reject_reparse_ancestry(path.parent)
        meta = os.lstat(native_path(path))
    except CortexError as exc:
        if exc.code == "reparse_path":
            raise
        raise validation_error("Profile operand could not be inspected", "profile_operand_unreadable", path=label) from exc
    except OSError as exc:
        raise validation_error("Profile operand could not be inspected", "profile_operand_unreadable", path=label, error_type=type(exc).__name__) from exc
    if is_reparse_metadata(meta):
        raise validation_error("Profile operand must not be a link or reparse point", "reparse_path", path=label)
    if not stat.S_ISREG(meta.st_mode):
        raise validation_error("Profile operand must be an ordinary file", "profile_operand_not_ordinary", path=label)
    try:
        with open(native_path(path), "rb") as stream:
            raw = stream.read()
    except OSError as exc:
        raise validation_error("Profile operand could not be read", "profile_operand_unreadable", path=label, error_type=type(exc).__name__) from exc
    return loads_object(raw, label=label), raw


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    """Read an already no-follow-preflighted legacy record JSON file."""
    try:
        with open(native_path(path), "rb") as stream:
            raw = stream.read()
    except OSError as exc:
        raise validation_error("Legacy record JSON could not be read", "migration_read_error", path=str(path), error_type=type(exc).__name__) from exc
    return loads_object(raw, label=str(path)), raw


def _allocate_owned_stage(parent: Path) -> tuple[Path, tuple[int, int]]:
    for _attempt in range(_STAGE_ALLOCATION_ATTEMPTS):
        candidate = parent / f".cortex-mig-{uuid.uuid4().hex}"
        try:
            os.mkdir(native_path(candidate))
        except FileExistsError:
            continue
        except OSError as exc:
            raise validation_error("Migration stage could not be allocated", "migration_stage_allocation_failed", path=str(candidate), error_type=type(exc).__name__) from exc
        meta = os.lstat(native_path(candidate))
        if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode):
            raise validation_error("Created migration stage is not an owned real directory", "migration_stage_identity_invalid", path=str(candidate))
        return candidate, (meta.st_dev, meta.st_ino)
    raise validation_error("Migration stage allocation collided repeatedly", "migration_stage_collision", path=str(parent))


def _cleanup_owned_stage(stage: Path, identity: tuple[int, int]) -> None:
    if not exists(stage):
        return
    try:
        meta = os.lstat(native_path(stage))
    except OSError as exc:
        raise validation_error("Owned migration stage could not be inspected for cleanup", "migration_stage_cleanup_failed", path=str(stage), error_type=type(exc).__name__) from exc
    if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode) or (meta.st_dev, meta.st_ino) != identity:
        raise validation_error("Migration stage identity changed; cleanup refused", "migration_stage_identity_changed", path=str(stage))
    try:
        shutil.rmtree(native_path(stage))
    except OSError as exc:
        raise validation_error("Owned migration stage could not be cleaned", "migration_stage_cleanup_failed", path=str(stage), error_type=type(exc).__name__) from exc


def _require_real_directory_no_follow(path: Path, label: str) -> None:
    meta = os.lstat(native_path(path))
    if is_reparse_metadata(meta):
        raise validation_error("Migration source contains a link or reparse point", "reparse_path", path=label)
    if not stat.S_ISDIR(meta.st_mode):
        raise validation_error("Migration source entry must be a real directory", "real_directory_required", path=label)


def _require_regular_no_follow(path: Path, label: str) -> None:
    meta = os.lstat(native_path(path))
    if is_reparse_metadata(meta):
        raise validation_error("Migration source contains a link or reparse point", "reparse_path", path=label)
    if not stat.S_ISREG(meta.st_mode):
        raise validation_error("Migration source entry must be an ordinary file", "ordinary_file_required", path=label)


def _safe_entries(directory: Path, label: str) -> list[os.DirEntry[str]]:
    _require_real_directory_no_follow(directory, label)
    try:
        entries = sorted(os.scandir(native_path(directory)), key=lambda entry: entry.name.encode("utf-8", "strict"))
    except UnicodeEncodeError as exc:
        raise validation_error("Migration source contains a name that is not strict UTF-8", "unsafe_component", path=label) from exc
    for entry in entries:
        relative = entry.name if label == "." else f"{label}/{entry.name}".strip("/")
        require_safe_component(entry.name, label=relative)
        meta = entry.stat(follow_symlinks=False)
        if is_reparse_metadata(meta):
            raise validation_error("Migration source contains a link or reparse point", "reparse_path", path=relative)
        if not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)):
            raise validation_error("Migration source contains a nonregular entry", "nonregular_entry", path=relative)
    return entries


def _safe_walk(root: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    def walk(directory: Path, prefix: str = "") -> None:
        entries = _safe_entries(directory, prefix or ".")
        for entry in entries:
            rel = f"{prefix}/{entry.name}" if prefix else entry.name
            meta = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(meta.st_mode):
                output.append({"path": rel, "type": "directory"})
                walk(Path(entry.path), rel)
            elif stat.S_ISREG(meta.st_mode):
                output.append({"path": rel, "type": "file", "size": meta.st_size, "sha256": _sha(Path(entry.path))})
    walk(root)
    return output


def _copy_tree_no_follow(source: Path, destination: Path) -> None:
    """Copy a tree only after no-follow validation of each current entry."""
    for item in _safe_walk(source):
        target = destination.joinpath(*item["path"].split("/"))
        current = source.joinpath(*item["path"].split("/"))
        meta = os.lstat(native_path(current))
        if is_reparse_metadata(meta):
            raise validation_error("Migration payload changed to a reparse point", "migration_source_drift", path=item["path"])
        if item["type"] == "directory":
            if not stat.S_ISDIR(meta.st_mode): raise validation_error("Migration payload type changed", "migration_source_drift", path=item["path"])
            target.mkdir()
        else:
            if not stat.S_ISREG(meta.st_mode): raise validation_error("Migration payload type changed", "migration_source_drift", path=item["path"])
            with open(native_path(current), "rb") as incoming, open(native_path(target), "xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def _copy_regular_no_follow(source: Path, destination: Path) -> None:
    meta = os.lstat(native_path(source))
    if is_reparse_metadata(meta) or not stat.S_ISREG(meta.st_mode):
        raise validation_error("Migration source file changed type", "migration_source_drift", path=str(source))
    with open(native_path(source), "rb") as incoming, open(native_path(destination), "xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)


def _body_bytes(body: dict[str, Any]) -> bytes:
    return (json.dumps(body, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n").encode("utf-8", "strict")


def plan_legacy(source: Path, tags_path: Path, layout_path: Path) -> tuple[dict[str, Any], bytes, str]:
    _require_repository_cortex()
    source = Path(os.path.abspath(source))
    reject_reparse_ancestry(source)
    require_real_directory(source, code="migration_source_not_directory")
    tags, tags_raw = _read_profile_operand(tags_path, "--tags")
    layout, layout_raw = _read_profile_operand(layout_path, "--layout")
    problems = validate_tags_profile(tags) + validate_layout_profile(layout)
    canonical_tags = json_bytes(tags)
    canonical_layout = json_bytes(layout)
    if tags_raw != canonical_tags: problems.append(issue("noncanonical_profile_json", "Tag 2 input must be canonical Cortex JSON", path=str(tags_path)))
    if layout_raw != canonical_layout: problems.append(issue("noncanonical_profile_json", "Layout 3 input must be canonical Cortex JSON", path=str(layout_path)))
    try: require_naming_runtime()
    except CortexError as exc: problems.append(exc.as_issue())
    if layout.get("unit_name_tag_group") is None:
        raise validation_error("Migration requires a nonempty naming group", "bundle_not_operational", path="layout#/unit_name_tag_group")
    groups = tag_groups(tags) if not validate_tags_profile(tags) else {}
    group = layout.get("unit_name_tag_group")
    if group is not None and group not in groups:
        problems.append(issue("unknown_unit_name_tag_group", "Naming group is not present in Tag 2", path="layout#/unit_name_tag_group", group=group))
    manifest: list[dict[str, Any]] = []
    manifest_failed_unit: str | None = None
    try:
        manifest = _safe_walk(source)
    except CortexError as exc:
        problems.append(exc.as_issue())
        if exc.path:
            manifest_failed_unit = exc.path.removeprefix("./").split("/", 1)[0]
    mappings: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    try:
        units = sorted(os.scandir(native_path(source)), key=lambda e: e.name.encode("utf-8", "strict"))
    except OSError as exc:
        units = []
        problems.append(issue("directory_unreadable", "Migration source could not be listed", path=".", os_error=str(exc)))
    for unit in units:
        if unit.name == manifest_failed_unit:
            # The no-follow manifest pass has already recorded this unit's
            # failure. Never continue into its record bytes or descendants.
            continue
        unit_path = Path(unit.path)
        unit_issues: list[dict[str, Any]] = []
        try:
            meta = unit.stat(follow_symlinks=False)
            if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): raise validation_error("Legacy root entries must be real unit directories", "invalid_legacy_unit", path=unit.name)
            # Preflight the complete unit no-follow. Any unsafe entry aborts this
            # unit before record parsing, hashing, or more-specific descent.
            _safe_walk(unit_path)
            top = {entry.name: entry for entry in _safe_entries(unit_path, unit.name)}
            if not set(top) <= {"record.json", "original", "representations"} or not {"record.json", "original"} <= set(top):
                raise validation_error("Unit does not match observed legacy direct-unit grammar", "invalid_legacy_unit", path=unit.name)
            _require_regular_no_follow(unit_path / "record.json", f"{unit.name}/record.json")
            _require_real_directory_no_follow(unit_path / "original", f"{unit.name}/original")
            record, record_raw = _read_json(unit_path / "record.json")
            unit_issues.extend(validate_record(record, registered_tags(tags), label=f"{unit.name}/record.json"))
            if record_raw != json_bytes({field: record.get(field) for field in RECORD_FIELDS}):
                unit_issues.append(issue("noncanonical_record_json", "Legacy record.json must be canonical Cortex JSON", path=f"{unit.name}/record.json"))
            original = _safe_entries(unit_path / "original", f"{unit.name}/original")
            if len(original) != 1: raise validation_error("original must contain one ordinary file", "invalid_legacy_original", path=f"{unit.name}/original")
            original_path = Path(original[0].path)
            _require_regular_no_follow(original_path, f"{unit.name}/original/{original[0].name}")
            kind = "markdown-only"
            payload_root = None
            payload_proofs: list[dict[str, Any]] = [{"path": f"original/{original[0].name}", "sha256": _sha(original_path)}]
            if "representations" in top:
                _require_real_directory_no_follow(unit_path / "representations", f"{unit.name}/representations")
                reps = _safe_entries(unit_path / "representations", f"{unit.name}/representations")
                if len(reps) != 1 or reps[0].name != "markdown-conversion": raise validation_error("representations must contain only markdown-conversion", "invalid_legacy_representation", path=f"{unit.name}/representations")
                payload_root = Path(reps[0].path)
                _require_real_directory_no_follow(payload_root, f"{unit.name}/representations/markdown-conversion")
                children = {entry.name: entry for entry in _safe_entries(payload_root, f"{unit.name}/representations/markdown-conversion")}
                md = [n for n in children if n.casefold().endswith(".md")]
                js = [n for n in children if n.casefold().endswith(".json")]
                if any(name.casefold() == "record.json" for name in children):
                    raise validation_error("Converter payload must not contain reserved Cortex record.json", "reserved_record_metadata", path=f"{unit.name}/representations/markdown-conversion/record.json")
                if len(md) != 1 or len(js) != 1 or Path(md[0]).stem != Path(js[0]).stem or "src" not in children or not set(children) <= {md[0], js[0], "src", "assets"}:
                    raise validation_error("markdown-conversion has invalid full shape", "invalid_legacy_conversion", path=f"{unit.name}/representations/markdown-conversion")
                _require_regular_no_follow(payload_root / md[0], f"{unit.name}/representations/markdown-conversion/{md[0]}")
                _require_regular_no_follow(payload_root / js[0], f"{unit.name}/representations/markdown-conversion/{js[0]}")
                _require_real_directory_no_follow(payload_root / "src", f"{unit.name}/representations/markdown-conversion/src")
                if "assets" in children:
                    _require_real_directory_no_follow(payload_root / "assets", f"{unit.name}/representations/markdown-conversion/assets")
                src = _safe_entries(payload_root / "src", f"{unit.name}/representations/markdown-conversion/src")
                if len(src) != 1: raise validation_error("conversion src must contain one file", "invalid_legacy_conversion", path=f"{unit.name}/representations/markdown-conversion/src")
                _require_regular_no_follow(Path(src[0].path), f"{unit.name}/representations/markdown-conversion/src/{src[0].name}")
                if src[0].name != original[0].name or _sha(Path(src[0].path)) != _sha(original_path): raise validation_error("original and conversion src differ", "legacy_source_mismatch", path=unit.name)
                kind = "full"
                payload_proofs.extend({"path": f"representations/markdown-conversion/{item['path']}", **{key: value for key, value in item.items() if key != "path"}} for item in _safe_walk(payload_root))
            elif original_path.suffix.casefold() != ".md":
                raise validation_error("Legacy source-only unit must contain Markdown", "invalid_legacy_markdown_only", path=unit.name)
            if group is None or group not in groups: raise validation_error("Bundle naming is not operational", "bundle_not_operational", path=unit.name)
            choices = {entry["tag"] for entry in groups[group]}
            selected = [tag for tag in record.get("tags", []) if tag in choices]
            if len(selected) != 1: raise validation_error("Record must select exactly one naming tag", "unit_name_tag_count", path=unit.name, tags=selected)
            target = tag_title_date_name(selected[0], record["title"], record["timestamp"], layout["max_component_length"])
            folded = target.casefold()
            if folded in names: raise validation_error("Migration targets collide under case folding", "record_casefold_collision", path=unit.name, names=[names[folded], target])
            names[folded] = target
            mappings.append({"source_unit": unit.name, "target_unit": target, "kind": kind, "record_sha256": hashlib.sha256(record_raw).hexdigest(), "payload_proofs": payload_proofs})
        except (CortexError, OSError, UnicodeError) as exc:
            unit_issues.append(exc.as_issue() if isinstance(exc, CortexError) else issue("migration_read_error", "Legacy unit could not be planned", path=unit.name, os_error=str(exc)))
        problems.extend(sorted(unit_issues, key=lambda x: (x.get("path", ""), x["code"])))
    mappings.sort(key=lambda item: item["source_unit"].encode("utf-8"))
    problems.sort(key=lambda x: (x.get("path", ""), x["code"], x["message"]))
    body = {
        "version": PLAN_VERSION,
        "source": str(source),
        "source_manifest": manifest,
        "config_digests": {"record_schema_sha256": hashlib.sha256(json_bytes(RECORD_SCHEMA)).hexdigest(), "tags_sha256": hashlib.sha256(canonical_tags).hexdigest(), "layout_sha256": hashlib.sha256(canonical_layout).hexdigest()},
        "profiles": {"record": RECORD_SCHEMA, "tags": tags, "layout": layout},
        "counts": {"total": len(mappings), "full": sum(m["kind"] == "full" for m in mappings), "markdown_only": sum(m["kind"] == "markdown-only" for m in mappings)},
        "mappings": mappings,
        "issues": problems,
    }
    encoded = _body_bytes(body)
    return body, encoded, hashlib.sha256(encoded).hexdigest()


def build_legacy(source: Path, output: Path, tags_path: Path, layout_path: Path, expected_digest: str) -> dict[str, Any]:
    _require_repository_cortex()
    source = Path(os.path.abspath(source)); output = Path(os.path.abspath(output))
    body, encoded, digest = plan_legacy(source, tags_path, layout_path)
    if digest != expected_digest: raise validation_error("Approved plan digest does not match current source/config", "migration_plan_digest_mismatch", expected=expected_digest, actual=digest)
    if body["issues"]: raise validation_error("Migration plan contains issues", "migration_plan_invalid", issues=body["issues"])
    if exists(output): raise validation_error("Migration output must be absent", "migration_output_exists", path=str(output))
    require_safe_component(output.name, label=output.name)
    parent = output.parent; reject_reparse_ancestry(parent); require_real_directory(parent, code="migration_output_parent_invalid")
    source_real = os.path.normcase(os.path.realpath(source)); output_real = os.path.normcase(os.path.realpath(output))
    if output_real == source_real or output_real.startswith(source_real + os.sep) or source_real.startswith(output_real + os.sep): raise validation_error("Source and output must be disjoint", "migration_path_overlap")
    parent_identity = (os.stat(native_path(parent)).st_dev, os.stat(native_path(parent)).st_ino)
    source_identity = (os.stat(native_path(source)).st_dev, os.stat(native_path(source)).st_ino)
    stage, stage_identity = _allocate_owned_stage(parent)
    try:
        profiles = stage / "profiles"; profiles.mkdir()
        (profiles / "record-schema.json").write_bytes(json_bytes(RECORD_SCHEMA))
        (profiles / "tags.json").write_bytes(json_bytes(body["profiles"]["tags"]))
        (profiles / "layout.json").write_bytes(json_bytes(body["profiles"]["layout"]))
        for mapping in body["mappings"]:
            old = source / mapping["source_unit"]; new = stage / mapping["target_unit"]; new.mkdir()
            _copy_regular_no_follow(old / "record.json", new / "record.json")
            if mapping["kind"] == "full":
                _copy_tree_no_follow(old / "representations" / "markdown-conversion", new)
            else:
                original = next(os.scandir(native_path(old / "original")))
                _copy_regular_no_follow(Path(original.path), new / original.name)
        report = validate_workspace(stage)
        if not report.valid: raise validation_error("Built migration output does not validate", "migration_build_invalid", issues=report.issues)
        current_body, current_encoded, current_digest = plan_legacy(source, tags_path, layout_path)
        if current_digest != digest or current_encoded != encoded: raise validation_error("Source/config drifted before publish", "migration_source_drift")
        current_identity = (os.stat(native_path(parent)).st_dev, os.stat(native_path(parent)).st_ino)
        current_source_identity = (os.stat(native_path(source)).st_dev, os.stat(native_path(source)).st_ino)
        if current_source_identity != source_identity: raise validation_error("Migration source identity changed", "migration_source_drift")
        if current_identity != parent_identity or os.path.normcase(os.path.realpath(output)) != output_real: raise validation_error("Output parent identity changed", "migration_output_parent_changed")
        rename_no_replace(stage, output)
        return {"output": str(output), "plan_sha256": digest, "counts": body["counts"]}
    finally:
        _cleanup_owned_stage(stage, stage_identity)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "build"):
        command = sub.add_parser(name); command.add_argument("--source", required=True); command.add_argument("--tags", required=True); command.add_argument("--layout", required=True)
        if name == "build": command.add_argument("--output", required=True); command.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            body, _encoded, digest = plan_legacy(Path(args.source), Path(args.tags), Path(args.layout))
            if body["issues"]:
                raise validation_error("Migration plan contains issues", "migration_plan_invalid", issues=body["issues"])
            print(json.dumps({"body": body, "plan_sha256": digest}, ensure_ascii=False, indent=2))
        else: print(json.dumps(build_legacy(Path(args.source), Path(args.output), Path(args.tags), Path(args.layout), args.plan_sha256), ensure_ascii=False, indent=2))
        return 0
    except CortexError as exc:
        print(json.dumps({"status": exc.status.value, "issues": exc.details.get("issues", [exc.as_issue()])}, ensure_ascii=True))
        return int(exc.status.exit_code)


if __name__ == "__main__": raise SystemExit(main())
