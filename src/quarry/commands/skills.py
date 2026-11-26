"""Agent skill generation command: `quarry skills build`."""
from __future__ import annotations

from pathlib import Path

import click

from quarry import context, skills
from quarry.commands._state import reports_errors
from quarry.output import CONTEXT, emit, output_options

__all__ = ["skills_group"]


@click.group("skills", context_settings=CONTEXT)
@output_options
def skills_group() -> None:
    """The agent skills this checkout ships."""


@skills_group.command("build", context_settings=CONTEXT)
@output_options
@click.pass_context
@reports_errors
def skills_build(ctx: click.Context) -> None:
    """Regenerate the skills' resource notes from the catalog."""
    root = skills.SKILLS_ROOT        # read at call time
    changed, unchanged = skills.build(context.open_env().catalog, root)

    def shown(paths: list[Path]) -> list[str]:
        return [str(p.relative_to(root.parent)) for p in paths]

    emit(ctx, {"written": shown(changed), "unchanged": shown(unchanged)},
         "\n".join(f"wrote {p}" for p in shown(changed)) or "skills are current")
