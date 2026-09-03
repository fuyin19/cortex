#!/usr/bin/env python3
"""Build the deterministic, offline Cortex runtime embedded in repository skills."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
import zipfile


VERSION = "8.1.1"
DISTRIBUTION = "cortex-record-kb"
IMPORT_NAME = "cortex"
WHEEL_NAME = "cortex_record_kb-8.1.1-py3-none-any.whl"
DIST_INFO = "cortex_record_kb-8.1.1.dist-info"
ADAPTER = Path("skills/cortex/scripts/kb")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_digest(raw: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _source_files(root: Path) -> list[tuple[str, bytes]]:
    package = root / "src" / IMPORT_NAME
    items: list[tuple[str, bytes]] = []
    selected = [
        path for path in package.rglob("*")
        if path.is_file() and path.suffix == ".py"
    ]
    for path in sorted(selected, key=lambda value: value.relative_to(package).as_posix().encode("utf-8")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unsafe source package entry: {path}")
        items.append((f"{IMPORT_NAME}/{path.relative_to(package).as_posix()}", path.read_bytes()))
    if not items or not any(name == "cortex/__init__.py" for name, _raw in items):
        raise RuntimeError("Cortex source package is incomplete")
    return items


def _verify_source_versions(root: Path) -> None:
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    package = json.loads((root / "package.json").read_text("utf-8"))
    constants = (root / "src" / "cortex" / "constants.py").read_text("utf-8")
    init = (root / "src" / "cortex" / "__init__.py").read_text("utf-8")
    if project.get("name") != DISTRIBUTION or project.get("version") != VERSION:
        raise RuntimeError("pyproject distribution/version does not match the runtime contract")
    if package.get("name") != DISTRIBUTION or package.get("version") != VERSION:
        raise RuntimeError("package version does not match the runtime contract")
    if not re.search(rf'^VERSION = "{re.escape(VERSION)}"$', constants, re.MULTILINE):
        raise RuntimeError("Cortex constant version does not match the runtime contract")
    if "__version__ = VERSION" not in init:
        raise RuntimeError("Cortex package version is not bound to the verified constant")
    if project.get("dependencies") != []:
        raise RuntimeError("embedded Cortex runtime must have no dependencies")
    if "scripts" in project or "entry-points" in project:
        raise RuntimeError("embedded Cortex runtime must not declare an installed command")


def _build_wheel(root: Path, output: Path) -> None:
    members = _source_files(root)
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {DISTRIBUTION}\n"
        f"Version: {VERSION}\n"
        "Summary: Minimal single-writer record knowledge base\n"
        "Requires-Python: >=3.11,<3.12\n"
        "\n"
    ).encode("utf-8")
    wheel = (
        "Wheel-Version: 1.0\n"
        "Generator: cortex-skill-runtime\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
        "\n"
    ).encode("utf-8")
    members.extend(((f"{DIST_INFO}/METADATA", metadata), (f"{DIST_INFO}/WHEEL", wheel)))
    rows = [[name, _record_digest(raw), str(len(raw))] for name, raw in members]
    rows.append([f"{DIST_INFO}/RECORD", "", ""])
    record_buffer = io.StringIO(newline="")
    csv.writer(record_buffer, lineterminator="\n").writerows(rows)
    members.append((f"{DIST_INFO}/RECORD", record_buffer.getvalue().encode("utf-8")))
    with zipfile.ZipFile(output, "w") as archive:
        for name, raw in members:
            archive.writestr(_zip_info(name), raw)


def _runner_bytes(digest: str) -> bytes:
    template = r'''#!/usr/bin/env python3
"""Offline, skill-local Cortex launcher. Generated; do not edit."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import unicodedata
import zipfile


EXPECTED_VERSION = "8.1.1"
EXPECTED_DISTRIBUTION = "cortex-record-kb"
EXPECTED_WHEEL_FILENAME = "cortex_record_kb-8.1.1-py3-none-any.whl"
EXPECTED_WHEEL_SHA256 = "__WHEEL_SHA256__"
EXPECTED_MANIFEST_KEYS = {
    "schema_version", "distribution", "import", "version", "wheel",
    "wheel_sha256", "python", "isolation",
}
BOOTSTRAP_EXIT = 70


class BootstrapError(Exception):
    pass


def _fail(code: str) -> int:
    sys.stderr.write(f"cortex skill runtime error: {code}\n")
    return BOOTSTRAP_EXIT


def _is_reparse(result: os.stat_result) -> bool:
    attributes = getattr(result, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _check_chain(path: Path, *, final_file: bool) -> Path:
    if not path.is_absolute():
        raise BootstrapError("non_absolute_path")
    nodes = list(reversed((path,) + tuple(path.parents)))
    for index, node in enumerate(nodes):
        try:
            result = node.lstat()
        except OSError as exc:
            raise BootstrapError("runtime_path_unreadable") from exc
        if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
            raise BootstrapError("runtime_path_reparse")
        is_final = index == len(nodes) - 1
        if is_final and final_file:
            if not stat.S_ISREG(result.st_mode):
                raise BootstrapError("runtime_file_not_regular")
        elif not stat.S_ISDIR(result.st_mode):
            raise BootstrapError("runtime_parent_not_directory")
    return path


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("manifest_invalid") from exc
    expected = {
        "schema_version": 1,
        "distribution": EXPECTED_DISTRIBUTION,
        "import": "cortex",
        "version": EXPECTED_VERSION,
        "wheel": EXPECTED_WHEEL_FILENAME,
        "wheel_sha256": EXPECTED_WHEEL_SHA256,
        "python": "3.11",
        "isolation": "-I",
    }
    if not isinstance(value, dict) or set(value) != EXPECTED_MANIFEST_KEYS or value != expected:
        raise BootstrapError("manifest_mismatch")
    return value


def _verify_archive(path: Path) -> None:
    metadata_name = "cortex_record_kb-8.1.1.dist-info/METADATA"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or metadata_name not in names:
                raise BootstrapError("wheel_metadata_invalid")
            if any(name.endswith("entry_points.txt") for name in names):
                raise BootstrapError("wheel_entry_point_forbidden")
            metadata = archive.read(metadata_name).decode("utf-8")
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as exc:
        raise BootstrapError("wheel_invalid") from exc
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        if not line:
            break
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    if fields.get("Name") != EXPECTED_DISTRIBUTION or fields.get("Version") != EXPECTED_VERSION:
        raise BootstrapError("wheel_version_mismatch")
    if any(line.startswith("Requires-Dist:") for line in metadata.splitlines()):
        raise BootstrapError("wheel_dependency_forbidden")


def _module_is_from_wheel(module: object, wheel: Path) -> bool:
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return False
    normalized_origin = os.path.normcase(os.path.abspath(origin)).replace("\\", "/")
    normalized_wheel = os.path.normcase(str(wheel)).replace("\\", "/")
    return normalized_origin.startswith(normalized_wheel + "/cortex/")


def _run() -> int:
    configured_raw = os.environ.get("CORTEX_PYTHON")
    if configured_raw is None or configured_raw == "":
        raise BootstrapError("cortex_python_required")
    configured = _check_chain(Path(configured_raw), final_file=True)
    try:
        same_interpreter = os.path.samefile(configured, sys.executable)
    except OSError as exc:
        raise BootstrapError("cortex_python_unreadable") from exc
    if not same_interpreter:
        raise BootstrapError("cortex_python_mismatch")
    if sys.version_info[:2] != (3, 11):
        raise BootstrapError("python_3_11_required")
    if unicodedata.unidata_version != "14.0.0":
        raise BootstrapError("unicode_14_required")
    if not sys.flags.isolated:
        raise BootstrapError("isolated_mode_required")
    runner = _check_chain(Path(os.path.abspath(__file__)), final_file=True)
    scripts = runner.parent
    skill = scripts.parent
    manifest_path = _check_chain(scripts / "runtime-manifest.json", final_file=True)
    wheel = _check_chain(scripts / "vendor" / EXPECTED_WHEEL_FILENAME, final_file=True)
    _load_manifest(manifest_path)
    try:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    except OSError as exc:
        raise BootstrapError("wheel_unreadable") from exc
    if digest != EXPECTED_WHEEL_SHA256:
        raise BootstrapError("wheel_digest_mismatch")
    _verify_archive(wheel)
    if "cortex" in sys.modules:
        raise BootstrapError("ambient_cortex_loaded")
    sys.path.insert(0, str(wheel))
    package = importlib.import_module("cortex")
    if getattr(package, "__version__", None) != EXPECTED_VERSION or not _module_is_from_wheel(package, wheel):
        raise BootstrapError("import_origin_mismatch")
    core_client = importlib.import_module("cortex.core_runner")
    for boundary in runner.parents:
        if boundary.name == "cortex":
            marker = boundary / "SKILL.md"
            try:
                marker_info = marker.lstat()
            except OSError:
                break
            if stat.S_ISREG(marker_info.st_mode) and not stat.S_ISLNK(marker_info.st_mode) and not bool(getattr(marker_info, "st_file_attributes", 0) & 0x400):
                core_skill = boundary.parent / "anti-entropy-core"
                core_client.set_default_runner(core_skill / "scripts" / "knowledge_unit_runner.py", core_skill / "SKILL.md")
            break
    cli = importlib.import_module("cortex.cli")
    if not _module_is_from_wheel(cli, wheel):
        raise BootstrapError("import_origin_mismatch")
    return int(cli.main(sys.argv[1:]))


def main() -> int:
    try:
        return _run()
    except BootstrapError as exc:
        return _fail(str(exc))
    except Exception:
        return _fail("bootstrap_failed")


if __name__ == "__main__":
    raise SystemExit(main())
'''
    return template.replace("__WHEEL_SHA256__", digest).encode("utf-8")


def _windows_launcher_bytes() -> bytes:
    return (
        "@echo off\r\n"
        "if not defined CORTEX_PYTHON (\r\n"
        "  >&2 echo cortex skill runtime error: cortex_python_required\r\n"
        "  exit /b 70\r\n"
        ")\r\n"
        '"%CORTEX_PYTHON%" -I "%~dp0run_cortex.py" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    ).encode("ascii")


def _batch_helper_bytes() -> bytes:
    source = Path(__file__).resolve().parents[1] / ADAPTER / "batch_record_add.py"
    return source.read_bytes()

    # Kept unreachable only as historical generator input for old source trees.
    return r'''#!/usr/bin/env python3
"""Build-skill-only sequential wrapper for Cortex record add."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA_VERSION = 1
RESULT_KEYS = {"status", "exit_code", "command", "data", "issues"}
STATUS_EXIT_CODES = {
    "ok": 0,
    "usage_error": 2,
    "validation_error": 3,
    "busy": 5,
    "io_error": 6,
}


class BatchUsage(Exception):
    pass


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BatchUsage("invalid_arguments")


def _parser() -> ContractParser:
    parser = ContractParser(prog="cortex-record-add-batch")
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--workspace")
    selectors.add_argument("--kb-root")
    parser.add_argument("--bundle-id")
    parser.add_argument("--job", required=True)
    return parser


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchUsage("duplicate_json_key")
        result[key] = value
    return result


def _read_job(operand: str) -> object:
    try:
        raw = sys.stdin.buffer.read() if operand == "-" else Path(operand).read_bytes()
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except BatchUsage:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatchUsage("job_invalid") from exc


def _absolute_string(value: object) -> bool:
    return isinstance(value, str) and value != "" and Path(value).is_absolute()


def _validate_job(value: object) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != {"version", "items"}:
        raise BatchUsage("job_shape_invalid")
    if type(value["version"]) is not int or value["version"] not in {1, 2}:
        raise BatchUsage("job_version_invalid")
    version = value["version"]
    items = value["items"]
    if not isinstance(items, list):
        raise BatchUsage("job_items_invalid")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise BatchUsage("job_item_invalid")
        keys = set(item)
        v1_shapes = ({"id", "source", "metadata"}, {"id", "source", "conversion", "metadata"})
        v2_shapes = ({"id", "source", "metadata"}, {"id", "conversion", "metadata"}, {"id", "source", "conversion", "metadata"})
        if keys not in (v1_shapes if version == 1 else v2_shapes):
            raise BatchUsage("job_item_shape_invalid")
        item_id = item["id"]
        if not isinstance(item_id, str) or item_id == "" or item_id in ids:
            raise BatchUsage("job_item_id_invalid")
        ids.add(item_id)
        if "source" in item and not _absolute_string(item["source"]):
            raise BatchUsage("job_source_not_absolute")
        if "conversion" in item and not _absolute_string(item["conversion"]):
            raise BatchUsage("job_conversion_not_absolute")
        metadata = item["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != {"title", "timestamp", "tags"}:
            raise BatchUsage("job_metadata_shape_invalid")
        if not isinstance(metadata["title"], str) or not isinstance(metadata["timestamp"], str):
            raise BatchUsage("job_metadata_value_invalid")
        tags = metadata["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise BatchUsage("job_metadata_value_invalid")
        normalized.append(item)
    return version, normalized


def _selectors(args: argparse.Namespace) -> list[str]:
    if args.workspace is not None:
        if args.bundle_id is not None:
            raise BatchUsage("invalid_selector")
        return ["--workspace", args.workspace]
    if args.bundle_id is None:
        raise BatchUsage("invalid_selector")
    return ["--kb-root", args.kb_root, "--bundle-id", args.bundle_id]


def _invoke(command: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _result(process: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        value = json.loads(process.stdout.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchUsage("runner_non_result") from exc
    if (
        process.stderr != b""
        or not isinstance(value, dict)
        or set(value) != RESULT_KEYS
        or value.get("command") != "record.add"
        or not isinstance(value.get("data"), dict)
        or not isinstance(value.get("issues"), list)
        or any(not isinstance(issue, dict) for issue in value["issues"])
        or type(value.get("exit_code")) is not int
        or value.get("status") not in STATUS_EXIT_CODES
        or STATUS_EXIT_CODES[value["status"]] != value["exit_code"]
        or process.returncode != value["exit_code"]
    ):
        raise BatchUsage("runner_non_result")
    return value


def _write_wrapper(items: list[dict[str, Any]], total: int) -> int:
    succeeded = sum(item["result"]["status"] == "ok" for item in items)
    value = {
        "schema_version": SCHEMA_VERSION,
        "command": "record.add.batch",
        "items": items,
        "summary": {"total": total, "succeeded": succeeded, "failed": total - succeeded},
    }
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
    return 0 if succeeded == total else 1


def _run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    selectors = _selectors(args)
    job_version, items = _validate_job(_read_job(args.job))
    runner = Path(__file__).absolute().parent / "run_cortex.py"
    base = [sys.executable, "-I", str(runner)]
    preflight = _invoke([*base, "--version"])
    with tempfile.TemporaryDirectory(prefix="cortex-record-add-batch-") as temporary:
        normalized = Path(temporary) / "job.json"
        normalized.write_text(json.dumps({"version": job_version, "items": items}, ensure_ascii=False,
                                         separators=(",", ":")) + "\n", encoding="utf-8")
        process = _invoke([*base, *selectors, "--_record-add-batch", str(normalized)])
        if process.returncode not in {0, 1} or process.stderr != b"":
            raise BatchUsage("runner_bootstrap_failed" if process.returncode == 70 else "runner_non_result")
        try: wrapper = json.loads(process.stdout.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as exc: raise BatchUsage("runner_non_result") from exc
        if not isinstance(wrapper, dict) or wrapper.get("command") != "record.add.batch": raise BatchUsage("runner_non_result")
        sys.stdout.buffer.write(process.stdout)
        return process.returncode


def main() -> int:
    try:
        return _run(sys.argv[1:])
    except KeyboardInterrupt:
        sys.stderr.write("cortex record batch error: interrupted\n")
        return 2
    except BatchUsage as exc:
        sys.stderr.write(f"cortex record batch error: {exc}\n")
        return 2
    except Exception:
        sys.stderr.write("cortex record batch error: wrapper_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''.encode("utf-8")


def _manifest_bytes(digest: str) -> bytes:
    value = {
        "schema_version": 1,
        "distribution": DISTRIBUTION,
        "import": IMPORT_NAME,
        "version": VERSION,
        "wheel": WHEEL_NAME,
        "wheel_sha256": digest,
        "python": "3.11",
        "isolation": "-I",
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _expected_payload(root: Path) -> dict[str, bytes]:
    _verify_source_versions(root)
    with tempfile.TemporaryDirectory(prefix="cortex-skill-wheel-") as temporary:
        wheel_path = Path(temporary) / WHEEL_NAME
        _build_wheel(root, wheel_path)
        wheels = list(Path(temporary).iterdir())
        if wheels != [wheel_path]:
            raise RuntimeError("runtime build did not produce exactly one expected artifact")
        wheel = wheel_path.read_bytes()
    digest = _sha256(wheel)
    return {
        "run_cortex.py": _runner_bytes(digest),
        "run_cortex.cmd": _windows_launcher_bytes(),
        "runtime-manifest.json": _manifest_bytes(digest),
        f"vendor/{WHEEL_NAME}": wheel,
    }


def _is_reparse(result: os.stat_result) -> bool:
    attributes = getattr(result, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _ordinary(path: Path, *, directory: bool) -> bool:
    try:
        result = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
        raise RuntimeError(f"linked runtime path is forbidden: {path}")
    expected = stat.S_ISDIR(result.st_mode) if directory else stat.S_ISREG(result.st_mode)
    if not expected:
        raise RuntimeError(f"nonordinary runtime path is forbidden: {path}")
    return True


def _prepare_skill(root: Path, expected: dict[str, bytes]) -> Path:
    skill = root / ADAPTER
    if not _ordinary(skill, directory=True):
        raise RuntimeError("missing KB adapter directory")
    scripts = skill
    vendor = scripts / "vendor"
    if vendor.exists() or vendor.is_symlink():
        _ordinary(vendor, directory=True)
    else:
        vendor.mkdir()

    allowed_files = {Path(relative).as_posix() for relative in expected}
    allowed_files.add("batch_record_add.py")
    allowed_directories = {"vendor"}
    for path in sorted(scripts.rglob("*"), key=lambda value: value.as_posix().encode("utf-8")):
        relative = path.relative_to(skill).as_posix()
        result = path.lstat()
        if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
            raise RuntimeError(f"linked KB runtime artifact is forbidden: {relative}")
        if stat.S_ISDIR(result.st_mode):
            if relative not in allowed_directories:
                raise RuntimeError(f"unexpected KB runtime directory: {relative}")
        elif stat.S_ISREG(result.st_mode):
            generated_wheel = relative.startswith("vendor/cortex_record_kb-") and relative.endswith("-py3-none-any.whl")
            if relative not in allowed_files and not generated_wheel:
                raise RuntimeError(f"unexpected KB runtime artifact: {relative}")
        else:
            raise RuntimeError(f"nonordinary KB runtime artifact is forbidden: {relative}")
    return skill


def _install_payload(root: Path, expected: dict[str, bytes]) -> None:
    skill = _prepare_skill(root, expected)
    for stale in (skill / "vendor").glob("cortex_record_kb-*-py3-none-any.whl"):
        if stale.name != WHEEL_NAME:
            stale.unlink()
    for relative, raw in expected.items():
        destination = skill / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    (skill / "batch_record_add.py").write_bytes(_batch_helper_bytes())


def _check_payload(root: Path, expected: dict[str, bytes]) -> None:
    skill = _prepare_skill(root, expected)
    for relative, raw in expected.items():
        path = skill / Path(relative)
        if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
            raise RuntimeError(f"KB adapter runtime drift: {relative}")
    wheels = sorted((skill / "vendor").glob("*.whl"))
    if [item.name for item in wheels] != [WHEEL_NAME]:
        raise RuntimeError("unexpected KB adapter runtime artifact")
    expected_batch = _batch_helper_bytes()
    batch = skill / "batch_record_add.py"
    if not _ordinary(batch, directory=False) or batch.read_bytes() != expected_batch:
        raise RuntimeError("KB batch helper drift")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify generated payload without writing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    expected = _expected_payload(root)
    if not args.check:
        _install_payload(root, expected)
    _check_payload(root, expected)
    digest = _sha256(expected[f"vendor/{WHEEL_NAME}"])
    print(f"{WHEEL_NAME} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
