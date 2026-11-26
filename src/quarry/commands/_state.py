"""CLI invocation state, error reporting, and finding emission for command modules."""
from __future__ import annotations

import dataclasses
import functools
from pathlib import Path

import click

from quarry.catalog import Finding
from quarry.errors import QuarryError
from quarry.output import emit, fail

__all__ = ["State", "emit_findings", "reports_errors", "state"]


@dataclasses.dataclass(frozen=True)
class State:
    config: Path | None
    trace: bool


def state(ctx: click.Context) -> State:
    found = ctx.find_object(State)
    if found is None:
        raise RuntimeError("CLI State not initialized on Click context")
    return found


def reports_errors(f):
    """A QuarryError becomes the error object (json) or one line (pretty), exit 1."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except QuarryError as error:
            fail(click.get_current_context(), error.kind, error.detail)
    return wrapper


def emit_findings(ctx: click.Context, findings: list[Finding]) -> None:
    if findings:
        emit(ctx, {"findings": [f.as_dict() for f in findings]},
             "\n".join(f"{f.where}: {f.kind}: {f.detail}" for f in findings))
        ctx.exit(1)
