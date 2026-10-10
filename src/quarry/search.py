"""Running one search across resources (spec §8.2, §8.3). Every refusal that needs no network
happens before the first request."""
from __future__ import annotations

from quarry.adapters import base
from quarry.catalog import DATA_ROLES, Catalog
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.query import Query
from quarry.results import Coverage


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


def run(*, catalog: Catalog, client: Client, query: Query, resource_ids: list[str], domain: str,
        allow_partial: bool, make=base.make) -> dict:
    adapters = []
    for rid in resource_ids:
        resource = catalog.resources.get(rid)
        if resource is None:
            raise QuarryError("unknown_resource", f"no resource {rid!r}")
        if not set(resource.roles) & DATA_ROLES:
            raise QuarryError("unsupported_role", f"{rid} holds no searchable records ({', '.join(resource.roles)})")
        adapter = make(resource)
        missing = set(query.filters) - adapter.filters
        if missing:
            raise QuarryError("unsupported_filter", f"{rid} cannot filter by {', '.join(sorted(missing))}")
        if query.exact and adapter.match != "exact":
            raise QuarryError("unsupported_filter", f"{rid} expands phrases and cannot match exactly; leave it out with --in")
        adapters.append((adapter, adapter.prepare(query)))
    coverage, results, warnings = [], [], []
    for adapter, execute in adapters:
        try:
            cov, rows = execute(client)
        except QuarryError as error:
            if not allow_partial:
                raise
            coverage.append(Coverage(adapter.resource.id, query.native,
                                     "native" if query.native is not None else adapter.match, None, 0, "failed",
                                     {"kind": error.kind, "detail": error.detail}))
            warnings.append(f"{adapter.resource.id} failed: {error.detail}")
            continue
        coverage.append(cov)
        results += rows
    return {"domain": domain, "coverage": [c.as_dict() for c in coverage],
            "results": [r.as_dict() for r in results], "warnings": warnings}
