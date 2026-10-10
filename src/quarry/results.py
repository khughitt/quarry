"""What a search returns (spec §8.3): rows with the fields every domain shares, and one
coverage entry per resource saying what was sent and how complete the answer is."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


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
    match: str                          # exact | expanded | native (sent as written, not compiled)
    total: int | None
    returned: int
    complete: str                       # total | short-page | limited | failed
    error: Mapping[str, str] | None = None

    def as_dict(self) -> dict:
        out = {"resource": self.resource, "native": self.native, "match": self.match, "total": self.total,
               "returned": self.returned, "complete": self.complete}
        if self.error is not None:
            out["error"] = dict(self.error)
        return out
