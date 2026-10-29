"""The `quarry` command."""
import dataclasses
import functools
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import click

from quarry import acquisition, context
from quarry import files as files_mod
from quarry import ids as idmod
from quarry import search as search_mod
from quarry.adapters import base as adapter_base
from quarry.catalog import DATA_ROLES, ROLES, ROUTE_KINDS, CatalogError, Finding, Resource
from quarry.errors import QuarryError
from quarry.lens import resolve_domain
from quarry.output import CONTEXT, emit, fail, output_options
from quarry.query import WHEN, Query


GLOBAL_VALUED = frozenset({"-C", "--config"})
GLOBAL_FLAGS = frozenset({"--trace"})


def hoist_globals(args: list[str]) -> list[str]:
    """Move root-only global options to the front, wherever they were written. `--` ends the
    options: nothing after it moves. Local option values remain with their options."""
    command = main
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


@dataclasses.dataclass(frozen=True)
class State:
    config: Path | None
    trace: bool


def reports_errors(f):
    """A QuarryError becomes the error object (json) or one line (pretty), exit 1."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except QuarryError as error:
            fail(click.get_current_context(), error.kind, error.detail)
    return wrapper


def state(ctx: click.Context) -> State:
    return ctx.find_object(State)


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
    command, remaining = main, list(words)
    while remaining and isinstance(command, click.Group) and remaining[0] in command.commands:
        command = command.commands[remaining.pop(0)]
    if remaining:
        raise click.UsageError(f"unknown command {' '.join(words)}", ctx)
    click.echo(command.get_help(click.Context(command, info_name=" ".join(["quarry", *words]), **CONTEXT)))


def _findings(ctx: click.Context, findings: list[Finding]) -> None:
    if findings:
        emit(ctx, {"findings": [f.as_dict() for f in findings]},
             "\n".join(f"{f.where}: {f.kind}: {f.detail}" for f in findings))
        ctx.exit(1)


@main.group(context_settings=CONTEXT)
@output_options
def domains() -> None:
    """The domains and their resources."""


@domains.command("list", context_settings=CONTEXT)
@output_options
@click.pass_context
@reports_errors
def domains_list(ctx: click.Context) -> None:
    """List the domains, their resources and beliefs contracts."""
    env = context.open_env()
    rows = [{"id": d.id, "summary": d.summary, "includes": list(d.includes),
             "resources": env.catalog.effective_resources(d.id), "beliefs_contract": d.beliefs_contract,
             "beliefs_contract_version": d.beliefs_contract_version}
            for d in sorted(env.catalog.domains.values(), key=lambda d: d.id)]
    emit(ctx, {"domains": rows},
         "\n".join(f"{r['id']}\t{', '.join(r['resources']) or '-'}\t{r['beliefs_contract'] or '-'}" for r in rows))


@main.group(context_settings=CONTEXT)
@output_options
def resources() -> None:
    """The catalog of resources."""


@resources.command("list", context_settings=CONTEXT)
@output_options
@click.option("--domain", help="List this domain's resources (also QUARRY_DOMAIN).")
@click.option("--role", type=click.Choice(ROLES), help="Only resources with this role.")
@click.option("--route", type=click.Choice(ROUTE_KINDS), help="Only resources with a route of this kind.")
@click.pass_context
@reports_errors
def resources_list(ctx: click.Context, domain: str | None, role: str | None, route: str | None) -> None:
    """List the resources a domain selects."""
    env = context.open_env()
    chosen = resolve_domain(domain, os.environ, state(ctx).config, Path.cwd(), env.catalog)
    rows = []
    for rid in env.catalog.effective_resources(chosen):
        r = env.catalog.resources[rid]
        if role and role not in r.roles or route and route not in {x.kind for x in r.routes}:
            continue
        rows.append({"id": r.id, "name": r.name, "roles": list(r.roles), "prefixes": list(r.owned_prefixes())})
    emit(ctx, {"domain": chosen, "resources": rows},
         "\n".join(f"{r['id']}\t{', '.join(r['roles'])}\t{r['name']}" for r in rows) or f"({chosen}: no resources)")


@resources.command("show", context_settings=CONTEXT)
@output_options
@click.argument("resource")
@click.pass_context
@reports_errors
def resources_show(ctx: click.Context, resource: str) -> None:
    """Show one resource's roles, routes, limits and pitfalls."""
    env = context.open_env()
    r = env.catalog.resources.get(resource)
    if r is None:
        raise QuarryError("unknown_resource", f"no resource {resource!r}")
    report = dataclasses.asdict(r)
    report["local_prefixes"] = {k: dataclasses.asdict(v) for k, v in r.local_prefixes.items()}
    lines = [f"{r.name} ({r.id}): {', '.join(r.roles)}"]
    lines += [f"  route {x.kind} {x.url} (service {x.service}, every {x.min_interval}s)" for x in r.routes]
    lines += [f"  pitfall: {p.summary} -> {p.handling}" for p in r.pitfalls]
    emit(ctx, report, "\n".join(lines))


@main.group("id", context_settings=CONTEXT)
@output_options
def id_group() -> None:
    """Identifiers."""


@id_group.command("check", context_settings=CONTEXT)
@output_options
@click.argument("ids", nargs=-1, required=True)
@click.pass_context
@reports_errors
def id_check(ctx: click.Context, ids: tuple[str, ...]) -> None:
    """Check that identifiers are well formed for their prefixes."""
    _findings(ctx, idmod.check_ids(ids, context.open_env().registry))


@id_group.command("normalize", context_settings=CONTEXT)
@output_options
@click.argument("id")
@click.pass_context
@reports_errors
def id_normalize(ctx: click.Context, id: str) -> None:
    """Print an identifier as a canonical CURIE."""
    env = context.open_env()
    owned = [p for r in env.catalog.resources.values() for p in r.owned_prefixes()]
    curie = idmod.normalize(id, env.registry, owned)
    emit(ctx, {"id": str(curie)}, str(curie))


@id_group.command("resolve", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.pass_context
@reports_errors
def id_resolve(ctx: click.Context, curie: str) -> None:
    """Print the URLs that resolve a CURIE."""
    env = context.open_env()
    parsed = idmod.parse(curie, env.registry)
    urls = idmod.resolve(parsed, env.registry)
    if not urls:
        raise QuarryError("no_resolver", f"no URL format is known for {parsed.prefix}")
    emit(ctx, {"id": str(parsed), "urls": urls}, "\n".join(urls))


@main.command("check", context_settings=CONTEXT)
@output_options
@click.pass_context
@reports_errors
def check_command(ctx: click.Context) -> None:
    """Validate the catalog."""
    from quarry import catalog as catalog_mod
    try:
        env = context.load_unchecked()
    except CatalogError as error:
        _findings(ctx, [Finding("catalog_invalid", "catalog", error.detail)])
        return
    _findings(ctx, catalog_mod.check(env.catalog, env.registry, collisions=env.collisions))


def _pretty_coverage(c: dict) -> str:
    if c["complete"] == "skipped":
        return f"# {c['resource']}: skipped ({c['reason']})"
    return f"# {c['resource']}: {c['returned']} of {c['total'] if c['total'] is not None else '?'} ({c['complete']}, {c['match']})"


def _pretty_search(report: dict) -> str:
    lines = [f"{r['id']}\t{r.get('date', '-')}\t{r['title']}" for r in report["results"]]
    lines += [_pretty_coverage(c) for c in report["coverage"]]
    lines += [f"warning: {w}" for w in report["warnings"]]
    return "\n".join(lines)


@main.command("search", context_settings=CONTEXT)
@output_options
@click.argument("phrases", nargs=-1)
@click.option("--in", "in_", multiple=True, help="Search this resource; repeatable. Replaces the domain's default set.")
@click.option("--domain", help="Search this domain's default resources (also QUARRY_DOMAIN).")
@click.option("--organism", help="Biology: a taxon name or an ncbitaxon: CURIE.")
@click.option("--exact", is_flag=True, help="Require exact phrase matching from every resource searched.")
@click.option("--native", help="One resource's own query, sent unchanged; needs exactly one --in.")
@click.option("--allow-partial", is_flag=True, help="Report a failed resource in coverage instead of failing.")
@click.option("--since", type=WHEN, help="Records released on or after this date (YYYY-MM-DD, <n>d, <n>w).")
@click.option("--until", type=WHEN, help="Records released on or before this date.")
@click.option("-n", "--limit", type=int, help="At most this many records from each resource.")
@click.pass_context
@reports_errors
def search_command(ctx, phrases, in_, domain, organism, exact, native, allow_partial, since, until, limit):
    """Search records across data-holding resources."""
    if native is not None and len(in_) != 1:
        raise click.UsageError("--native needs exactly one --in", ctx)
    try:
        query = Query(phrases=tuple(phrases), filters={"organism": organism} if organism is not None else {}, since=since,
                      until=until, limit=limit, exact=exact, native=native)
    except ValueError as error:
        raise click.UsageError(str(error), ctx) from None
    env = context.open_env()
    chosen = resolve_domain(domain, os.environ, state(ctx).config, Path.cwd(), env.catalog)
    for name in query.filters:
        if name not in env.catalog.domain_filters(chosen):
            raise QuarryError("unsupported_filter", f"--{name} is not a filter of the {chosen} domain")
    resource_ids = list(in_) or env.catalog.search_set(chosen)
    if not resource_ids:
        raise QuarryError("nothing_to_search", f"the {chosen} domain searches nothing by default; name resources with --in")
    with context.make_client(state(ctx).trace) as client:
        report = search_mod.run(catalog=env.catalog, client=client, query=query, resource_ids=resource_ids,
                                domain=chosen, allow_partial=allow_partial, from_defaults=not in_)
    emit(ctx, report, _pretty_search(report))


def _owned(env: context.Env, text: str) -> tuple[idmod.Curie, Resource]:
    """The CURIE and the data-holding resource that owns its prefix (spec §7)."""
    parsed = idmod.parse(text, env.registry)
    owner = env.catalog.owner(parsed.prefix)
    if owner is None:
        raise QuarryError("no_owner", f"no catalog resource owns {parsed.prefix}: ids under it are cross-references only")
    if not set(owner.roles) & DATA_ROLES:
        raise QuarryError("unsupported_role", f"{owner.id} holds no records")
    return parsed, owner


@main.command("show", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.pass_context
@reports_errors
def show_command(ctx: click.Context, curie: str) -> None:
    """Show one record."""
    env = context.open_env()
    parsed, owner = _owned(env, curie)
    with context.make_client(state(ctx).trace) as client:
        result = adapter_base.make(owner).show(client, parsed)
    emit(ctx, result.as_dict(), f"{result.id}\t{result.date or '-'}\t{result.title}")


def _pretty_files(listed: list[files_mod.File]) -> str:
    lines = [f"{f.name}\t{f.size if f.size is not None else '-'}\t{f.urls[0] if f.urls else '-'}" for f in listed]
    return "\n".join([*lines, f"# {len(listed)} files"])


@main.command("files", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.option("--file", "globs", multiple=True, help="Only files whose name matches this glob; repeatable, widening.")
@click.option("--acquisition", "as_request", is_flag=True, help="Print the acquisition request the kernel takes (spec §9).")
@click.pass_context
@reports_errors
def files_command(ctx: click.Context, curie: str, globs: tuple[str, ...], as_request: bool) -> None:
    """List a record's files, or the acquisition request the kernel takes for them."""
    env = context.open_env()
    parsed, owner = _owned(env, curie)
    with context.make_client(state(ctx).trace) as client:
        record, listed = adapter_base.make(owner).files(client, parsed)
    listed = files_mod.select(listed, globs)
    if as_request:
        request = acquisition.build(record, listed)
        emit(ctx, request, json.dumps(request, indent=1))
        return
    emit(ctx, {"id": record.id, "resource": owner.id, "files": [f.as_dict() for f in listed]}, _pretty_files(listed))


if __name__ == "__main__":
    main()
