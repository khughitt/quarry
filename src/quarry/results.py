"""What a search returns: rows with the fields every domain shares, one coverage entry per
resource saying what was sent and how complete the answer is, and the completeness checks."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from quarry.errors import QuarryError

__all__ = ["Coverage", "Result", "completeness", "distinct", "trimmed"]


@dataclass(frozen=True)
class Result:
    id: str
    resource: str
    kind: str                           # dataset | record | publication
    title: str
    date: str | None = None
    summary: str | None = None
    xrefs: tuple[str, ...] = ()
    publication: str | None = None
    domain_fields: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {"id": self.id, "resource": self.resource, "kind": self.kind, "title": self.title}
        for key in ("date", "summary", "publication"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        out["xrefs"] = list(self.xrefs)
        out["domain_fields"] = {k: dict(v) for k, v in self.domain_fields.items()}
        return out


@dataclass(frozen=True)
class Coverage:
    resource: str
    native: str | None
    match: str                          # exact | expanded | native (sent as written, not compiled) | none (no phrases)
    total: int | None
    returned: int
    complete: str                       # total | short-page | limited | failed | skipped
    error: Mapping[str, str] | None = None
    warnings: tuple[str, ...] = ()      # what the adapter wants the report to say; never in as_dict
    reason: str | None = None           # why a skipped resource was not searched
    time: str | None = None             # overlap | start | nearest: how records met --during or --at; never on skipped rows

    @classmethod
    def skipped(cls, resource: str, match: str, reason: str) -> Coverage:
        return cls(resource, None, match, None, 0, "skipped", reason=reason)

    def as_dict(self) -> dict:
        if self.complete == "skipped":
            return {"resource": self.resource, "match": self.match, "complete": "skipped", "reason": self.reason}
        out = {"resource": self.resource, "native": self.native, "match": self.match}
        if self.time is not None:
            out["time"] = self.time
        out |= {"total": self.total, "returned": self.returned, "complete": self.complete}
        if self.error is not None:
            out["error"] = dict(self.error)
        return out


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
    @property
    def id(self) -> str: ...


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


def trimmed(*, kept: int, fetched: int, native_total: int | None, short_page: bool,
            limit: int | None) -> tuple[int | None, str]:
    """The (total, complete) of a listing an adapter trimmed by record time. Completeness is
    established on the native listing: the rows fetched against the provider's total, or a short
    page. The kept rows are the total only when that listing was fetched to its end; a listing
    stopped by -n reports no total, since trimming could discard the rows left unfetched."""
    if limit is not None and kept > limit:
        raise ValueError("cut the kept rows to -n before reporting them")
    if native_total is not None and fetched > native_total:
        raise QuarryError("incomplete_listing", f"the resource returned {fetched} rows of a total of {native_total}")
    if (fetched == native_total) if native_total is not None else short_page:
        return kept, "total"
    if limit is not None and kept == limit:
        return None, "limited"
    of = f" of {native_total}" if native_total is not None else ""
    raise QuarryError("incomplete_listing", f"paging stopped after {fetched}{of} rows, before the listing ended "
                                            f"or -n was reached")
