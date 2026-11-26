"""Catalog inspection and validation commands: `domains`, `resources`, and `check`."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import click

from quarry import catalog as catalog_mod
from quarry import context
from quarry.catalog import ROLES, ROUTE_KINDS, CatalogError, Finding
from quarry.commands._state import emit_findings, reports_errors, state
from quarry.errors import QuarryError
from quarry.lens import resolve_domain
from quarry.output import CONTEXT, emit, output_options

__all__ = ["check_command", "domains", "resources"]


@click.group("domains", context_settings=CONTEXT)
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


@click.group("resources", context_settings=CONTEXT)
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


@click.command("check", context_settings=CONTEXT)
@output_options
@click.pass_context
@reports_errors
def check_command(ctx: click.Context) -> None:
    """Validate the catalog."""
    try:
        env = context.load_unchecked()
    except CatalogError as error:
        emit_findings(ctx, [Finding("catalog_invalid", "catalog", error.detail)])
        return
    emit_findings(ctx, catalog_mod.check(env.catalog, env.registry, collisions=env.collisions))
