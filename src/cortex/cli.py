"""Closed Cortex 5 command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .constants import PUBLIC_ROUTES, VERSION
from .errors import CortexError, Status, io_error
from .service import CortexService, Outcome


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CortexError(message, status=Status.USAGE_ERROR, code="invalid_arguments")


def _parser() -> ContractParser:
    parser = ContractParser(prog="cortex")
    parser.add_argument("--json", action="store_true", help="emit one machine-readable Result")
    parser.add_argument("--workspace", required=True, help="one Cortex 5 record-KB root")
    parser.add_argument("--version", action="version", version=f"cortex {VERSION}")
    groups = parser.add_subparsers(dest="group", required=True)

    manage = groups.add_parser("manage")
    manage_commands = manage.add_subparsers(dest="manage_command", required=True)
    manage_commands.add_parser("init")
    manage_commands.add_parser("status")
    manage_commands.add_parser("validate")
    config = manage_commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    show = config_commands.add_parser("show")
    show.add_argument("--profile", required=True, choices=("tags", "layout"))
    set_command = config_commands.add_parser("set")
    set_command.add_argument("--profile", required=True, choices=("tags", "layout"))
    set_command.add_argument("--file", required=True)

    record = groups.add_parser("record")
    record_commands = record.add_subparsers(dest="record_command", required=True)
    add = record_commands.add_parser("add")
    add.add_argument("--source", required=True)
    add.add_argument("--conversion")
    add.add_argument("--metadata", required=True)
    edit = record_commands.add_parser("edit")
    edit.add_argument("--record", required=True)
    edit.add_argument("--metadata", required=True)
    return parser


def _route(args: argparse.Namespace) -> str:
    if args.group == "manage":
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
    service = CortexService(Path(args.workspace))
    if route == "manage.init":
        return service.init()
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
        return service.record_edit(args.record, args.metadata)
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


def _render(payload: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        return
    sys.stdout.write(f"status: {payload['status']}\n")
    data = payload["data"]
    for key, value in data.items():
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
        sys.stdout.write(f"{key}: {rendered}\n")
    for item in payload["issues"]:
        location = f" ({item['path']})" if item.get("path") else ""
        sys.stdout.write(f"error: {item['code']}{location}: {item['message']}\n")


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
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
    _render(payload, json_mode)
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
