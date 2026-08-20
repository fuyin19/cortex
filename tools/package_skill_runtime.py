#!/usr/bin/env python3
"""Build the deterministic, offline Cortex runtime embedded in both skills."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import tomllib
import zipfile


VERSION = "6.0.0"
DISTRIBUTION = "cortex-record-kb"
IMPORT_NAME = "cortex"
WHEEL_NAME = "cortex_record_kb-6.0.0-py3-none-any.whl"
DIST_INFO = "cortex_record_kb-6.0.0.dist-info"
SKILLS = ("cortex-build", "cortex-manage")
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
    for path in sorted(package.glob("*.py"), key=lambda value: value.name.encode("utf-8")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unsafe source package entry: {path}")
        items.append((f"{IMPORT_NAME}/{path.name}", path.read_bytes()))
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
import zipfile


EXPECTED_VERSION = "6.0.0"
EXPECTED_DISTRIBUTION = "cortex-record-kb"
EXPECTED_WHEEL_FILENAME = "cortex_record_kb-6.0.0-py3-none-any.whl"
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
    metadata_name = "cortex_record_kb-6.0.0.dist-info/METADATA"
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
    if sys.version_info[:2] != (3, 11):
        raise BootstrapError("python_3_11_required")
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
        "scripts/run_cortex.py": _runner_bytes(digest),
        "scripts/runtime-manifest.json": _manifest_bytes(digest),
        f"scripts/vendor/{WHEEL_NAME}": wheel,
    }


def _install_payload(root: Path, expected: dict[str, bytes]) -> None:
    for skill_name in SKILLS:
        skill = root / "skills" / skill_name
        vendor = skill / "scripts" / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        for stale in vendor.glob("*.whl"):
            if stale.name != WHEEL_NAME:
                stale.unlink()
        for relative, raw in expected.items():
            destination = skill / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)


def _check_payload(root: Path, expected: dict[str, bytes]) -> None:
    observed: list[dict[str, bytes]] = []
    for skill_name in SKILLS:
        skill = root / "skills" / skill_name
        actual: dict[str, bytes] = {}
        for relative, raw in expected.items():
            path = skill / Path(relative)
            if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
                raise RuntimeError(f"skill runtime drift: {skill_name}/{relative}")
            actual[relative] = path.read_bytes()
        wheels = sorted((skill / "scripts" / "vendor").glob("*.whl"))
        if [item.name for item in wheels] != [WHEEL_NAME]:
            raise RuntimeError(f"unexpected skill runtime artifact: {skill_name}")
        observed.append(actual)
    if observed[0] != observed[1]:
        raise RuntimeError("skill runtime payloads are not byte-identical")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify generated payload without writing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    expected = _expected_payload(root)
    if not args.check:
        _install_payload(root, expected)
    _check_payload(root, expected)
    digest = _sha256(expected[f"scripts/vendor/{WHEEL_NAME}"])
    print(f"{WHEEL_NAME} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
