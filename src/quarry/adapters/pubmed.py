"""PubMed through E-utilities db=pubmed (spec §13): a literature index searched live per record;
lit owns the snapshot. Phrases are quoted [All Fields] clauses, which PubMed still expands."""
from __future__ import annotations

from collections.abc import Callable

from quarry.adapters import eutils
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

STRINGS = ("title", "sortpubdate", "fulljournalname")


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    clauses = [eutils.phrases(query)]
    when = eutils.dates(query)
    if when:
        clauses.append(when)
    return " AND ".join(clauses)


def _valid(d: dict) -> dict:
    if type(d.get("uid")) is not str or not d["uid"].isdigit():
        raise UpstreamError("pubmed esummary: uid is not a number")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"pubmed esummary: {name} is not text")
    ids = d.get("articleids")
    if ids is not None and (type(ids) is not list or any(type(a) is not dict for a in ids)):
        raise UpstreamError("pubmed esummary: articleids is not a list")
    return d


def _row(d: dict) -> Result:
    d = _valid(d)
    ids = {a.get("idtype"): a["value"] for a in d.get("articleids") or [] if type(a.get("value")) is str}
    xrefs = []
    if ids.get("doi"):
        xrefs.append(f"doi:{ids['doi']}")
    if ids.get("pmc"):
        xrefs.append(f"pmc:{ids['pmc']}")
    return Result(id=f"pubmed:{d['uid']}", resource="pubmed", kind="publication", title=d.get("title") or "",
                  date=eutils.iso_date(d.get("sortpubdate")), summary=d.get("fulljournalname") or None, xrefs=tuple(xrefs))


class Pubmed:
    match = "expanded"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            count, ids, notices = eutils.esearch(client, self.route, "pubmed", q, query.limit)
            rows = [_row(d) for d in eutils.esummary(client, self.route, "pubmed", ids)]
            complete = completeness(total=count, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, count, len(rows), complete, warnings=notices), rows

        return execute

    def show(self, client: Client, curie: Curie) -> Result:
        record = eutils.esummary_one(client, self.route, "pubmed", curie.local)
        if record is None:
            raise QuarryError("not_found", f"{curie} is not in PubMed")
        return _row(record)

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        raise QuarryError("unsupported_role", f"{curie}: pubmed holds publications, which carry no files")


def make(resource: Resource) -> Pubmed:
    return Pubmed(resource)
