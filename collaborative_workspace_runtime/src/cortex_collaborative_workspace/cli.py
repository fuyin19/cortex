"""Closed command-line interface for Collaborative Workspace v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from . import workspace


class Usage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise Usage("invalid_arguments")


def _parser() -> Parser:
    parser = Parser(prog="cortex-collaborative-workspace")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="action")
    for action in ("prepare", "status", "validate"):
        command = commands.add_parser(action)
        command.add_argument("--root", required=True)
        if action == "prepare":
            command.add_argument("--outdate", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    machine = "--json" in argv
    command = "collaborative_workspace.cli"
    try:
        args = _parser().parse_args(argv)
        if args.version:
            sys.stdout.write(f"cortex-collaborative-workspace {__version__}\n")
            return 0
        if args.action is None:
            raise Usage("command_required")
        command = "collaborative_workspace." + args.action
        root = Path(args.root)
        if args.action == "prepare":
            value = workspace.prepare(root, tuple(args.outdate))
        elif args.action == "status":
            value = workspace.status(root)
        else:
            value = workspace.validate(root)
    except Usage as exc:
        value = workspace.failure(command, workspace.WorkspaceError("usage_error", str(exc)))
    except workspace.WorkspaceError as exc:
        value = workspace.failure(command, exc)
    except workspace.RuntimeFallback:
        return 75
    except KeyboardInterrupt:
        value = workspace.failure(command, workspace.WorkspaceError("io_error", "interrupted"))
    except Exception:
        value = workspace.failure(command, workspace.WorkspaceError("io_error", "unexpected_failure"))
    raw = json.dumps(value, ensure_ascii=machine, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(raw.encode("utf-8"))
    return int(value["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
