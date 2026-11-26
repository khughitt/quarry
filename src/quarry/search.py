"""Running one search across resources. Every refusal that needs no network
happens before the first request."""
from __future__ import annotations

from dataclasses import replace

from quarry.adapters import base
from quarry.catalog import DATA_ROLES, Catalog
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.query import Query
from quarry.results import Coverage

__all__ = ["PHRASES", "TIME_RELATIONS", "run"]

PHRASES = ("required", "optional", "none")
TIME_RELATIONS = {"during": ("overlap", "start"), "at": ("nearest",)}


def _match(adapter, query: Query) -> str:
    if query.native is not None:
        return "native"
    return adapter.match if query.phrases else "none"


def _time_form(query: Query) -> str | None:
    return "during" if query.during is not None else "at" if query.at is not None else None


def _stamped(row: Coverage, adapter, query: Query) -> Coverage:
    """The row with the match and time relation the search applied, decided here for every row."""
    form = _time_form(query)
    time = None if form is None or row.complete == "skipped" else adapter.time_relations[form]
    return replace(row, match=_match(adapter, query), time=time)


def _check_declarations(adapter) -> None:
    """Refuse an adapter whose own declarations are malformed, before any request."""
    rid = adapter.resource.id
    if adapter.phrases not in PHRASES:
        raise ValueError(f"{rid} declares phrases = {adapter.phrases!r}; expected one of {', '.join(PHRASES)}")
    doubled = adapter.filters & set(TIME_RELATIONS)
    if doubled:
        raise ValueError(f"{rid} declares {', '.join(sorted(doubled))} in filters; time filters belong in time_relations")
    for form, relation in adapter.time_relations.items():
        if relation not in TIME_RELATIONS.get(form, ()):
            raise ValueError(f"{rid} declares {form} = {relation!r}; expected "
                             + "; ".join(f"{f}: {' or '.join(r)}" for f, r in TIME_RELATIONS.items()))


def _unexpressed(adapter, query: Query) -> str | None:
    """Why the adapter cannot carry the query's filters, phrases or matching mode, or None when it can."""
    rid = adapter.resource.id
    missing = query.filter_names() - adapter.filters - set(adapter.time_relations)
    if missing:
        return f"{rid} cannot filter by {', '.join(sorted(missing))}"
    if query.native is None:
        if not query.phrases and adapter.phrases == "required":
            return f"{rid} needs at least one phrase"
        if query.phrases and adapter.phrases == "none":
            return f"{rid} has no text search"
    reason = adapter.unexpressed(query)
    if reason is not None:
        return reason
    if query.exact and adapter.match != "exact":
        return f"{rid} expands phrases and cannot match exactly"
    return None


def run(*, catalog: Catalog, client: Client, query: Query, resource_ids: list[str], domain: str,
        allow_partial: bool, from_defaults: bool, make=base.make) -> dict:
    """Search each resource in order. When the set came from a domain's search_default, a resource
    that cannot express the query is skipped with a coverage row; one named with --in is refused."""
    planned: list[Coverage | base.Adapter] = []          # in search-set order: a skip row or an adapter to run
    for rid in resource_ids:
        resource = catalog.resources.get(rid)
        if resource is None:
            raise QuarryError("unknown_resource", f"no resource {rid!r}")
        if not set(resource.roles) & DATA_ROLES:
            raise QuarryError("unsupported_role", f"{rid} holds no searchable records ({', '.join(resource.roles)})")
        adapter = make(resource)
        _check_declarations(adapter)
        reason = _unexpressed(adapter, query)
        if reason is not None and not from_defaults:
            raise QuarryError("unsupported_filter", f"{reason}; leave it out of --in")
        planned.append(adapter if reason is None else _stamped(Coverage.skipped(rid, adapter.match, reason), adapter, query))
    if all(isinstance(p, Coverage) for p in planned):
        raise QuarryError("unsupported_filter", "no resource can carry this query: "
                                                + "; ".join(p.reason for p in planned))
    steps = [p if isinstance(p, Coverage) else (p, p.prepare(query)) for p in planned]
    coverage, results, warnings = [], [], []
    for step in steps:
        if isinstance(step, Coverage):
            coverage.append(step)
            warnings.append(f"{step.resource} skipped: {step.reason}")
            continue
        adapter, prepared = step
        try:
            cov, rows = prepared.execute(client)
        except QuarryError as error:
            if not allow_partial:
                raise
            coverage.append(_stamped(Coverage(adapter.resource.id, prepared.native, adapter.match, None, 0, "failed",
                                              {"kind": error.kind, "detail": error.detail}), adapter, query))
            warnings.append(f"{adapter.resource.id} failed: {error.detail}")
            continue
        cov = _stamped(cov, adapter, query)
        coverage.append(cov)
        if cov.time == "start":
            warnings.append(f"{cov.resource} matched --during by start time: records that began before START "
                            f"are not returned")
        warnings += list(cov.warnings)
        results += rows
    return {"domain": domain, "coverage": [c.as_dict() for c in coverage],
            "results": [r.as_dict() for r in results], "warnings": warnings}
