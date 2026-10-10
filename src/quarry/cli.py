"""The `quarry` command."""
import importlib.metadata
import sys

import click

from quarry.output import CONTEXT, output_options


class Cli(click.Group):
    """A usage error prints one line naming the problem and one pointing at --help, per the
    CLI vocabulary's exit-code and error contract."""

    def main(self, *args, **kwargs):
        kwargs.setdefault("prog_name", "quarry")
        kwargs.pop("standalone_mode", None)
        try:
            rv = super().main(*args, standalone_mode=False, **kwargs)
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
def main() -> None:
    """quarry: one CLI over scientific resources, with domains as lenses."""


@main.command("help", context_settings=CONTEXT)
@click.argument("words", nargs=-1)
@click.pass_context
def help_command(ctx: click.Context, words: tuple[str, ...]) -> None:
    """Print a command's help: `quarry help resources list`."""
    command, remaining = main, list(words)
    while remaining and isinstance(command, click.Group) and remaining[0] in command.commands:
        command = command.commands[remaining.pop(0)]
    if remaining:
        raise click.UsageError(f"unknown command {' '.join(words)}", ctx)
    click.echo(command.get_help(click.Context(command, info_name=" ".join(["quarry", *words]), **CONTEXT)))


if __name__ == "__main__":
    main()
