"""Running one search across resources (spec §8.2, §8.3). Every refusal that needs no network
happens before the first request."""
from __future__ import annotations

from typing import Protocol

from quarry.adapters import base
from quarry.catalog import DATA_ROLES, Catalog
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.query import Query
from quarry.results import Coverage, Result


def completeness(*, total: int | None, returned: int, limit: int | None, short_page: bool) -> str:
    if total is not None:
        if returned == total:
            return "total"
        if limit is not None and returned == limit and total > limit:
            return "limited"
        raise QuarryError("incomplete_listing", f"the resource returned {returned} of {total} records")
    if limit is not None and returned == limit:
        return "limited"
    if short_page:
        return "short-page"
    raise QuarryError("incomplete_listing", f"the resource reports no total and paging ended without a short page "
                                            f"after {returned} records")


class _HasId(Protocol):
    id: str


def distinct[Row: _HasId](rows: list[Row]) -> list[Row]:
    """The rows, refused when an id repeats: offset paging over a moving or tied sort that repeats
    one record can skip another while the count still matches the total."""
    seen = set()
    for row in rows:
        if row.id in seen:
            raise QuarryError("incomplete_listing", f"the resource returned {row.id} twice while paging; "
                                                    f"a record may have been skipped")
        seen.add(row.id)
    return rows


def _unexpressed(adapter, query: Query) -> str | None:
    """Why the adapter cannot carry the query's filters or matching mode, or None when it can."""
    rid = adapter.resource.id
    missing = set(query.filters) - adapter.filters
    if missing:
        return f"{rid} cannot filter by {', '.join(sorted(missing))}"
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
        reason = _unexpressed(adapter, query)
        if reason is not None and not from_defaults:
            raise QuarryError("unsupported_filter", f"{reason}; leave it out of --in")
        planned.append(adapter if reason is None else Coverage.skipped(rid, adapter.match, reason))
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
            coverage.append(Coverage(adapter.resource.id, prepared.native,
                                     "native" if query.native is not None else adapter.match, None, 0, "failed",
                                     {"kind": error.kind, "detail": error.detail}))
            warnings.append(f"{adapter.resource.id} failed: {error.detail}")
            continue
        coverage.append(cov)
        warnings += list(cov.warnings)
        results += rows
    return {"domain": domain, "coverage": [c.as_dict() for c in coverage],
            "results": [r.as_dict() for r in results], "warnings": warnings}
