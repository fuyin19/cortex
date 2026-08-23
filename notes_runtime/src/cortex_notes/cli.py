"""Command-line interface for the skill-local Notes runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from . import core


class Usage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Usage("invalid_arguments")


def _parser() -> Parser:
    parser = Parser(prog="cortex-notes", add_help=True)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=str(core.DEFAULT_ROOT))
    parser.add_argument("--tools-root", default=str(core.DEFAULT_TOOLS_ROOT))
    families = parser.add_subparsers(dest="family")
    registry = families.add_parser("registry"); registry_actions = registry.add_subparsers(dest="action", required=True)
    registry_actions.add_parser("init"); registry_actions.add_parser("show"); registry_actions.add_parser("validate")
    registry_resolve = registry_actions.add_parser("resolve"); registry_resolve.add_argument("--bundle", required=True)
    bundle = families.add_parser("bundle"); bundle_actions = bundle.add_subparsers(dest="action", required=True)
    for action in ("init", "show", "resolve", "validate"):
        item = bundle_actions.add_parser(action); item.add_argument("--bundle", required=True)
    part = bundle_actions.add_parser("partition-add"); part.add_argument("--bundle", required=True); part.add_argument("--partition", required=True)
    note = families.add_parser("note"); note_actions = note.add_subparsers(dest="action", required=True)
    add = note_actions.add_parser("add"); add.add_argument("--bundle", required=True); add.add_argument("--partition"); add.add_argument("--title", required=True); add.add_argument("--body-file", required=True); add.add_argument("--timestamp")
    listing = note_actions.add_parser("list"); listing.add_argument("--bundle", required=True); listing.add_argument("--partition", required=True); listing.add_argument("--state", choices=("active", "archived", "all"), default="active")
    for action in ("show", "edit", "archive", "delete"):
        item = note_actions.add_parser(action); item.add_argument("--bundle", required=True); item.add_argument("--partition", required=True); item.add_argument("--note", required=True); item.add_argument("--archived", action="store_true")
        if action in ("edit", "archive", "delete"): item.add_argument("--expected-tree-sha256", required=True)
        if action == "edit": item.add_argument("--body-file", required=True)
        if action == "delete": item.add_argument("--confirmed", required=True)
    return parser


def _command(args: argparse.Namespace) -> str:
    action = str(getattr(args, "action", "usage")).replace("partition-add", "partition.add")
    return "notes." + str(args.family or "cli") + "." + action


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    root, tools = Path(args.root), Path(args.tools_root)
    if args.family == "registry":
        if args.action == "init": return core.registry_init(root)
        if args.action == "show": return core.registry_show(root)
        if args.action == "resolve": return core.registry_resolve(root, args.bundle)
        return core.validate(root)
    if args.family == "bundle":
        if args.action == "init": return core.bundle_init(root, args.bundle, tools)
        if args.action == "show": return core.bundle_show(root, args.bundle)
        if args.action == "resolve": return core.bundle_resolve(root, args.bundle)
        if args.action == "validate": return core.validate(root, args.bundle)
        return core.partition_add(root, args.bundle, args.partition, tools)
    if args.action == "add": return core.note_add(root, tools, args.bundle, args.partition, args.title, Path(args.body_file), args.timestamp)
    if args.action == "list": return core.note_list(root, args.bundle, args.partition, None if args.state == "all" else args.state == "archived")
    if args.action == "show": return core.note_show(root, args.bundle, args.partition, args.note, args.archived)
    if args.action == "edit": return core.note_edit(root, args.bundle, args.partition, args.note, args.archived, Path(args.body_file), args.expected_tree_sha256)
    if args.action == "archive":
        if args.archived: raise core.NotesError("usage_error", "already_archived")
        return core.note_archive(root, args.bundle, args.partition, args.note, args.expected_tree_sha256)
    return core.note_delete(root, args.bundle, args.partition, args.note, args.archived, args.expected_tree_sha256, args.confirmed)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    machine = "--json" in argv
    command = "notes.cli"
    try:
        args = _parser().parse_args(argv)
        if args.version:
            sys.stdout.write(f"cortex-notes {__version__}\n"); return 0
        if args.family is None: raise Usage("command_required")
        command = _command(args); value = _dispatch(args)
    except Usage as exc:
        value = core.failure(command, core.NotesError("usage_error", str(exc)))
    except core.NotesError as exc:
        value = core.failure(command, exc)
    except KeyboardInterrupt:
        value = core.failure(command, core.NotesError("io_error", "interrupted"))
    except Exception:
        value = core.failure(command, core.NotesError("io_error", "unexpected_failure"))
    raw = json.dumps(value, ensure_ascii=machine, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(raw.encode("utf-8" if machine else "utf-8"))
    return int(value["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
