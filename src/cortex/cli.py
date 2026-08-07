"""Cortex 4 command line boundary."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

from .commands import dispatch
from .constants import PUBLIC_LEAF_ROUTES
from .contracts import make_envelope
from .errors import CortexError, Status


class ContractParser(argparse.ArgumentParser):
    def error(self,message:str)->None:raise CortexError(message,status=Status.USAGE_ERROR,code="invalid_arguments")


def _mutation(parser:argparse.ArgumentParser)->None:
    parser.add_argument("--plan",help="Exact content-addressed MutationPlan2 id")
    parser.add_argument("--apply",action="store_true")


def _parser()->ContractParser:
    parser=ContractParser(prog="cortex")
    parser.add_argument("--workspace",default=".")
    parser.add_argument("--json",action="store_true")
    parser.add_argument("--version",action="version",version="cortex 4.0.0")
    top=parser.add_subparsers(dest="group",required=True)
    build=top.add_parser("build");build_sub=build.add_subparsers(dest="command",required=True)
    ingest=build_sub.add_parser("ingest");ingest.add_argument("--source",action="append",default=[]);ingest.add_argument("--tag",action="append",default=[]);ingest.add_argument("--context");ingest.add_argument("--proposal");ingest.add_argument("--replace-conflict",action="append",default=[]);ingest.add_argument("--sanitize-links",action="store_true");_mutation(ingest)
    manage=top.add_parser("manage");sub=manage.add_subparsers(dest="command",required=True)
    init=sub.add_parser("init");init.add_argument("--tag-schema");_mutation(init)
    status=sub.add_parser("status");status.add_argument("--kind",choices=("bundle","method"),default="bundle")
    config=sub.add_parser("config");config.add_argument("action",choices=("show","set"),nargs="?",default="show");config.add_argument("--file");_mutation(config)
    sub.add_parser("validate")
    sub.add_parser("index")
    repair=sub.add_parser("repair");repair.add_argument("--phase",choices=("structural","link-closure"),default="structural");_mutation(repair)
    rename=sub.add_parser("rename");rename.add_argument("--from",dest="old");rename.add_argument("--to",dest="new");_mutation(rename)
    retag=sub.add_parser("retag");retag.add_argument("action",choices=("set",),nargs="?");retag.add_argument("--reference");retag.add_argument("--tags");_mutation(retag)
    return parser


def _route(args:argparse.Namespace)->str:
    route=f"{args.group}.{args.command}"
    if route not in PUBLIC_LEAF_ROUTES:raise CortexError("Route is not public",status=Status.USAGE_ERROR,code="unknown_route",details={"route":route})
    return route


def _issue(exc:CortexError)->dict[str,Any]:
    return {"rule_id":"cli","code":exc.code,"severity":"error","message":str(exc),"path":exc.details.get("path"),"concept_id":None,"operation_id":None,"hint":None,"details":exc.details}


def _next(command:str,artifact_id:str,workspace:str)->str|None:
    prefix=f"cortex --workspace {shlex.quote(workspace)}"
    route={"manage.config":"manage config set"}.get(command,command.replace("."," "))
    if artifact_id.startswith("mutation-plan@"):return f"{prefix} {route} --plan {artifact_id} --apply"
    if artifact_id.startswith("ingest-context@"):return f"{prefix} build ingest --context {artifact_id} --proposal -"
    return None


def _render(payload:dict[str,Any],json_mode:bool,workspace:str)->None:
    if json_mode:
        sys.stdout.write(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n");return
    data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
    blocked_context=None
    if any(issue.get("code")=="source_link_closure_required" for issue in payload["issues"]):
        blocked_context=next((item["artifact_id"] for item in payload["artifacts"] if item["artifact_id"].startswith("ingest-context@")),None)
    primary=blocked_context or data.get("artifact_id")
    if not primary and payload.get("artifacts"):primary=payload["artifacts"][-1]["artifact_id"]
    sys.stdout.write(f"status: {payload['status']}\n")
    if primary:
        sys.stdout.write(f"artifact_id: {primary}\n")
        next_command=(f"cortex --workspace {shlex.quote(workspace)} build ingest --context {primary} --proposal - --sanitize-links" if blocked_context else _next(payload["command"],primary,workspace))
        if next_command:sys.stdout.write(f"next: {next_command}\n")
    for issue in payload["issues"]:
        location=f" ({issue['path']})" if issue.get("path") else ""
        sys.stdout.write(f"{issue['severity']}: {issue['code']}{location}: {issue['message']}\n")
        if issue.get("hint"):sys.stdout.write(f"  hint: {issue['hint']}\n")


def main(argv:Sequence[str]|None=None)->int:
    raw=list(sys.argv[1:] if argv is None else argv);json_mode="--json" in raw;raw=[item for item in raw if item!="--json"];command="cli.parse";workspace_text="."
    try:
        args=_parser().parse_args(raw);command=_route(args);workspace_text=str(Path(args.workspace).absolute())
        stdin_operands=[*getattr(args,"source",[]),getattr(args,"proposal",None),getattr(args,"tag_schema",None),getattr(args,"file",None)]
        if sum(item=="-" for item in stdin_operands)>1:raise CortexError("A command may consume stdin exactly once",status=Status.USAGE_ERROR,code="multiple_stdin_operands")
        if command=="build.ingest" and any(item=="-" for item in args.source):raise CortexError("--source - is unsupported because source custody requires a stable path",status=Status.USAGE_ERROR,code="source_stdin_unsupported")
        outcome=dispatch(command,args,Path(args.workspace).absolute())
        payload=make_envelope(command,outcome.status,outcome.data,outcome.data_schema_id,issues=outcome.issues,artifacts=outcome.artifacts)
    except CortexError as exc:payload=make_envelope(command,exc.status,issues=[_issue(exc)])
    except KeyboardInterrupt:
        exc=CortexError("Interrupted",status=Status.INTERRUPTED,code="keyboard_interrupt");payload=make_envelope(command,exc.status,issues=[_issue(exc)])
    except Exception as exc:
        error=CortexError("Internal command interruption",status=Status.INTERRUPTED,code="internal_error",details={"type":type(exc).__name__});payload=make_envelope(command,error.status,issues=[_issue(error)])
    _render(payload,json_mode,workspace_text);return int(payload["exit_code"])


if __name__=="__main__":raise SystemExit(main())
