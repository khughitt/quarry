"""NCBI E-utilities, shared by GEO (db=gds) and PubMed (db=pubmed): quoted [All Fields] phrases,
[PDAT] bounds, esearch paged by retstart inside the 10,000-id window, esummary in POSTed
batches. Both resources' routes name service ncbi-eutils, so they share one throttle and key."""
from __future__ import annotations

import json
import re
from datetime import date

from quarry.catalog import Route
from quarry.errors import QuarryError
from quarry.http import Client, UpstreamError
from quarry.query import Query

WINDOW = 10_000            # retstart + retmax never exceeds this without the history server
BATCH = 500                # esummary ids per POST
UID = re.compile(r"^[0-9]+$")
SLASH_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})")


def phrase(text: str) -> str:
    """A literal phrase: Entrez reads a double quote as syntax and a star as a wildcard, and documents no escape."""
    return f'"{literal(text, "an E-utilities phrase")}"[All Fields]'


def literal(text: str, what: str) -> str:
    """`text` when Entrez would read every character of it literally inside double quotes."""
    for char, name in (('"', "a double quote"), ("*", "a wildcard star")):
        if char in text:
            raise QuarryError("unsupported_filter", f"{what} cannot contain {name}: {text!r}; use --native")
    return text


def phrases(query: Query) -> str:
    return "(" + " OR ".join(phrase(p) for p in query.phrases) + ")"


def _pdat(when: date | None, open_end: str) -> str:
    return f'"{when.strftime("%Y/%m/%d")}"[PDAT]' if when else f'"{open_end}"[PDAT]'


def dates(query: Query) -> str | None:
    if query.since is None and query.until is None:
        return None
    return f"({_pdat(query.since, '1800/01/01')} : {_pdat(query.until, '3000/12/31')})"


def iso_date(text: object) -> str | None:
    """'2010/09/11' or '2024/02/01 00:00' as ISO; None when absent; an upstream fault otherwise."""
    if text is None or text == "":
        return None
    match = SLASH_DATE.match(text) if type(text) is str else None
    if match is None:
        raise UpstreamError("eutils: a date is not YYYY/MM/DD")
    return "-".join(match.groups())


def _object(text: str, key: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"eutils {key}: not JSON") from None
    if type(data) is not dict or "error" in data or type(data.get(key)) is not dict:
        raise UpstreamError(f"eutils {key}: expected a JSON object holding {key!r}")
    return data[key]


# (container, list, sentence): what Entrez names when it drops part of a query. It never widens
# one, so a dropped term narrows the answer silently unless the report says so.
NOTICES = (("errorlist", "phrasesnotfound", "Entrez has no records for"),
           ("errorlist", "fieldsnotfound", "Entrez ignored unknown fields"),
           ("warninglist", "quotedphrasesnotfound", "Entrez has no records for"),
           ("warninglist", "phrasesignored", "Entrez ignored phrases"))


def _notices(result: dict) -> list[str]:
    """One sentence per non-empty notice list, in NCBI's own spellings of the terms."""
    if "ERROR" in result:
        raise UpstreamError(f"eutils esearch: {result['ERROR']}")
    sentences = []
    for container, key, sentence in NOTICES:
        lists = result.get(container)
        if lists is None:
            continue
        if type(lists) is not dict:
            raise UpstreamError(f"eutils esearch: {container} is not an object")
        terms = lists.get(key)
        if terms is None:
            continue
        if type(terms) is not list or any(type(t) is not str for t in terms):
            raise UpstreamError(f"eutils esearch: {container}.{key} is not a list of text")
        if terms:
            sentences.append(f"{sentence}: {', '.join(terms)}")
    return sentences


def esearch(client: Client, route: Route, db: str, term: str,
            limit: int | None) -> tuple[int, list[str], tuple[str, ...]]:
    """(count, ids, warnings): every matching id, or the first `limit`, paged by retstart, and a
    sentence for each term Entrez found nothing for or ignored. A count the window cannot hold is
    refused before anything else is asked (spec §8.3: no silent truncation)."""
    ids: list[str] = []
    warnings: list[str] = []
    wanted = WINDOW if limit is None else min(limit, WINDOW)
    while True:
        result = _object(client.request(route, "GET", "esearch.fcgi", params={
            "db": db, "term": term, "retmode": "json", "retstart": len(ids), "retmax": wanted - len(ids)}).text,
            "esearchresult")
        warnings += _notices(result)
        page = result.get("idlist")
        if (type(page) is not list or any(type(i) is not str or not UID.match(i) for i in page)
                or type(result.get("count")) is not str or not UID.match(result["count"])):
            raise UpstreamError("eutils esearch: malformed count or idlist")
        count = int(result["count"])
        if count > WINDOW and (limit is None or limit > WINDOW):
            raise QuarryError("too_many_results", f"{count} records match, more than the {WINDOW} E-utilities serves "
                                                  f"per query; narrow the query or pass -n")
        ids += page
        if not page or len(ids) >= min(count, wanted):
            return count, ids, tuple(dict.fromkeys(warnings))


def esummary(client: Client, route: Route, db: str, ids: list[str]) -> list[dict]:
    """One summary per id, in the order given, POSTed in batches; an id esearch just listed must answer."""
    records = []
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        result = _object(client.request(route, "POST", "esummary.fcgi",
                                        data={"db": db, "retmode": "json", "id": ",".join(batch)}).text, "result")
        for uid in batch:
            record = result.get(uid)
            if type(record) is not dict or "error" in record:
                raise UpstreamError("eutils esummary: no summary for an id esearch listed")
            records.append(record)
    return records


def esummary_one(client: Client, route: Route, db: str, uid: str) -> dict | None:
    """The summary for one known uid, or None when E-utilities answers its error object for it."""
    result = _object(client.request(route, "GET", "esummary.fcgi", params={"db": db, "retmode": "json", "id": uid}).text,
                     "result")
    record = result.get(uid)
    if type(record) is not dict:
        raise UpstreamError("eutils esummary: malformed result")
    return None if "error" in record else record
