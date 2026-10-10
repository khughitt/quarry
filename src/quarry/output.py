"""The CLI vocabulary's output mode for click: a decorator any command takes, the mode
resolved from flags then QUARRY_FORMAT, and the json failure object."""
import json
import os
import sys
from typing import NoReturn

import click

CONTEXT = {"help_option_names": ["-h", "--help"]}


def _choose(mode: str):
    def callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
        if not value:
            return
        seen = ctx.meta.get("quarry.mode")
        if seen is not None and seen != mode:
            raise click.UsageError("--json and --pretty are mutually exclusive", ctx)
        ctx.meta["quarry.mode"] = mode
    return callback


def output_options(command):
    """Two flags with their own destinations, so both callbacks run when both are given and
    the conflict is caught on one command and across the group/command boundary."""
    command = click.option("--json", "json_mode", is_flag=True, expose_value=False, callback=_choose("json"),
                           help="Emit one JSON value on stdout.")(command)
    return click.option("--pretty", "pretty_mode", is_flag=True, expose_value=False, callback=_choose("pretty"),
                        help="Emit text for a person (default; also QUARRY_FORMAT).")(command)


def mode(ctx: click.Context) -> str:
    chosen = ctx.meta.get("quarry.mode") or os.environ.get("QUARRY_FORMAT", "pretty")
    if chosen not in ("json", "pretty"):
        raise click.UsageError(f"QUARRY_FORMAT must be json or pretty, got {chosen!r}", ctx)
    return chosen


def emit(ctx: click.Context, report: dict, text: str) -> None:
    click.echo(json.dumps(report) if mode(ctx) == "json" else text)


def fail(ctx: click.Context, kind: str, detail: str) -> NoReturn:
    if mode(ctx) == "json":
        sys.stderr.write(json.dumps({"error": {"kind": kind, "detail": detail}}) + "\n")
    else:
        sys.stderr.write(f"quarry: {detail}\n")
    ctx.exit(1)
