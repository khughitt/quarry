"""Identifier commands: `quarry id check`, `normalize`, and `resolve`."""
from __future__ import annotations

import click

from quarry import context
from quarry import ids as idmod
from quarry import terms as terms_mod
from quarry.catalog import DATA_ROLES
from quarry.commands._state import emit_findings, reports_errors
from quarry.errors import QuarryError
from quarry.output import CONTEXT, emit, output_options

__all__ = ["id_group"]


@click.group("id", context_settings=CONTEXT)
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
    emit_findings(ctx, idmod.check_ids(ids, context.open_env().registry))


@id_group.command("normalize", context_settings=CONTEXT)
@output_options
@click.argument("id")
@click.pass_context
@reports_errors
def id_normalize(ctx: click.Context, id: str) -> None:
    """Print an identifier as a canonical CURIE."""
    env = context.open_env()
    # Term-only prefixes and bare_normalize=false resources stay out: a bare word or number is not a term id
    # (ncbitaxon's pattern takes any word), and opted-out resources require a qualified <prefix>:<id>.
    owned = [p for r in env.catalog.resources.values() if set(r.roles) & DATA_ROLES and r.bare_normalize
             for p in r.owned_prefixes()]
    try:
        curie = idmod.normalize(id, env.registry, owned)
    except QuarryError as error:
        if error.kind != "unknown_id":
            raise
        raise QuarryError("unknown_id", f"{id!r} matches no prefix a record-holding resource owns; a term id is "
                                        f"never read from a bare word: {terms_mod.term_hint(env.catalog, terms_mod.TERM_ROLES)}") from None
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
