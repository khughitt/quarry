"""Record verbs: `quarry search`, `show`, and `files`."""
from __future__ import annotations

import json
import os
from pathlib import Path

import click

from quarry import acquisition, context
from quarry import files as files_mod
from quarry import ids as idmod
from quarry import search as search_mod
from quarry import terms as terms_mod
from quarry.adapters import base as adapter_base
from quarry.catalog import DATA_ROLES, Resource
from quarry.commands._state import reports_errors, state
from quarry.errors import QuarryError
from quarry.lens import resolve_domain
from quarry.output import CONTEXT, emit, output_options
from quarry.query import INSTANT_TIME, WHEN, WINDOW, Query
from quarry.results import Result

__all__ = ["files_command", "search_command", "show_command"]


def _pretty_coverage(c: dict) -> str:
    if c["complete"] == "skipped":
        return f"# {c['resource']}: skipped ({c['reason']})"
    how = ", ".join([c["complete"], c["match"], *([c["time"]] if "time" in c else [])])
    return f"# {c['resource']}: {c['returned']} of {c['total'] if c['total'] is not None else '?'} ({how})"


def _pretty_search(report: dict) -> str:
    lines = [f"{r['id']}\t{r.get('date', '-')}\t{r['title']}" for r in report["results"]]
    lines += [_pretty_coverage(c) for c in report["coverage"]]
    lines += [f"warning: {w}" for w in report["warnings"]]
    return "\n".join(lines)


@click.command("search", context_settings=CONTEXT)
@output_options
@click.argument("phrases", nargs=-1)
@click.option("--in", "in_", multiple=True, help="Search this resource; repeatable. Replaces the domain's default set.")
@click.option("--domain", help="Search this domain's default resources (also QUARRY_DOMAIN).")
@click.option("--organism", help="Biology: a taxon name or an ncbitaxon: CURIE.")
@click.option("--during", type=WINDOW, help="Records observed during START/END (dates, or UTC datetimes ending in Z; "
                                            "a date END includes its day), where a domain declares it.")
@click.option("--at", "at", type=INSTANT_TIME, help="The record observed nearest this UTC datetime (ending in Z), "
                                                    "where a domain declares it.")
@click.option("--instrument", help="Instrument name or source selector, where a domain declares it.")
@click.option("--observatory", help="Observatory or mission name, where a domain declares it.")
@click.option("--measurement", help="SPASE measurement type, where a domain declares it.")
@click.option("--exact", is_flag=True, help="Require exact phrase matching from every resource searched.")
@click.option("--native", help="One resource's own query, sent unchanged; needs exactly one --in.")
@click.option("--allow-partial", is_flag=True, help="Report a failed resource in coverage instead of failing.")
@click.option("--since", type=WHEN, help="Records released on or after this date (YYYY-MM-DD, <n>d, <n>w).")
@click.option("--until", type=WHEN, help="Records released on or before this date.")
@click.option("-n", "--limit", type=int, help="At most this many records from each resource.")
@click.pass_context
@reports_errors
def search_command(ctx, phrases, in_, domain, organism, during, at, instrument, observatory, measurement,
                   exact, native, allow_partial, since, until, limit):
    """Search records across data-holding resources."""
    if native is not None and len(in_) != 1:
        raise click.UsageError("--native needs exactly one --in", ctx)
    filters = {name: value for name, value in (
        ("organism", organism),
        ("instrument", instrument),
        ("observatory", observatory),
        ("measurement", measurement),
    ) if value is not None}
    try:
        query = Query(phrases=tuple(phrases), filters=filters, since=since,
                      until=until, limit=limit, exact=exact, native=native, during=during, at=at)
    except ValueError as error:
        raise click.UsageError(str(error), ctx) from None
    env = context.open_env()
    chosen = resolve_domain(domain, os.environ, state(ctx).config, Path.cwd(), env.catalog)
    for name in sorted(query.filter_names()):
        if name not in env.catalog.domain_filters(chosen):
            raise QuarryError("unsupported_filter", f"--{name} is not a filter of the {chosen} domain")
    resource_ids = list(dict.fromkeys(in_)) or env.catalog.search_set(chosen)
    if not resource_ids:
        raise QuarryError("nothing_to_search", f"the {chosen} domain searches nothing by default; name resources with --in")
    with context.make_client(state(ctx).trace) as client:
        report = search_mod.run(catalog=env.catalog, client=client, query=query, resource_ids=resource_ids,
                                domain=chosen, allow_partial=allow_partial, from_defaults=not in_)
    emit(ctx, report, _pretty_search(report))


def _owned(env: context.Env, text: str) -> tuple[idmod.Curie, Resource]:
    """The CURIE and the data-holding resource that owns its prefix."""
    parsed = idmod.parse(text, env.registry)
    owner = env.catalog.owner(parsed.prefix)
    if owner is None:
        raise QuarryError("no_owner", f"no catalog resource owns {parsed.prefix}: ids under it are cross-references only")
    if not set(owner.roles) & DATA_ROLES:
        hint = f"; {parsed} is a term: `quarry term show {parsed}`" if set(owner.roles) & terms_mod.TERM_ROLES else ""
        raise QuarryError("unsupported_role", f"{owner.id} holds no records{hint}")
    return parsed, owner


def _pretty_record(result: Result) -> str:
    lines = [f"{result.id}\t{result.date or '-'}\t{result.title}"]
    core = result.domain_fields.get("core", {})
    lines += [f"{key}: {core[key]}" for key in ("open_access", "in_epmc", "in_pmc", "license") if key in core]
    return "\n".join(lines)


@click.command("show", context_settings=CONTEXT)
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
    emit(ctx, result.as_dict(), _pretty_record(result))


def _pretty_files(listed: list[files_mod.File]) -> str:
    lines = []
    for f in listed:
        lines.append(f"{f.name}\t{f.size if f.size is not None else '-'}\t{f.urls[0] if f.urls else '-'}")
        if f.license is not None:
            lines.append(f"  license: {f.license}")
    return "\n".join([*lines, f"# {len(listed)} files"])


@click.command("files", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.option("--file", "globs", multiple=True, help="Only files whose name matches this glob; repeatable, widening.")
@click.option("--acquisition", "as_request", is_flag=True, help="Emit a request for a consumer to retrieve the selected files.")
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
