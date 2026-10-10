"""ENA portal API, result=study (spec §13). Phrases compile to exact-equality clauses: a
wildcard cannot contain '-', '.' or a space and matches substrings. Every search asks the
count endpoint too, and a listing shorter than the count fails."""
from __future__ import annotations

import json
import re

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

FIELDS = ("study_accession,secondary_study_accession,study_alias,study_title,study_description,"
          "tax_id,scientific_name,first_public")
GSE = re.compile(r"^GSE\d+$")
TAXON = re.compile(r"^ncbitaxon:(\d+)$", re.IGNORECASE)
SHOW_FIELD = {"bioproject": "study_accession", "insdc.sra": "secondary_study_accession"}


def _quote(text: str) -> str:
    """Shared phrases are literal. ENA reads `*` as a wildcard and cannot carry a double quote,
    and documents no escape for either, so both are refused; --native sends ENA syntax as is."""
    for char, what in (('"', "a double quote"), ("*", "`*`, ENA's wildcard")):
        if char in text:
            raise QuarryError("unsupported_filter", f"an ENA phrase cannot contain {what}: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    terms = " OR ".join(f"study_title={q} OR study_description={q}" for q in map(_quote, query.phrases))
    clauses = [f"({terms})"]
    organism = query.filters.get("organism")
    if organism:
        taxon = TAXON.match(organism)
        clauses.append(f"tax_eq({taxon.group(1)})" if taxon else f"scientific_name={_quote(organism)}")
    if query.since:
        clauses.append(f"first_public>={query.since.isoformat()}")
    if query.until:
        clauses.append(f"first_public<={query.until.isoformat()}")
    return " AND ".join(clauses)


OPTIONAL_TEXT = ("secondary_study_accession", "study_alias", "study_title", "study_description",
                 "tax_id", "scientific_name", "first_public")


def _valid(d: dict) -> dict:
    """The fields _row reads, checked: a malformed value is the upstream's fault, never a crash."""
    if type(d.get("study_accession")) is not str or not d["study_accession"].startswith("PRJ"):
        raise UpstreamError(f"ena search: a study without a PRJ accession: {str(d)[:120]}")
    for name in OPTIONAL_TEXT:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"ena search: {name} of {d['study_accession']} is not text")
    if d.get("tax_id") and not re.fullmatch(r"[0-9]+", d["tax_id"]):
        raise UpstreamError(f"ena search: tax_id of {d['study_accession']} is not a number")
    return d


def _row(d: dict) -> Result:
    d = _valid(d)
    xrefs = [f"insdc.sra:{s}" for s in (d.get("secondary_study_accession") or "").split(";") if s]
    if GSE.match(d.get("study_alias") or ""):
        xrefs.append(f"geo:{d['study_alias']}")
    biology = {}
    if d.get("scientific_name"):
        biology["organism"] = d["scientific_name"]
    if d.get("tax_id"):
        biology["taxon"] = f"ncbitaxon:{d['tax_id']}"
    return Result(id=f"bioproject:{d['study_accession']}", resource="ena", kind="dataset",
                  title=d.get("study_title") or "", date=d.get("first_public") or None,
                  summary=d.get("study_description") or None, xrefs=tuple(xrefs),
                  domain_fields={"biology": biology} if biology else {})


def _count(text: str) -> int:
    """The count endpoint answers `count\\n<n>`."""
    words = text.split()
    if len(words) != 2 or words[0] != "count" or not re.fullmatch(r"[0-9]+", words[1]):
        raise UpstreamError(f"ena count: expected 'count <n>', got {text[:80]!r}")
    try:
        return int(words[1])
    except ValueError:
        raise UpstreamError(f"ena count: count is too large: {text[:80]!r}") from None


def _rows(text: str) -> list[dict]:
    if not text.strip():
        return []                                      # no matches: ENA answers an empty body
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"ena search: not JSON: {text[:80]!r}") from None
    if type(data) is not list or any(type(d) is not dict for d in data):
        raise UpstreamError("ena search: expected a list of studies")
    return data


class Ena:
    match = "exact"
    filters = frozenset({"organism"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        q = compile_query(query)
        total = _count(client.request(self.route, "GET", "count", params={"result": "study", "query": q}).text)
        listing = client.request(self.route, "GET", "search", params={
            "result": "study", "query": q, "fields": FIELDS, "format": "json", "limit": query.limit or 0}).text
        rows = [_row(d) for d in _rows(listing)]
        complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
        match = "native" if query.native is not None else self.match
        return Coverage(self.resource.id, q, match, total, len(rows), complete), rows

    def show(self, client: Client, curie: Curie) -> Result:
        field = SHOW_FIELD[curie.prefix]
        listing = client.request(self.route, "GET", "search", params={
            "result": "study", "query": f'{field}="{curie.local}"', "fields": FIELDS, "format": "json", "limit": 2}).text
        rows = _rows(listing)
        if not rows:
            raise QuarryError("not_found", f"{curie} is not in ENA")
        return _row(rows[0])


def make(resource: Resource) -> Ena:
    return Ena(resource)
