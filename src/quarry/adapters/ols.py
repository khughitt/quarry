"""The EBI Ontology Lookup Service (OLS4) as a term adapter: release, show, is_a ancestors and
search for one ontology. Every URL is built from the route's base; a link OLS returns is never
followed. Every response shape read is validated and a violation is `upstream_failed`."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from refs import Registry

from quarry.catalog import Resource, Route
from quarry.errors import QuarryError
from quarry.http import Client, UpstreamError
from quarry.ids import Curie, parse
from quarry.search import completeness, distinct
from quarry.terms import Release, Synonym, Term, TermCoverage, obo_text

ROUTE_PATH = re.compile(r"(.+)/ontologies/([^/]+)")
RELEASE_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SCOPES = {"hasExactSynonym": "exact", "hasRelatedSynonym": "related",
          "hasNarrowSynonym": "narrow", "hasBroadSynonym": "broad"}
DOC_SYNONYMS = {"exact_synonyms": "exact", "related_synonyms": "related",
                "narrow_synonyms": "narrow", "broad_synonyms": "broad"}
SOLR_CHARACTERS = frozenset('*?~"()[]{}^\\!/&|')
SOLR_WORDS = frozenset({"AND", "OR", "NOT"})


def _object(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"ols {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"ols {what}: expected a JSON object")
    return data


def _text(data: dict, key: str, what: str, *, optional: bool = False) -> str | None:
    value = data.get(key)
    if value is None and optional:
        return None
    if type(value) is not str:
        raise UpstreamError(f"ols {what}: {key} is not text")
    return value


def _list(data: dict, key: str, what: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if type(value) is not list:
        raise UpstreamError(f"ols {what}: {key} is not a list")
    return value


def _primary(value: object, registry: Registry, what: str) -> str:
    """The canonical id of a term the listing itself is made of; one that cannot be read fails."""
    text = obo_text(value)
    if text is None:
        raise UpstreamError(f"ols {what}: term id {value!r} is not an OBO id")
    try:
        return str(parse(text, registry))
    except QuarryError:
        raise UpstreamError(f"ols {what}: term id {value!r} is not an id refs knows") from None


def _optional(value: object, registry: Registry, left_out: list[str], *, as_curie: bool = False) -> str | None:
    """The canonical id of a cross-reference, or None with the raw id noted in `left_out`.
    `as_curie` says `value` is already `<database>:<id>` text; otherwise it is an OBO id, short
    form or PURL and goes through `obo_text` first."""
    text = value if as_curie else obo_text(value)
    if type(text) is str:
        try:
            return str(parse(text, registry))
        except QuarryError:
            pass
    left_out.append(str(value))
    return None


def _check_text(text: str, registry: Registry) -> None:
    """Refuse what Solr would read as syntax rather than words (OLS `q` is a Solr query)."""
    words = text.split()
    if not words:
        raise QuarryError("unsupported_filter", "a term search needs at least one word")
    for word in words:
        bad = sorted(SOLR_CHARACTERS & set(word))
        if bad:
            raise QuarryError("unsupported_filter", f"{word!r} has {' '.join(bad)}, which OLS reads as query "
                                                    f"syntax; search plain words")
        if word[0] in "+-":
            raise QuarryError("unsupported_filter", f"{word!r} starts with {word[0]}, which OLS reads as query syntax")
        if word in SOLR_WORDS:
            raise QuarryError("unsupported_filter", f"{word!r} is a query operator in OLS; search plain words")
        if ":" in word:
            try:
                parse(word, registry)
            except QuarryError:
                raise QuarryError("unsupported_filter", f"{word!r} has a colon that is not a CURIE; OLS would "
                                                        f"read it as a field query") from None


class Ols:
    def __init__(self, resource: Resource, base: str, ontology: str):
        self.resource = resource
        self.route: Route = resource.routes[0]
        self.base = base
        self.ontology = ontology

    def _get(self, client: Client, url: str, params: dict, *, accept: frozenset[int] = frozenset()):
        return client.request(self.route, "GET", url, params=params, accept=accept)

    def _ontology(self, client: Client) -> tuple[Release, str, list[str]]:
        what = f"{self.ontology} ontology"
        data = _object(self._get(client, self.route.url, {}).text, what)
        if data.get("ontologyId") not in (None, self.ontology) or data.get("status") != "LOADED":
            raise UpstreamError(f"ols {what}: expected the loaded ontology {self.ontology}")
        config = data.get("config")
        if type(config) is not dict:
            raise UpstreamError(f"ols {what}: config is not an object")
        prefix = _text(config, "preferredPrefix", what)
        if not prefix:
            raise UpstreamError(f"ols {what}: preferredPrefix is empty")
        loaded = _text(data, "loaded", what, optional=True)
        version = _text(config, "version", what, optional=True)
        iri = _text(config, "versionIri", what, optional=True)
        warnings = []
        if not version:
            found = RELEASE_DATE.search(iri or "")
            version = found[0] if found else None
        if version is None:
            warnings.append(f"{self.resource.id}: OLS gives no release version")
        return Release(self.resource.id, version, loaded), prefix, warnings

    def release(self, client: Client) -> tuple[Release, list[str]]:
        release, _, warnings = self._ontology(client)
        return release, warnings

    # -- terms --------------------------------------------------------------------------------

    def _term(self, doc: dict, registry: Registry, left_out: list[str], what: str, *, full: bool) -> Term:
        label = _text(doc, "label", what)
        identity = _primary(doc.get("obo_id"), registry, what)
        if not full:
            return Term(identity, label, self.resource.id)
        definition = None
        for citation in _list(doc, "obo_definition_citation", what):
            if type(citation) is not dict:
                raise UpstreamError(f"ols {what}: a definition citation is not an object")
            text = _text(citation, "definition", what, optional=True)
            if text:
                definition = text
                break
        synonyms = []
        for item in _list(doc, "obo_synonym", what):
            if type(item) is not dict:
                raise UpstreamError(f"ols {what}: a synonym is not an object")
            scope = SCOPES.get(item.get("scope"))
            if scope is None:
                raise UpstreamError(f"ols {what}: unknown synonym scope {item.get('scope')!r}")
            synonyms.append(Synonym(_text(item, "name", what), scope))
        xrefs = []
        for item in _list(doc, "obo_xref", what):
            if type(item) is not dict:
                raise UpstreamError(f"ols {what}: a cross-reference is not an object")
            database, local = item.get("database"), _text(item, "id", what)
            if database is not None and type(database) is not str:
                raise UpstreamError(f"ols {what}: a cross-reference database is not text")
            xref = (_optional(local, registry, left_out) if database is None
                    else _optional(f"{database}:{local}", registry, left_out, as_curie=True))
            if xref is not None:
                xrefs.append(xref)
        obsolete = doc.get("is_obsolete")
        if obsolete is None:
            obsolete = False
        if type(obsolete) is not bool:
            raise UpstreamError(f"ols {what}: is_obsolete is not a boolean")
        replaced = _text(doc, "term_replaced_by", what, optional=True)
        replaced_by = _optional(replaced, registry, left_out) if replaced else None
        annotation = doc.get("annotation")
        if annotation is not None and type(annotation) is not dict:
            raise UpstreamError(f"ols {what}: annotation is not an object")
        consider = [_optional(v, registry, left_out) for v in _list(annotation or {}, "consider", what)]
        return Term(identity, label, self.resource.id, definition, tuple(synonyms),
                    tuple(dict.fromkeys(xrefs)), obsolete, replaced_by,
                    tuple(dict.fromkeys(c for c in consider if c is not None)))

    def _left_out(self, left_out: list[str]) -> list[str]:
        if not left_out:
            return []
        return [f"{self.resource.id}: left out ids refs cannot read: {', '.join(dict.fromkeys(left_out))}"]

    def _show(self, client: Client, registry: Registry, curie: Curie) -> tuple[dict, Term, Release, list[str]]:
        release, prefix, warnings = self._ontology(client)
        what = f"term {curie}"
        response = self._get(client, f"{self.route.url}/terms", {"obo_id": f"{prefix}:{curie.local}"},
                             accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in {self.resource.id}")
        data = _object(response.text, what)
        page = data.get("page")
        if type(page) is not dict or page.get("totalElements") != 1:
            raise UpstreamError(f"ols {what}: expected exactly one matching term")
        embedded = data.get("_embedded")
        docs = embedded.get("terms") if type(embedded) is dict else None
        if type(docs) is not list or len(docs) != 1 or type(docs[0]) is not dict:
            raise UpstreamError(f"ols {what}: _embedded.terms is not one object")
        left_out: list[str] = []
        term = self._term(docs[0], registry, left_out, what, full=True)
        if term.id != str(curie):
            raise UpstreamError(f"ols {what}: returned {term.id}")
        return docs[0], term, release, warnings + self._left_out(left_out)

    def show(self, client: Client, registry: Registry, curie: Curie) -> tuple[Term, Release, list[str]]:
        _, term, release, warnings = self._show(client, registry, curie)
        return term, release, warnings

    def ancestors(self, client: Client, registry: Registry,
                  curie: Curie) -> tuple[Term, list[Term], Release, list[str]]:
        doc, term, release, warnings = self._show(client, registry, curie)
        what = f"ancestors of {curie}"
        iri = _text(doc, "iri", what)
        url = f"{self.route.url}/terms/{quote(quote(iri, safe=''), safe='')}/ancestors"
        size, total, found, page_number = self.route.page_cap, None, [], 0
        while True:
            data = _object(self._get(client, url, {"size": size, "page": page_number}).text, what)
            page = data.get("page")
            count = page.get("totalElements") if type(page) is dict else None
            if type(count) is not int or count < 0:
                raise UpstreamError(f"ols {what}: page.totalElements is not a nonnegative integer")
            if total is None:
                total = count
            elif count != total:
                raise QuarryError("incomplete_listing", f"OLS totalElements changed while listing {what}")
            embedded = data.get("_embedded")
            docs = embedded.get("terms") if type(embedded) is dict else ([] if total == 0 else None)
            if type(docs) is not list or any(type(d) is not dict for d in docs):
                raise UpstreamError(f"ols {what}: _embedded.terms is not a list of objects")
            found += [self._term(d, registry, [], what, full=False) for d in docs]
            distinct(found)
            if len(found) > total:
                raise QuarryError("incomplete_listing", f"OLS listed more ancestors than totalElements for {curie}")
            if len(found) == total:
                return term, found, release, warnings
            if len(docs) < size:
                raise QuarryError("incomplete_listing", f"OLS listed {len(found)} of {total} ancestors of {curie}")
            page_number += 1

    # -- search -------------------------------------------------------------------------------

    def search(self, client: Client, registry: Registry, text: str, *, exact: bool,
               limit: int) -> tuple[TermCoverage, list[Term], Release, list[str]]:
        _check_text(text, registry)
        if limit < 1:
            raise ValueError("limit is at least 1")
        release, _, warnings = self._ontology(client)
        params = {"q": " ".join(text.split()), "ontology": self.ontology, "local": "true", "obsoletes": "false"}
        if exact:
            params |= {"exact": "true", "queryFields": "label,synonym"}
        native = "&".join(f"{k}={v}" for k, v in params.items())
        what = f"search {text!r}"
        rows: list[Term] = []
        total = wanted = None
        while True:
            ask = min(self.route.page_cap, limit - len(rows) if wanted is None else wanted - len(rows))
            data = _object(self._get(client, f"{self.base}/search", {**params, "rows": ask, "start": len(rows)}).text,
                           what)
            response = data.get("response")
            if type(response) is not dict:
                raise UpstreamError(f"ols {what}: response is not an object")
            found = response.get("numFound")
            if type(found) is not int or found < 0:
                raise UpstreamError(f"ols {what}: numFound is not a nonnegative integer")
            if total is None:
                total, wanted = found, min(found, limit)
            elif found != total:
                raise QuarryError("incomplete_listing", f"OLS numFound changed while paging {what}")
            docs = response.get("docs")
            if type(docs) is not list or any(type(d) is not dict for d in docs):
                raise UpstreamError(f"ols {what}: response.docs is not a list of objects")
            if len(docs) > ask:
                raise UpstreamError(f"ols {what}: returned more documents than asked for")
            rows += [self._hit(d, registry, what) for d in docs]
            distinct(rows)
            if len(rows) >= wanted:
                break
            if not docs:
                raise QuarryError("incomplete_listing", f"OLS returned {len(rows)} of {wanted} terms for {what}")
        complete = completeness(total=total, returned=len(rows), limit=limit, short_page=False)
        return TermCoverage(self.resource.id, native, exact, total, len(rows), complete), rows, release, warnings

    def _hit(self, doc: dict, registry: Registry, what: str) -> Term:
        term = self._term(doc, registry, [], what, full=False)
        synonyms = []
        for key, scope in DOC_SYNONYMS.items():
            for value in _list(doc, key, what):
                if type(value) is not str:
                    raise UpstreamError(f"ols {what}: a {key} entry is not text")
                synonyms.append(Synonym(value, scope))
        return Term(term.id, term.label, term.resource, synonyms=tuple(synonyms))


def make(resource: Resource) -> Ols:
    route = resource.routes[0]
    match = ROUTE_PATH.fullmatch(route.url.rstrip("/"))
    if match is None:
        raise QuarryError("catalog_invalid", f"{resource.id}: the route url must end in /ontologies/<id>")
    if not route.page_cap or route.page_cap < 1:
        raise QuarryError("catalog_invalid", f"{resource.id}: the OLS route needs a page_cap")
    return Ols(resource, match[1], match[2])
