"""The `quarry` command: global option hoisting, usage-error contract, and dispatch."""
from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path

import click

from quarry.commands._state import State
from quarry.commands.catalog import check_command, domains, resources
from quarry.commands.id import id_group
from quarry.commands.records import files_command, search_command, show_command
from quarry.commands.skills import skills_group
from quarry.commands.term import term_group
from quarry.output import CONTEXT, output_options

__all__ = ["Cli", "hoist_globals", "main"]

GLOBAL_VALUED = frozenset({"-C", "--config"})
GLOBAL_FLAGS = frozenset({"--trace"})


def hoist_globals(args: list[str]) -> list[str]:
    """Move root-only global options to the front, wherever they were written. `--` ends the
    options: nothing after it moves. Local option values remain with their options."""
    command: click.Command = main
    front, rest, i = [], [], 0
    while i < len(args):
        word = args[i]
        if word == "--":
            rest += args[i:]
            break
        if (word in GLOBAL_FLAGS or word.startswith("--config=")
                or word.startswith("-C") and word != "-C"):
            front.append(word)
        elif word in GLOBAL_VALUED:
            front += args[i:i + 2]
            i += 1
        else:
            rest.append(word)
            option = next((p for p in command.params if isinstance(p, click.Option)
                           and word in p.opts and not p.is_flag and not p.count), None)
            if option is not None:
                rest += args[i + 1:i + 1 + option.nargs]
                i += option.nargs
            elif isinstance(command, click.Group) and word in command.commands:
                command = command.commands[word]
        i += 1
    return front + rest


class Cli(click.Group):
    """Global options are accepted before and after the command; a usage error prints one line
    naming the problem and one pointing at --help (the CLI vocabulary's exit-code contract)."""

    def main(self, args=None, **kwargs):
        kwargs.setdefault("prog_name", "quarry")
        kwargs.pop("standalone_mode", None)
        args = hoist_globals(list(sys.argv[1:] if args is None else args))
        try:
            rv = super().main(args, standalone_mode=False, **kwargs)
        except click.UsageError as error:
            prog = error.ctx.command_path if error.ctx else kwargs["prog_name"]
            click.echo(f"{prog}: {error.format_message()}", err=True)
            click.echo(f"Run '{kwargs['prog_name']} --help'.", err=True)
            sys.exit(2)
        except click.ClickException as error:
            error.show()
            sys.exit(error.exit_code)
        except click.Abort:
            click.echo("Aborted!", err=True)
            sys.exit(1)
        sys.exit(rv or 0)


@click.group(cls=Cli, context_settings=CONTEXT)
@click.version_option(importlib.metadata.version("quarry"), "-V", "--version", prog_name="quarry",
                      message="%(prog)s %(version)s")
@output_options
@click.option("-C", "chdir", type=click.Path(file_okay=False, exists=True, path_type=Path),
              help="Run as if started in that directory.")
@click.option("--config", type=click.Path(dir_okay=False, path_type=Path),
              help="Read this file instead of the nearest quarry.toml.")
@click.option("--trace", is_flag=True, envvar="QUARRY_TRACE", help="Print every request to stderr (also QUARRY_TRACE).")
@click.pass_context
def main(ctx: click.Context, chdir: Path | None, config: Path | None, trace: bool) -> None:
    """quarry: one CLI over scientific resources, with domains as lenses."""
    if chdir is not None:
        os.chdir(chdir)
    ctx.obj = State(config=config.resolve() if config is not None else None, trace=trace)


@main.command("help", context_settings=CONTEXT)
@click.argument("words", nargs=-1)
@click.pass_context
def help_command(ctx: click.Context, words: tuple[str, ...]) -> None:
    """Print a command's help: `quarry help resources list`."""
    command: click.Command = main
    remaining = list(words)
    while remaining and isinstance(command, click.Group) and remaining[0] in command.commands:
        command = command.commands[remaining.pop(0)]
    if remaining:
        raise click.UsageError(f"unknown command {' '.join(words)}", ctx)
    click.echo(command.get_help(click.Context(command, info_name=" ".join(["quarry", *words]), **CONTEXT)))


for _subcommand in (
    domains,
    resources,
    id_group,
    check_command,
    search_command,
    show_command,
    files_command,
    term_group,
    skills_group,
):
    main.add_command(_subcommand)


if __name__ == "__main__":
    main()
