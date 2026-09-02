"""Closed Cortex 8 command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .alignment import apply_alignment, plan_alignment
from .constants import PUBLIC_ROUTES, VERSION
from .core_runner import CoreRunner
from .errors import CortexError, Status, io_error
from .service import CortexService, Outcome, RegistryService


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CortexError(message, status=Status.USAGE_ERROR, code="invalid_arguments")


def _parser() -> ContractParser:
    parser = ContractParser(prog="cortex")
    parser.add_argument("--json", action="store_true", help="emit one machine-readable Result")
    parser.add_argument("--workspace", help="one Cortex 8 Bundle root")
    parser.add_argument("--kb-root", help="one registered Cortex KB root")
    parser.add_argument("--bundle-id", help="explicit registered Bundle id")
    parser.add_argument("--version", action="version", version=f"cortex {VERSION}")
    groups = parser.add_subparsers(dest="group", required=True)

    align = groups.add_parser("align")
    align_commands = align.add_subparsers(dest="align_command", required=True)
    align_commands.add_parser("plan")
    align_apply = align_commands.add_parser("apply")
    align_apply.add_argument("--plan", required=True)

    registry = groups.add_parser("registry")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    registry_commands.add_parser("show")
    registry_commands.add_parser("validate")
    resolve = registry_commands.add_parser("resolve")
    resolve.add_argument("--bundle-id", required=True, dest="resolve_bundle_id")
    set_registry = registry_commands.add_parser("set")
    set_registry.add_argument("--file", required=True)

    manage = groups.add_parser("manage")
    manage_commands = manage.add_subparsers(dest="manage_command", required=True)
    manage_commands.add_parser("init")
    manage_commands.add_parser("status")
    manage_commands.add_parser("validate")
    config = manage_commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    show = config_commands.add_parser("show")
    show.add_argument("--profile", required=True, choices=("record", "tags", "layout"))
    set_command = config_commands.add_parser("set")
    set_command.add_argument("--profile", required=True, choices=("tags", "layout"))
    set_command.add_argument("--file", required=True)

    record = groups.add_parser("record")
    record_commands = record.add_subparsers(dest="record_command", required=True)
    add = record_commands.add_parser("add")
    add.add_argument("--source")
    add.add_argument("--conversion")
    add.add_argument("--metadata", required=True)
    edit = record_commands.add_parser("edit")
    edit.add_argument("--partition", required=True)
    edit.add_argument("--record", required=True)
    edit.add_argument("--metadata", required=True)
    show_record = record_commands.add_parser("show")
    show_record.add_argument("--partition", required=True)
    show_record.add_argument("--record", required=True)
    delete = record_commands.add_parser("delete")
    delete.add_argument("--partition", required=True)
    delete.add_argument("--record", required=True)
    delete.add_argument("--expected-tree-sha256", required=True)
    return parser


def _route(args: argparse.Namespace) -> str:
    if args.group == "align":
        route = f"align.{args.align_command}"
    elif args.group == "registry":
        route = f"registry.{args.registry_command}"
    elif args.group == "manage":
        if args.manage_command == "config":
            route = f"manage.config.{args.config_command}"
        else:
            route = f"manage.{args.manage_command}"
    else:
        route = f"record.{args.record_command}"
    if route not in PUBLIC_ROUTES:
        raise CortexError("Route is not public", status=Status.USAGE_ERROR, code="unknown_route")
    return route


def _dispatch(route: str, args: argparse.Namespace) -> Outcome:
    if route == "align.plan":
        if args.workspace is None or args.kb_root is not None or args.bundle_id is not None:
            raise CortexError("align plan requires only --workspace", status=Status.USAGE_ERROR, code="invalid_selector")
        return Outcome(data={"plan": plan_alignment(Path(args.workspace), CoreRunner.from_config())})
    if route == "align.apply":
        if args.workspace is not None or args.kb_root is not None or args.bundle_id is not None:
            raise CortexError("align apply requires only --plan", status=Status.USAGE_ERROR, code="invalid_selector")
        return Outcome(data=apply_alignment(args.plan, CoreRunner.from_config()))

    if route.startswith("registry."):
        if args.workspace is not None or args.kb_root is None or args.bundle_id is not None:
            raise CortexError("Registry routes require only --kb-root", status=Status.USAGE_ERROR, code="invalid_selector")
        service = RegistryService(Path(args.kb_root), core=CoreRunner.from_config())
        if route == "registry.show":
            return service.show()
        if route == "registry.validate":
            return service.validate()
        if route == "registry.resolve":
            return service.resolve(args.resolve_bundle_id)
        if route == "registry.set":
            return service.set(args.file)
        raise CortexError("Route is not public", status=Status.USAGE_ERROR, code="unknown_route")

    if route == "manage.init":
        if args.workspace is None or args.kb_root is not None or args.bundle_id is not None:
            raise CortexError("manage init requires only --workspace", status=Status.USAGE_ERROR, code="invalid_selector")
        service = CortexService(Path(args.workspace))
        return service.init()
    if args.workspace is not None:
        if args.kb_root is not None or args.bundle_id is not None:
            raise CortexError("Use either --workspace or --kb-root with --bundle-id", status=Status.USAGE_ERROR, code="invalid_selector")
        service = CortexService(Path(args.workspace), core=CoreRunner.from_config())
    else:
        if args.kb_root is None or args.bundle_id is None:
            raise CortexError("Managed bundle routes require --kb-root and --bundle-id", status=Status.USAGE_ERROR, code="bundle_selection_required")
        if route in {"manage.config.set", "record.add", "record.edit", "record.show", "record.delete"}:
            service = CortexService(None, kb_root=Path(args.kb_root), bundle_id=args.bundle_id, core=CoreRunner.from_config())
        else:
            core = CoreRunner.from_config()
            registry_service = RegistryService(Path(args.kb_root), core=core)
            resolved = registry_service.resolve(args.bundle_id).data
            service = CortexService(Path(resolved["workspace"]), kb_root=Path(args.kb_root), bundle_id=args.bundle_id, core=core)
    if route == "manage.status":
        return service.status()
    if route == "manage.validate":
        return service.validate()
    if route == "manage.config.show":
        return service.config_show(args.profile)
    if route == "manage.config.set":
        return service.config_set(args.profile, args.file)
    if route == "record.add":
        return service.record_add(args.source, args.conversion, args.metadata)
    if route == "record.edit":
        return service.record_edit(args.partition, args.record, args.metadata)
    if route == "record.show":
        return service.record_show(args.partition, args.record)
    if route == "record.delete":
        return service.record_delete(args.partition, args.record, args.expected_tree_sha256)
    raise CortexError("Route is not public", status=Status.USAGE_ERROR, code="unknown_route")


def _result(command: str, outcome: Outcome) -> dict[str, Any]:
    return {
        "status": outcome.status.value,
        "exit_code": int(outcome.status.exit_code),
        "command": command,
        "data": outcome.data,
        "issues": outcome.issues,
    }


def _error_result(command: str, exc: CortexError) -> dict[str, Any]:
    nested = exc.details.get("issues")
    issues = nested if isinstance(nested, list) else [exc.as_issue()]
    return {
        "status": exc.status.value,
        "exit_code": int(exc.status.exit_code),
        "command": command,
        "data": {},
        "issues": issues,
    }


def _render(payload: dict[str, Any], json_mode: bool) -> bool:
    if json_mode:
        try:
            rendered = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        except Exception as exc:
            detail = ascii(str(exc))
            _safe_write(sys.stderr, f"cortex: JSON output failure ({type(exc).__name__}): {detail}\n")
            return False
        _safe_write(sys.stdout, rendered)
        return True
    _safe_write(sys.stdout, f"status: {payload['status']}\n")
    data = payload["data"]
    for key, value in data.items():
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
        _safe_write(sys.stdout, f"{key}: {rendered}\n")
    for item in payload["issues"]:
        location = f" ({item['path']})" if item.get("path") else ""
        _safe_write(sys.stdout, f"error: {item['code']}{location}: {item['message']}\n")
    return True


def _safe_write(stream: Any, value: str) -> None:
    try:
        stream.write(value)
    except UnicodeEncodeError:
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            raise
        buffer.write(value.encode("utf-8"))


def _configure_utf8(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")


def _private_batch(raw: list[str]) -> int:
    marker = raw.index("--_record-add-batch")
    selectors, job_path = raw[:marker], raw[marker + 1]
    if len(selectors) == 2 and selectors[0] == "--workspace":
        service = CortexService(Path(selectors[1]), core=CoreRunner.from_config())
    elif len(selectors) == 4 and selectors[0] == "--kb-root" and selectors[2] == "--bundle-id":
        service = CortexService(None, kb_root=Path(selectors[1]), bundle_id=selectors[3], core=CoreRunner.from_config())
    else:
        raise CortexError("Invalid private batch selector", status=Status.USAGE_ERROR, code="invalid_selector")
    job = json.loads(Path(job_path).read_text("utf-8"))
    results: list[dict[str, Any]] = []
    uncertain = False
    with tempfile.TemporaryDirectory(prefix="cortex-batch-metadata-") as temporary:
        with service.batch_add_context():
            for index, item in enumerate(job["items"]):
                if uncertain:
                    try:
                        service.batch_revalidate()
                    except CortexError as exc:
                        if exc.code in {"core_protocol_error", "core_runner_start_failed", "core_runner_process_failed"}:
                            raise
                        results.append({"id": item["id"], "result": _error_result("record.add", exc)})
                        continue
                    uncertain = False
                metadata = Path(temporary) / f"item-{index}.json"
                metadata.write_text(json.dumps(item["metadata"], ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
                try:
                    outcome = service.record_add(item.get("source"), item.get("conversion"), str(metadata))
                    value = _result("record.add", outcome)
                except CortexError as exc:
                    if exc.code in {"core_protocol_error", "core_runner_start_failed", "core_runner_process_failed"}:
                        raise
                    value = _error_result("record.add", exc)
                    uncertain = value["status"] == "io_error"
                results.append({"id": item["id"], "result": value})
    succeeded = sum(item["result"]["status"] == "ok" for item in results)
    wrapper = {"schema_version": 1, "command": "record.add.batch", "items": results,
               "summary": {"total": len(results), "succeeded": succeeded, "failed": len(results) - succeeded}}
    _safe_write(sys.stdout, json.dumps(wrapper, ensure_ascii=True, separators=(",", ":")) + "\n")
    return 0 if succeeded == len(results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8(sys.stdout)
    _configure_utf8(sys.stderr)
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--_record-add-batch" in raw:
        try:
            return _private_batch(raw)
        except CortexError as exc:
            _safe_write(sys.stderr, f"cortex record batch error: {exc.code}\n")
            return 2
    json_mode = "--json" in raw
    if json_mode:
        raw = [item for item in raw if item != "--json"]
    command = "cli.parse"
    try:
        args = _parser().parse_args(raw)
        route = _route(args)
        command = route
        payload = _result(route, _dispatch(route, args))
    except CortexError as exc:
        payload = _error_result(command, exc)
    except KeyboardInterrupt:
        payload = _error_result(command, io_error("Command was interrupted", "interrupted"))
    except OSError as exc:
        payload = _error_result(command, io_error("Filesystem operation failed", os_error=str(exc)))
    except Exception as exc:
        payload = _error_result(command, io_error("Internal command failure", "internal_error", error_type=type(exc).__name__))
    if not _render(payload, json_mode):
        return int(Status.IO_ERROR.exit_code)
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
