"""Noninstalled, nonpublic Layout 3 -> 4 and Layout 4 -> 5 dispatcher.

The source is read-only. Build writes one absent candidate outside every
declared repository/KB boundary; this module has no cutover operation.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, stat, sys, uuid
from pathlib import Path
from typing import Any, Iterable

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path: sys.path.insert(0, str(_SRC))
from cortex.constants import RECORD_FIELDS, RECORD_SCHEMA, REGISTRY_FILENAME, ROOT_LOCK_FILENAME, VERSION  # noqa: E402
from cortex.errors import CortexError, issue, validation_error  # noqa: E402
from cortex.jsonio import json_bytes, loads_object  # noqa: E402
from cortex.knowledge_unit import finalize_staged, navigation_bytes  # noqa: E402
from cortex.naming import require_naming_runtime, tag_title_date_name  # noqa: E402
from cortex.native import exists, is_reparse_metadata, native_path, reject_reparse_ancestry, rename_no_replace, require_safe_component  # noqa: E402
from cortex.profiles import registered_tags, tag_groups, validate_record, validate_tags_profile  # noqa: E402
from cortex.registry import canonical_registry, validate_registry_value  # noqa: E402
from cortex.validation import validate_workspace  # noqa: E402

if VERSION != "8.0.0": raise RuntimeError(f"Layout migration requires repository Cortex 8.0.0, found {VERSION}")
LEGACY_KEYS = {"version", "unit_name_tag_group", "unit_name_strategy", "max_component_length", "duplicate_name_strategy"}
TARGET_KEYS = {"version", "partition_tag_group", "partition_name_strategy", "unit_name_strategy", "max_component_length", "duplicate_name_strategy"}

def _sha(value: bytes) -> str: return hashlib.sha256(value).hexdigest()

def _read_regular(path: Path, code: str) -> bytes:
    reject_reparse_ancestry(path.parent)
    try: meta = os.lstat(native_path(path))
    except OSError as exc: raise validation_error("Required file is unreadable", code, path=str(path), os_error=str(exc)) from exc
    if is_reparse_metadata(meta) or not stat.S_ISREG(meta.st_mode): raise validation_error("Required file must be ordinary", code, path=str(path))
    try: return path.read_bytes()
    except OSError as exc: raise validation_error("Required file is unreadable", code, path=str(path), os_error=str(exc)) from exc

def _json_file(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, code); return loads_object(raw, label=str(path)), raw

def _guarded_json(path: Path, code: str) -> tuple[dict[str, Any], bytes, list[dict[str, Any]], bool]:
    try:
        value, raw = _json_file(path, code); return value, raw, [], True
    except CortexError as exc:
        return {}, b"", exc.details.get("issues", [exc.as_issue()]), False

def _real_dir(path: Path, code: str) -> os.stat_result:
    reject_reparse_ancestry(path.parent)
    try: meta = os.lstat(native_path(path))
    except OSError as exc: raise validation_error("Directory is unreadable", code, path=str(path), os_error=str(exc)) from exc
    if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): raise validation_error("Real directory required", code, path=str(path))
    return meta

def _children(path: Path) -> list[os.DirEntry[str]]:
    try: return sorted(os.scandir(native_path(path)), key=lambda item: item.name.encode("utf-8", "strict"))
    except (OSError, UnicodeEncodeError) as exc: raise validation_error("Directory cannot be inventoried", "migration_source_unreadable", path=str(path), os_error=str(exc)) from exc

def _walk(root: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []; folded: dict[str, str] = {}
    def visit(directory: Path, prefix: str = "") -> None:
        for entry in _children(directory):
            require_safe_component(entry.name, label=entry.name)
            rel = f"{prefix}/{entry.name}" if prefix else entry.name; key = rel.casefold()
            if key in folded and folded[key] != rel: raise validation_error("Paths collide under case folding", "migration_casefold_collision", path=rel)
            folded[key] = rel; meta = entry.stat(follow_symlinks=False)
            if is_reparse_metadata(meta): raise validation_error("Links and reparse points are forbidden", "reparse_path", path=rel)
            if stat.S_ISDIR(meta.st_mode): output.append({"path": rel, "type": "directory"}); visit(Path(entry.path), rel)
            elif stat.S_ISREG(meta.st_mode):
                raw = _read_regular(Path(entry.path), "migration_source_unreadable"); output.append({"path": rel, "type": "file", "size": len(raw), "sha256": _sha(raw)})
            else: raise validation_error("Nonregular entry is forbidden", "nonregular_entry", path=rel)
    visit(root); return output

def _layout_problems(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if set(source) != LEGACY_KEYS or source.get("version") != 3 or source.get("unit_name_strategy") != "tag-title-date" or source.get("duplicate_name_strategy") != "reject": out.append(issue("invalid_source_layout", "Source must use exact operational Layout Profile 3"))
    if set(target) != TARGET_KEYS or target.get("version") != 4 or target.get("partition_name_strategy") != "tag" or target.get("unit_name_strategy") != "tag-title-date" or target.get("duplicate_name_strategy") != "reject": out.append(issue("invalid_target_layout", "Target must use exact Layout Profile 4"))
    for name, value in (("source", source.get("max_component_length")), ("target", target.get("max_component_length"))):
        if type(value) is not int or not 16 <= value <= 200: out.append(issue(f"invalid_{name}_layout", "max_component_length must be 16..200"))
    if not isinstance(source.get("unit_name_tag_group"), str) or not source.get("unit_name_tag_group"): out.append(issue("bundle_not_operational", "Source naming group must be configured"))
    if not isinstance(target.get("partition_tag_group"), str) or not target.get("partition_tag_group"): out.append(issue("bundle_not_operational", "Target partition group must be configured"))
    if source.get("unit_name_tag_group") != target.get("partition_tag_group"): out.append(issue("partition_group_mismatch", "Source naming group must equal target partition group"))
    return out

def _canonical_tags(value: dict[str, Any]) -> dict[str, Any]:
    groups = value.get("groups")
    if not isinstance(groups, list): return value
    canonical_groups: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("tags"), list): return value
        canonical_tags: list[dict[str, Any]] = []
        for item in group["tags"]:
            if not isinstance(item, dict): return value
            canonical_tags.append({"tag": item.get("tag"), "description": item.get("description")})
        canonical_groups.append({"name": group.get("name"), "tags": canonical_tags})
    return {"version": value.get("version"), "groups": canonical_groups}

def _canonical_legacy(value: dict[str, Any]) -> dict[str, Any]:
    return {"version": value.get("version"), "unit_name_tag_group": value.get("unit_name_tag_group"), "unit_name_strategy": value.get("unit_name_strategy"), "max_component_length": value.get("max_component_length"), "duplicate_name_strategy": value.get("duplicate_name_strategy")}

def _canonical_target(value: dict[str, Any]) -> dict[str, Any]:
    return {"version": value.get("version"), "partition_tag_group": value.get("partition_tag_group"), "partition_name_strategy": value.get("partition_name_strategy"), "unit_name_strategy": value.get("unit_name_strategy"), "max_component_length": value.get("max_component_length"), "duplicate_name_strategy": value.get("duplicate_name_strategy")}

def _operational_target_problems(tags: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    groups = tag_groups(tags); group = target.get("partition_tag_group"); maximum = target.get("max_component_length")
    if group not in groups:
        return [issue("unknown_partition_tag_group", "Target partition_tag_group must name an existing Tag 2 group", path="target-layout#/partition_tag_group", group=group)]
    try: require_naming_runtime()
    except CortexError as exc: problems.append(exc.as_issue()); return problems
    folded: dict[str, str] = {}
    for item in groups[group]:
        tag = item["tag"]
        try: require_safe_component(tag, allow_profiles=False, label=tag)
        except CortexError as exc: problems.append(exc.as_issue())
        if type(maximum) is int:
            if len(tag.encode("utf-8")) > maximum: problems.append(issue("partition_name_too_long", "Partition tag exceeds target component limit", path=tag))
            if len(tag.encode("utf-8")) + 11 > maximum: problems.append(issue("insufficient_unit_name_capacity", "Partition tag and date leave no title capacity", path=tag))
        key = tag.casefold()
        if key in folded and folded[key] != tag: problems.append(issue("partition_tag_casefold_collision", "Partition tags collide under case folding", path="profiles/tags.json", tags=[folded[key], tag]))
        folded[key] = tag
    return problems

def _kind(manifest: list[dict[str, Any]]) -> str:
    files = {x["path"] for x in manifest if x["type"] == "file"}; dirs = {x["path"] for x in manifest if x["type"] == "directory"}
    top = {x for x in files if "/" not in x}; md = [x for x in top - {"record.json"} if x.casefold().endswith(".md")]; js = [x for x in top - {"record.json"} if x.casefold().endswith(".json")]
    if len(md) == 1 and not js and not dirs and files == {"record.json", md[0]}: return "markdown-only"
    src = [x for x in files if x.startswith("src/") and x.count("/") == 1]
    allowed_dirs = dirs.issuperset({"src"}) and all(x == "src" or x == "assets" or x.startswith("assets/") for x in dirs)
    allowed_files = len(md) == len(js) == len(src) == 1 and Path(md[0]).stem == Path(js[0]).stem and all(x in {"record.json", md[0], js[0], src[0]} or x.startswith("assets/") for x in files)
    if allowed_dirs and allowed_files:
        if Path(src[0]).stem != Path(md[0]).stem: raise validation_error("Conversion Markdown/JSON and src file must share one stem", "conversion_source_stem_mismatch")
        return "full"
    raise validation_error("Unit is not an exact Layout 3 record unit", "invalid_record_shape")

def _plan_3_to_4(source: Path, target_layout_path: Path) -> tuple[dict[str, Any], bytes, str]:
    source = Path(os.path.abspath(source)); target_layout_path = Path(os.path.abspath(target_layout_path)); _real_dir(source, "migration_source_invalid")
    profiles = source / "profiles"; problems: list[dict[str, Any]] = []
    try: _real_dir(profiles, "migration_profile_directory_invalid"); profile_names = {entry.name for entry in _children(profiles)}
    except CortexError as exc: problems.extend(exc.details.get("issues", [exc.as_issue()])); profile_names = set()
    record, record_raw, record_read_issues, record_read_ok = _guarded_json(profiles / "record-schema.json", "migration_profile_invalid")
    tags, tags_raw, tags_read_issues, tags_read_ok = _guarded_json(profiles / "tags.json", "migration_profile_invalid")
    old, old_raw, old_read_issues, old_read_ok = _guarded_json(profiles / "layout.json", "migration_profile_invalid")
    target, target_raw, target_read_issues, target_read_ok = _guarded_json(target_layout_path, "migration_profile_invalid")
    problems.extend(record_read_issues + tags_read_issues + old_read_issues + target_read_issues)
    tag_problems = validate_tags_profile(tags) if tags_read_ok else []; layout_problems = _layout_problems(old, target) if old_read_ok and target_read_ok else []
    problems.extend(layout_problems + tag_problems)
    if tags_read_ok and old_read_ok and target_read_ok and not tag_problems and not layout_problems: problems.extend(_operational_target_problems(tags, target))
    for missing in sorted({"record-schema.json", "tags.json", "layout.json"} - profile_names): problems.append(issue("missing_profile", "Required source profile is missing", path=f"profiles/{missing}"))
    for extra in sorted(profile_names - {"record-schema.json", "tags.json", "layout.json"}): problems.append(issue("unexpected_profile", "Unexpected source profile entry", path=f"profiles/{extra}"))
    if record_read_ok and record != RECORD_SCHEMA: problems.append(issue("invalid_record_schema", "Source Record Profile 1 is invalid", path="profiles/record-schema.json"))
    if record_read_ok and record_raw != json_bytes(RECORD_SCHEMA): problems.append(issue("noncanonical_source_profile", "Source Record Profile 1 JSON is not canonical", path="profiles/record-schema.json"))
    if tags_read_ok and tags_raw != json_bytes(_canonical_tags(tags)): problems.append(issue("noncanonical_source_profile", "Source Tag Profile 2 JSON is not canonical", path="profiles/tags.json"))
    if old_read_ok and old_raw != json_bytes(_canonical_legacy(old)): problems.append(issue("noncanonical_source_profile", "Source Layout Profile 3 JSON is not canonical", path="profiles/layout.json"))
    if target_read_ok and target_raw != json_bytes(_canonical_target(target)): problems.append(issue("noncanonical_target_profile", "Target Layout Profile 4 JSON is not canonical", path=str(target_layout_path)))
    groups = tag_groups(tags) if tags_read_ok and not tag_problems else {}; choices = {x["tag"] for x in groups.get(old.get("unit_name_tag_group"), [])} if old_read_ok else set(); registered = registered_tags(tags) if tags_read_ok and not tag_problems else set()
    mappings: list[dict[str, Any]] = []; counts: dict[str, dict[str, int]] = {}; seen: set[tuple[str, str]] = set()
    for entry in _children(source):
        if entry.name == "profiles": continue
        unit_issues: list[dict[str, Any]] = []; manifest: list[dict[str, Any]] | None = None; kind: str | None = None; value: dict[str, Any] = {}; raw = b""; record_ok = False; partition: str | None = None
        try:
            require_safe_component(entry.name, allow_profiles=False, label=entry.name); meta = entry.stat(follow_symlinks=False)
            if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): raise validation_error("Unit must be a real directory", "invalid_record_unit", path=entry.name)
            unit = Path(entry.path)
        except CortexError as exc:
            problems.extend(exc.details.get("issues", [exc.as_issue()])); continue
        try: manifest = _walk(unit)
        except CortexError as exc: unit_issues.extend(exc.details.get("issues", [exc.as_issue()]))
        if manifest is not None:
            try: kind = _kind(manifest)
            except CortexError as exc: unit_issues.extend(exc.details.get("issues", [exc.as_issue()]))
        value, raw, record_read_issues, record_read_ok = _guarded_json(unit / "record.json", "invalid_record_metadata"); unit_issues.extend(record_read_issues)
        if record_read_ok:
            structural_registered = registered if tags_read_ok and not tag_problems else set(value.get("tags", [])) if isinstance(value.get("tags"), list) else set()
            bad = validate_record(value, structural_registered, label=f"{entry.name}/record.json")
            if bad: unit_issues.extend(bad)
            else: record_ok = True
            canonical_record = {field: value.get(field) for field in RECORD_FIELDS}
            if raw != json_bytes(canonical_record): unit_issues.append(issue("noncanonical_record_json", "record.json is not canonical Cortex JSON", path=f"{entry.name}/record.json"))
        if record_ok and tags_read_ok and not tag_problems and old_read_ok:
            selected = [x for x in value["tags"] if x in choices]
            if len(selected) != 1: unit_issues.append(issue("partition_tag_count", "Record must select exactly one partition tag", path=entry.name, tags=selected))
            else: partition = selected[0]
        if partition is not None:
            try: require_safe_component(partition, allow_profiles=False, label=partition)
            except CortexError as exc: unit_issues.extend(exc.details.get("issues", [exc.as_issue()]))
            if old_read_ok and old.get("unit_name_strategy") == "tag-title-date" and type(old.get("max_component_length")) is int and 16 <= old["max_component_length"] <= 200:
                expected_name = tag_title_date_name(partition, value["title"], value["timestamp"], old["max_component_length"])
                if entry.name != expected_name: unit_issues.append(issue("record_name_mismatch", "Source unit name does not match Layout 3 tag-title-date naming", path=entry.name, expected=expected_name, actual=entry.name))
            if target_read_ok and target.get("unit_name_strategy") == "tag-title-date" and type(target.get("max_component_length")) is int and 16 <= target["max_component_length"] <= 200:
                target_name = tag_title_date_name(partition, value["title"], value["timestamp"], target["max_component_length"])
                if entry.name != target_name: unit_issues.append(issue("target_record_name_mismatch", "Preserved unit name does not satisfy target Layout 4 naming", path=entry.name, expected=target_name, actual=entry.name))
            if target_read_ok and type(target.get("max_component_length")) is int and len(partition.encode("utf-8")) > target["max_component_length"]: unit_issues.append(issue("partition_name_too_long", "Partition exceeds target component limit", path=partition))
            key = (partition.casefold(), entry.name.casefold())
            if key in seen: unit_issues.append(issue("record_casefold_collision", "Target records collide under case folding", path=f"{partition}/{entry.name}"))
        if unit_issues or partition is None or kind is None or manifest is None:
            problems.extend(unit_issues); continue
        seen.add((partition.casefold(), entry.name.casefold())); pc = counts.setdefault(partition, {"total": 0, "full": 0, "markdown_only": 0}); pc["total"] += 1; pc["full" if kind == "full" else "markdown_only"] += 1
        mappings.append({"source_unit": entry.name, "partition": partition, "target_unit": entry.name, "kind": kind, "record_sha256": _sha(raw), "manifest": manifest})
    mappings.sort(key=lambda x: (x["partition"].encode(), x["target_unit"].encode()))
    body = {"format": "cortex-layout3-to-layout4-plan-v1", "source": str(source), "profiles": {"record": record, "tags": tags, "layout": target}, "profile_sha256": {"record": _sha(record_raw), "tags": _sha(tags_raw), "source_layout": _sha(old_raw), "target_layout": _sha(target_raw)}, "counts": {"partitions": len(counts), "total": len(mappings), "full": sum(x["kind"] == "full" for x in mappings), "markdown_only": sum(x["kind"] == "markdown-only" for x in mappings)}, "partition_counts": [{"partition": name, **counts[name]} for name in sorted(counts)], "mappings": mappings, "issues": problems}
    raw = json_bytes(body); return body, raw, _sha(raw)

def _layout45_problems(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    if set(source) != TARGET_KEYS or source.get("version") != 4:
        problems.append(issue("invalid_source_layout", "Source must use exact Layout Profile 4"))
    if set(target) != TARGET_KEYS or target.get("version") != 5:
        problems.append(issue("invalid_target_layout", "Target must use exact Layout Profile 5"))
    if not problems:
        for key in TARGET_KEYS - {"version"}:
            if source[key] != target[key]:
                problems.append(issue("layout_contract_change", "Layout 4 -> 5 may change only the version field", path=f"target-layout#/{key}"))
    return problems

def _layout4_unit(
    unit: Path,
    *,
    partition: str,
    tags: dict[str, Any],
    layout: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    problems: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    kind: str | None = None
    try:
        manifest = _walk(unit)
        kind = _kind(manifest)
    except CortexError as exc:
        problems.extend(exc.details.get("issues", [exc.as_issue()]))
        return problems, manifest, kind
    for item in manifest:
        components = item["path"].split("/")
        for component in components:
            folded = component.casefold()
            if folded in {".claude", ".cursor", "agents.md", "agents.override.md", "claude.md", "claude.local.md", ".cursorrules", ".mcp.json"}:
                problems.append(issue("instruction_control_path", "Layout 4 source contains a future instruction-control collision", path=f"{partition}/{unit.name}/{item['path']}"))
    record, raw, read_issues, ok = _guarded_json(unit / "record.json", "invalid_record_metadata")
    problems.extend(read_issues)
    if ok:
        registered = registered_tags(tags)
        problems.extend(validate_record(record, registered, label=f"{partition}/{unit.name}/record.json"))
        canonical = {field: record.get(field) for field in RECORD_FIELDS}
        if raw != json_bytes(canonical):
            problems.append(issue("noncanonical_record_json", "record.json is not canonical Cortex JSON", path=f"{partition}/{unit.name}/record.json"))
        groups = tag_groups(tags)
        selected_group = layout.get("partition_tag_group")
        choices = {item["tag"] for item in groups.get(selected_group, [])}
        selected = [tag for tag in record.get("tags", []) if tag in choices]
        if len(selected) != 1 or selected[0] != partition:
            problems.append(issue("partition_tag_mismatch", "Record must select exactly its Layout 4 partition tag", path=f"{partition}/{unit.name}"))
        else:
            try:
                expected = tag_title_date_name(partition, record["title"], record["timestamp"], layout["max_component_length"])
                if expected != unit.name:
                    problems.append(issue("record_name_mismatch", "Record folder does not match Layout 4 naming", path=f"{partition}/{unit.name}", expected=expected, actual=unit.name))
            except CortexError as exc:
                problems.append(exc.as_issue())
    return problems, manifest, kind

def _plan_4_to_5(source: Path, target_layout_path: Path) -> tuple[dict[str, Any], bytes, str]:
    source = Path(os.path.abspath(source)); target_layout_path = Path(os.path.abspath(target_layout_path)); _real_dir(source, "migration_source_invalid")
    profiles = source / "profiles"; problems: list[dict[str, Any]] = []
    try: _real_dir(profiles, "migration_profile_directory_invalid"); profile_names = {entry.name for entry in _children(profiles)}
    except CortexError as exc: problems.extend(exc.details.get("issues", [exc.as_issue()])); profile_names = set()
    record, record_raw, record_issues, record_ok = _guarded_json(profiles / "record-schema.json", "migration_profile_invalid")
    tags, tags_raw, tag_read_issues, tags_ok = _guarded_json(profiles / "tags.json", "migration_profile_invalid")
    old, old_raw, old_issues, old_ok = _guarded_json(profiles / "layout.json", "migration_profile_invalid")
    target, target_raw, target_issues, target_ok = _guarded_json(target_layout_path, "migration_profile_invalid")
    problems.extend(record_issues + tag_read_issues + old_issues + target_issues)
    for missing in sorted({"record-schema.json", "tags.json", "layout.json"} - profile_names): problems.append(issue("missing_profile", "Required source profile is missing", path=f"profiles/{missing}"))
    for extra in sorted(profile_names - {"record-schema.json", "tags.json", "layout.json"}): problems.append(issue("unexpected_profile", "Unexpected source profile entry", path=f"profiles/{extra}"))
    if record_ok and (record != RECORD_SCHEMA or record_raw != json_bytes(RECORD_SCHEMA)): problems.append(issue("invalid_record_schema", "Source Record Profile 1 must be exact canonical bytes", path="profiles/record-schema.json"))
    tag_problems = validate_tags_profile(tags) if tags_ok else []
    problems.extend(tag_problems)
    if tags_ok and not tag_problems and tags_raw != json_bytes(_canonical_tags(tags)): problems.append(issue("noncanonical_source_profile", "Source Tag Profile 2 JSON is not canonical", path="profiles/tags.json"))
    layout_problems = _layout45_problems(old, target) if old_ok and target_ok else []
    problems.extend(layout_problems)
    if old_ok and old_raw != json_bytes(_canonical_target(old)): problems.append(issue("noncanonical_source_profile", "Source Layout Profile 4 JSON is not canonical", path="profiles/layout.json"))
    if target_ok and target_raw != json_bytes(_canonical_target(target)): problems.append(issue("noncanonical_target_profile", "Target Layout Profile 5 JSON is not canonical", path=str(target_layout_path)))
    if tags_ok and target_ok and not tag_problems and not layout_problems: problems.extend(_operational_target_problems(tags, target))
    mappings: list[dict[str, Any]] = []; seen_partitions: dict[str, str] = {}; total = 0
    guides = assets_markers = src_markers = directory_additions = 0
    for partition_entry in _children(source):
        if partition_entry.name == "profiles": continue
        try:
            require_safe_component(partition_entry.name, allow_profiles=False, label=partition_entry.name)
            meta = partition_entry.stat(follow_symlinks=False)
            if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): raise validation_error("Partition must be a real directory", "invalid_partition", path=partition_entry.name)
            key = partition_entry.name.casefold()
            if key in seen_partitions: raise validation_error("Partitions collide under case folding", "partition_casefold_collision", path=partition_entry.name)
            seen_partitions[key] = partition_entry.name
        except CortexError as exc:
            problems.extend(exc.details.get("issues", [exc.as_issue()])); continue
        units = _children(Path(partition_entry.path))
        if not units: problems.append(issue("empty_partition", "Layout 4 partitions must be nonempty", path=partition_entry.name))
        seen_units: dict[str, str] = {}
        for unit_entry in units:
            total += 1
            unit = Path(unit_entry.path); unit_problems: list[dict[str, Any]] = []
            try:
                require_safe_component(unit_entry.name, allow_profiles=False, label=f"{partition_entry.name}/{unit_entry.name}")
                meta = unit_entry.stat(follow_symlinks=False)
                if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): raise validation_error("Record must be a real directory", "invalid_record_unit", path=f"{partition_entry.name}/{unit_entry.name}")
                key = unit_entry.name.casefold()
                if key in seen_units: raise validation_error("Records collide under case folding", "record_casefold_collision", path=f"{partition_entry.name}/{unit_entry.name}")
                seen_units[key] = unit_entry.name
            except CortexError as exc: unit_problems.extend(exc.details.get("issues", [exc.as_issue()]))
            if tags_ok and old_ok and not tag_problems and not layout_problems and not unit_problems:
                checked, manifest, kind = _layout4_unit(unit, partition=partition_entry.name, tags=tags, layout=old)
                unit_problems.extend(checked)
            else: manifest, kind = [], None
            if unit_problems or kind is None:
                problems.extend(unit_problems); continue
            paths = {item["path"] for item in manifest}
            additions = ["AGENTS.md", "CLAUDE.md"]
            added_directories: list[str] = []
            guides += 2
            if "assets" not in paths:
                additions.append("assets/.keep"); added_directories.append("assets"); assets_markers += 1; directory_additions += 1
            elif not any(path.startswith("assets/") for path in paths):
                additions.append("assets/.keep"); assets_markers += 1
            if "src" not in paths:
                additions.append("src/.keep"); added_directories.append("src"); src_markers += 1; directory_additions += 1
            elif not any(path.startswith("src/") for path in paths):
                additions.append("src/.keep"); src_markers += 1
            mappings.append({"partition": partition_entry.name, "record": unit_entry.name, "kind": kind, "manifest": manifest, "add_files": additions, "add_directories": added_directories})
    mappings.sort(key=lambda item: (item["partition"].encode("utf-8"), item["record"].encode("utf-8")))
    counts = {"partitions": len(seen_partitions), "total": len(mappings), "guide_files": guides, "assets_markers": assets_markers, "src_markers": src_markers, "files_added": guides + assets_markers + src_markers, "dirs_added": directory_additions}
    body = {"format": "cortex-layout4-to-layout5-plan-v1", "source": str(source), "profiles": {"record": record, "tags": tags, "layout": target}, "profile_sha256": {"record": _sha(record_raw), "tags": _sha(tags_raw), "source_layout": _sha(old_raw), "target_layout": _sha(target_raw)}, "counts": counts, "mappings": mappings, "issues": problems}
    raw = json_bytes(body); return body, raw, _sha(raw)

def plan_bundle(source: Path, target_layout_path: Path) -> tuple[dict[str, Any], bytes, str]:
    """Dispatch exactly one supported adjacent edge from the target version."""
    source_layout, _raw, _issues, readable = _guarded_json(
        Path(os.path.abspath(source)) / "profiles" / "layout.json",
        "migration_profile_invalid",
    )
    if readable and source_layout.get("version") == 4:
        return _plan_4_to_5(source, target_layout_path)
    return _plan_3_to_4(source, target_layout_path)

def _contains(root: Path, target: Path) -> bool:
    root = root.resolve(strict=False); target = target.resolve(strict=False); return target == root or root in target.parents

def _git_repository(path: Path) -> Path:
    current = Path(os.path.abspath(path))
    while True:
        marker = current / ".git"
        try: meta = os.lstat(native_path(marker))
        except FileNotFoundError: pass
        except OSError as exc: raise validation_error("Repository marker could not be inspected", "migration_repository_invalid", path=str(marker), os_error=str(exc)) from exc
        else:
            if is_reparse_metadata(meta) or not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)): raise validation_error("Repository marker must be ordinary", "migration_repository_invalid", path=str(marker))
            if stat.S_ISDIR(meta.st_mode):
                _read_regular(marker / "HEAD", "migration_repository_invalid")
            else:
                raw = _read_regular(marker, "migration_repository_invalid")
                try: text = raw.decode("utf-8", "strict").strip()
                except UnicodeDecodeError as exc: raise validation_error("Worktree .git marker must be strict UTF-8", "migration_repository_invalid", path=str(marker)) from exc
                if not text.startswith("gitdir: ") or not text[8:]: raise validation_error("Worktree .git marker must name its gitdir", "migration_repository_invalid", path=str(marker))
                gitdir = Path(text[8:]); gitdir = gitdir if gitdir.is_absolute() else current / gitdir; _real_dir(gitdir, "migration_repository_invalid"); _read_regular(gitdir / "HEAD", "migration_repository_invalid")
            return current
        if current.parent == current: break
        current = current.parent
    raise validation_error("Source has no derivable repository boundary", "migration_repository_not_found", path=str(path))

def _registered_bundle_issues(path: Path) -> list[dict[str, Any]]:
    try:
        layout, layout_raw = _json_file(path / "profiles" / "layout.json", "migration_registered_sibling_invalid")
    except CortexError as exc:
        return exc.details.get("issues", [exc.as_issue()])
    if layout.get("version") == 5:
        return validate_workspace(path).issues
    if layout.get("version") != 4 or layout_raw != json_bytes(_canonical_target(layout)):
        return [issue("invalid_profile_version", "Registered sibling must use exact Layout 4 or Layout 5", path=str(path / "profiles/layout.json"))]
    try:
        record, record_raw = _json_file(path / "profiles" / "record-schema.json", "migration_registered_sibling_invalid")
        tags, tags_raw = _json_file(path / "profiles" / "tags.json", "migration_registered_sibling_invalid")
        problems = []
        if record != RECORD_SCHEMA or record_raw != json_bytes(RECORD_SCHEMA): problems.append(issue("invalid_record_schema", "Registered sibling Record Profile is invalid"))
        tag_problems = validate_tags_profile(tags); problems.extend(tag_problems)
        if not tag_problems and tags_raw != json_bytes(_canonical_tags(tags)): problems.append(issue("noncanonical_source_profile", "Registered sibling Tag Profile is noncanonical"))
        for partition in _children(path):
            if partition.name == "profiles": continue
            meta = partition.stat(follow_symlinks=False)
            if is_reparse_metadata(meta) or not stat.S_ISDIR(meta.st_mode): problems.append(issue("invalid_partition", "Registered sibling partition must be a real directory", path=partition.name)); continue
            units = _children(Path(partition.path))
            if not units: problems.append(issue("empty_partition", "Registered sibling Layout 4 partition must be nonempty", path=partition.name))
            for unit in units:
                checked, _manifest, _kind_value = _layout4_unit(Path(unit.path), partition=partition.name, tags=tags, layout=layout)
                problems.extend(checked)
        return problems
    except CortexError as exc:
        return exc.details.get("issues", [exc.as_issue()])

def _validated_boundaries(source: Path, kb_root_operand: Path | None, kb_repo_operand: Path | None) -> tuple[Path, ...]:
    if kb_root_operand is None or kb_repo_operand is None or not os.fspath(kb_root_operand) or not os.fspath(kb_repo_operand): raise validation_error("Exact KB root and KB repository operands are required", "migration_boundary_required")
    source = Path(os.path.abspath(source)); kb_root = Path(os.path.abspath(kb_root_operand)); kb_repo = Path(os.path.abspath(kb_repo_operand))
    _real_dir(_REPO, "migration_source_repository_invalid"); _real_dir(source, "migration_source_invalid"); _real_dir(kb_root, "migration_kb_root_invalid"); _real_dir(kb_repo, "migration_kb_repository_invalid")
    if source.parent != kb_root: raise validation_error("Source must be a direct child of the initialized KB root", "migration_source_not_registered", path=str(source))
    derived_repo = _git_repository(kb_root)
    if kb_repo != derived_repo: raise validation_error("KB repository operand does not equal the derived repository boundary", "migration_repository_mismatch", expected=str(derived_repo), actual=str(kb_repo))
    registry, registry_raw = _json_file(kb_root / REGISTRY_FILENAME, "migration_registry_invalid")
    registry_problems = validate_registry_value(registry)
    if registry_problems: raise validation_error("KB Registry 1 is invalid", "migration_registry_invalid", issues=registry_problems)
    canonical = canonical_registry(registry)
    if registry_raw != json_bytes(canonical): raise validation_error("KB Registry 1 JSON is not canonical", "migration_registry_noncanonical", path=REGISTRY_FILENAME)
    matches = [entry for entry in canonical["bundles"] if entry["path"] == source.name]
    if len(matches) != 1: raise validation_error("Source direct child must have exactly one Registry 1 entry", "migration_source_not_registered", path=source.name)
    lock = kb_root / ROOT_LOCK_FILENAME
    try: lock_meta = os.lstat(native_path(lock))
    except OSError as exc: raise validation_error("Initialized KB root lock is unreadable", "migration_root_lock_invalid", path=ROOT_LOCK_FILENAME, os_error=str(exc)) from exc
    if is_reparse_metadata(lock_meta) or not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_size != 0: raise validation_error("Initialized KB root lock must be ordinary and zero-byte", "migration_root_lock_invalid", path=ROOT_LOCK_FILENAME)
    registered = {entry["path"] for entry in canonical["bundles"]}
    for entry in canonical["bundles"]:
        if entry["path"] == source.name: continue
        sibling = kb_root / entry["path"]
        try: _real_dir(sibling, "migration_registered_sibling_invalid")
        except CortexError as exc: raise validation_error("Registered sibling Bundle is missing or invalid", "migration_registered_sibling_invalid", path=entry["path"], issues=[exc.as_issue()]) from exc
        sibling_issues = _registered_bundle_issues(sibling)
        if sibling_issues: raise validation_error("Registered sibling must be a valid Layout 4 or Layout 5 Bundle", "migration_registered_sibling_invalid", path=entry["path"], issues=sibling_issues)
    for child in _children(kb_root):
        if child.name in {REGISTRY_FILENAME, ROOT_LOCK_FILENAME, ".git"} or child.name in registered: continue
        try: child_meta = child.stat(follow_symlinks=False)
        except OSError: continue
        if is_reparse_metadata(child_meta) or not stat.S_ISDIR(child_meta.st_mode): continue
        profile_dir = Path(child.path) / "profiles"
        try:
            profile_meta = os.lstat(native_path(profile_dir))
            if is_reparse_metadata(profile_meta) or not stat.S_ISDIR(profile_meta.st_mode): continue
            profile_names = {profile.name for profile in os.scandir(native_path(profile_dir))}
        except OSError: continue
        if {"record-schema.json", "tags.json", "layout.json"} <= profile_names: raise validation_error("Unregistered complete Bundle is forbidden", "migration_orphan_bundle", path=child.name)
    return (_REPO, source, kb_repo, kb_root)

def _destination(source: Path, output: Path, forbidden: Iterable[Path]) -> None:
    if exists(output): raise validation_error("Candidate must be absent", "migration_output_exists", path=str(output))
    if _contains(source, output) or _contains(output, source): raise validation_error("Candidate must be separate from source", "migration_output_overlap", path=str(output))
    for root in forbidden:
        if _contains(Path(root), output): raise validation_error("Candidate/staging is forbidden below a protected root", "migration_candidate_location", path=str(output), root=str(root))
    if _real_dir(output.parent, "migration_output_parent_invalid").st_dev != _real_dir(source, "migration_source_invalid").st_dev: raise validation_error("Candidate must be on source volume", "migration_volume_mismatch", path=str(output))

def build_bundle(source: Path, output: Path, target_layout_path: Path, expected_digest: str, *, kb_root: Path | None = None, kb_repo: Path | None = None) -> dict[str, Any]:
    source = Path(os.path.abspath(source)); output = Path(os.path.abspath(output)); protected = _validated_boundaries(source, kb_root, kb_repo); _destination(source, output, protected)
    body, raw, digest = plan_bundle(source, target_layout_path)
    if digest != expected_digest: raise validation_error("Approved plan digest does not match", "migration_plan_digest_mismatch", expected=expected_digest, actual=digest)
    if body["issues"]: raise validation_error("Migration plan contains issues", "migration_plan_invalid", issues=body["issues"])
    stage = output.parent / f".cortex-mig-{uuid.uuid4().hex}"; _destination(source, stage, protected)
    try:
        if body["format"] == "cortex-layout3-to-layout4-plan-v1":
            stage.mkdir(); (stage / "profiles").mkdir(); shutil.copyfile(native_path(source / "profiles" / "record-schema.json"), native_path(stage / "profiles" / "record-schema.json")); shutil.copyfile(native_path(source / "profiles" / "tags.json"), native_path(stage / "profiles" / "tags.json")); (stage / "profiles" / "layout.json").write_bytes(json_bytes(body["profiles"]["layout"]))
            for item in body["mappings"]:
                partition = stage / item["partition"]; partition.mkdir(exist_ok=True); shutil.copytree(native_path(source / item["source_unit"]), native_path(partition / item["target_unit"]), symlinks=False)
            expected_roots = {"profiles", *(item["partition"] for item in body["mappings"])}
            if {entry.name for entry in _children(stage)} != expected_roots:
                raise validation_error("Layout 4 candidate root differs from its approved plan", "migration_candidate_invalid")
            for item in body["mappings"]:
                if _walk(stage / item["partition"] / item["target_unit"]) != item["manifest"]:
                    raise validation_error("Layout 4 candidate payload differs from its approved plan", "migration_candidate_invalid", path=f"{item['partition']}/{item['target_unit']}")
        else:
            shutil.copytree(native_path(source), native_path(stage), symlinks=False)
            (stage / "profiles" / "layout.json").write_bytes(json_bytes(body["profiles"]["layout"]))
            for item in body["mappings"]:
                finalize_staged(stage / item["partition"] / item["record"], source=None)
            report = validate_workspace(stage)
            if not report.valid: raise validation_error("Layout 5 candidate validation failed", "migration_candidate_invalid", issues=report.issues)
        again, again_raw, again_digest = plan_bundle(source, target_layout_path)
        if (again, again_raw, again_digest) != (body, raw, digest): raise validation_error("Source changed during build", "migration_source_drift")
        try: rename_no_replace(stage, output)
        except FileExistsError as exc: raise validation_error("Candidate must remain absent through publication", "migration_output_exists", path=str(output)) from exc
        stage = None
        return {"output": str(output), "plan_sha256": digest, "counts": body["counts"]}
    finally:
        if stage is not None and stage.exists(): shutil.rmtree(native_path(stage))

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan"); plan.add_argument("--source", required=True); plan.add_argument("--layout", required=True)
    build = sub.add_parser("build"); build.add_argument("--source", required=True); build.add_argument("--output", required=True); build.add_argument("--layout", required=True); build.add_argument("--plan-sha256", required=True); build.add_argument("--kb-root", required=True); build.add_argument("--kb-repo", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            body, _, digest = plan_bundle(Path(args.source), Path(args.layout))
            if body["issues"]: raise validation_error("Migration plan contains issues", "migration_plan_invalid", issues=body["issues"])
            result = {"body": body, "plan_sha256": digest}
        else: result = build_bundle(Path(args.source), Path(args.output), Path(args.layout), args.plan_sha256, kb_root=Path(args.kb_root), kb_repo=Path(args.kb_repo))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except CortexError as exc:
        print(json.dumps({"status": exc.status.value, "issues": exc.details.get("issues", [exc.as_issue()])}, ensure_ascii=True)); return int(exc.status.exit_code)

if __name__ == "__main__": raise SystemExit(main())
