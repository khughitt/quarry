"""Ontology and vocabulary commands: `quarry term show`, `ancestors`, and `search`."""
from __future__ import annotations

import os
from pathlib import Path

import click

from quarry import context
from quarry import ids as idmod
from quarry import terms as terms_mod
from quarry.adapters import base as adapter_base
from quarry.catalog import Resource
from quarry.commands._state import reports_errors, state
from quarry.errors import QuarryError
from quarry.lens import resolve_domain
from quarry.output import CONTEXT, emit, output_options

__all__ = ["term_group"]


@click.group("term", context_settings=CONTEXT)
@output_options
def term_group() -> None:
    """Terms in vocabularies and ontologies."""


def _term_adapter(resource: Resource, roles: frozenset[str]) -> terms_mod.TermAdapter:
    if not set(resource.roles) & roles:
        raise QuarryError("unsupported_role", f"{resource.id} is not a {' or '.join(sorted(roles))} resource")
    adapter = adapter_base.make(resource)
    if not isinstance(adapter, terms_mod.TermAdapter):
        raise QuarryError("unsupported_role", f"{resource.id} has no term adapter")
    return adapter


def _term_owner(env: context.Env, text: str, roles: frozenset[str]) -> tuple[idmod.Curie, terms_mod.TermAdapter]:
    """The CURIE and the term adapter of the resource that owns its prefix."""
    try:
        parsed = idmod.parse(text, env.registry)
    except QuarryError as error:
        if error.kind not in ("ambiguous_id", "not_a_curie"):
            raise
        # ids.parse points at `id normalize`, which reads no term ids
        raise QuarryError(error.kind, f"{text!r} needs a prefix; {terms_mod.term_hint(env.catalog, roles)}") from None
    owner = env.catalog.owner(parsed.prefix)
    if owner is None:
        raise QuarryError("no_owner", f"no catalog resource owns {parsed.prefix}: ids under it are cross-references only")
    return parsed, _term_adapter(owner, roles)


def _version(release: terms_mod.Release) -> str:
    return f"{release.resource} {release.version or '?'}"


def _warning_lines(warnings: list[str]) -> list[str]:
    return [f"warning: {w}" for w in warnings]


def _pretty_term(term: terms_mod.Term, release: terms_mod.Release, warnings: list[str]) -> str:
    lines = [f"{term.id}\t{term.label}"]
    if term.definition is not None:
        lines.append(f"definition: {term.definition}")
    if term.synonyms:
        lines.append(f"synonyms: {'; '.join(s.text for s in term.synonyms)}")
    if term.obsolete:
        lines.append("obsolete")
    if term.replaced_by is not None:
        lines.append(f"replaced by: {term.replaced_by}")
    if term.consider:
        lines.append(f"consider: {'; '.join(term.consider)}")
    lines.append(f"release: {_version(release)}")
    return "\n".join(lines + _warning_lines(warnings))


@term_group.command("show", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.pass_context
@reports_errors
def term_show(ctx: click.Context, curie: str) -> None:
    """Show one term."""
    env = context.open_env()
    parsed, adapter = _term_owner(env, curie, terms_mod.TERM_ROLES)
    with context.make_client(state(ctx).trace) as client:
        term, release, warnings = adapter.show(client, env.registry, parsed)
    emit(ctx, {"term": term.as_dict(), "release": release.as_dict(), "warnings": warnings},
         _pretty_term(term, release, warnings))


@term_group.command("ancestors", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.pass_context
@reports_errors
def term_ancestors(ctx: click.Context, curie: str) -> None:
    """List a term's is_a ancestors."""
    env = context.open_env()
    parsed, adapter = _term_owner(env, curie, frozenset({"ontology"}))
    with context.make_client(state(ctx).trace) as client:
        term, found, release, warnings = adapter.ancestors(client, env.registry, parsed)
    listed = [{"id": t.id, "label": t.label} for t in found]
    report = {"term": {"id": term.id, "label": term.label}, "relation": "is_a", "ancestors": listed,
              "release": release.as_dict(), "warnings": warnings}
    lines = [f"{a['id']}\t{a['label']}" for a in listed]
    lines.append(f"# {len(listed)} is_a ancestors ({_version(release)})")
    emit(ctx, report, "\n".join(lines + _warning_lines(warnings)))


@term_group.command("search", context_settings=CONTEXT)
@output_options
@click.argument("words", nargs=-1, required=True)
@click.option("--in", "in_", multiple=True, help="Search this resource; repeatable. Replaces the domain's ontologies.")
@click.option("--domain", help="Search this domain's ontologies and vocabularies (also QUARRY_DOMAIN).")
@click.option("--exact", is_flag=True, help="Match the words as a whole label or synonym.")
@click.option("-n", "--limit", type=click.IntRange(min=1), default=10, show_default=True,
              help="At most this many terms from each resource.")
@click.pass_context
@reports_errors
def term_search(ctx: click.Context, words: tuple[str, ...], in_: tuple[str, ...], domain: str | None,
                exact: bool, limit: int) -> None:
    """Search terms by label and synonym."""
    env = context.open_env()
    chosen = resolve_domain(domain, os.environ, state(ctx).config, Path.cwd(), env.catalog)
    if in_:
        resource_ids = list(dict.fromkeys(in_))
        for rid in resource_ids:
            if rid not in env.catalog.resources:
                raise QuarryError("unknown_resource", f"no resource {rid!r}")
    else:
        resource_ids = [rid for rid in env.catalog.effective_resources(chosen)
                        if set(env.catalog.resources[rid].roles) & terms_mod.TERM_ROLES]
        if not resource_ids:
            raise QuarryError("nothing_to_search", f"the {chosen} domain has no ontology or vocabulary resources; "
                                                   f"name one with --in or pick a domain from `quarry domains list`")
    adapters = [(rid, _term_adapter(env.catalog.resources[rid], terms_mod.TERM_ROLES)) for rid in resource_ids]
    text = " ".join(words)
    coverage, found, warnings = [], [], []
    with context.make_client(state(ctx).trace) as client:
        for rid, adapter in adapters:
            cov, rows, release, notes = adapter.search(client, env.registry, text, exact=exact, limit=limit)
            coverage.append({**cov.as_dict(), "release": release.as_dict()})
            found += rows
            warnings += notes
    report = {"domain": chosen, "coverage": coverage, "terms": [t.as_dict() for t in found], "warnings": warnings}
    lines = [f"{t.id}\t{t.label}" for t in found]
    lines += [f"# {c['resource']} {c['release']['version'] or '?'}: {c['returned']} of {c['total']} ({c['complete']})"
              for c in coverage]
    emit(ctx, report, "\n".join(lines + _warning_lines(warnings)))
