"""Pinned technical controls; metadata only, with access-class checks on every capture."""
from datetime import date

from quarry.adapters import europepmc
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

OA = Curie("europepmc", "MED/22253597")
# Technical software-foundation article and scientific-software CI preprint, selected by metadata.
NON_OA = Curie("europepmc", "MED/41805121")
PREPRINT = Curie("ppr", "PPR1338310")
MISSING = Curie("europepmc", "MED/999999999")
TECHNICAL = Query(phrases=("bioinformatics software",), since=date(2024, 1, 1), limit=3)
PINNED = Query(native='(EXT_ID:22253597 AND SRC:MED) OR (EXT_ID:41805121 AND SRC:MED) OR '
                      '(EXT_ID:PPR1338310 AND SRC:PPR)')


def search_technical(adapter, client):
    cov, rows = adapter.search(client, TECHNICAL)
    assert rows and cov.returned == len(rows) <= 3
    assert all(row.date and row.date >= "2024-01-01" for row in rows)
    return cov, rows


def search_pages(adapter, client):
    old_size = europepmc.PAGE_SIZE
    try:
        europepmc.PAGE_SIZE = 2
        cov, rows = adapter.search(client, PINNED)
    finally:
        europepmc.PAGE_SIZE = old_size
    assert cov.total == cov.returned == 3 and cov.complete == "total"
    assert {row.id for row in rows} == {str(OA), str(NON_OA), "europepmc:PPR/PPR1338310"}
    return cov, rows


def show_oa(adapter, client):
    row = adapter.show(client, OA)
    assert row.id == str(OA) and "pmc:PMC3257301" in row.xrefs
    assert row.domain_fields["core"]["open_access"] == row.domain_fields["core"]["in_epmc"] == "Y"
    return row


def show_non_oa(adapter, client):
    row = adapter.show(client, NON_OA)
    assert row.id == str(NON_OA) and any(x.startswith("pmc:") for x in row.xrefs)
    assert row.domain_fields["core"]["open_access"] == "N" and row.domain_fields["core"]["in_epmc"] == "Y"
    return row


def show_preprint(adapter, client):
    row = adapter.show(client, PREPRINT)
    assert row.id == "europepmc:PPR/PPR1338310" and "ppr:PPR1338310" in row.xrefs
    assert row.domain_fields["core"] == {"open_access": "N", "in_epmc": "N", "in_pmc": "N"}
    return row


def show_missing(adapter, client):
    try:
        adapter.show(client, MISSING)
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError("Europe PMC missing control now exists; select a new unused id")


def files_oa(adapter, client):
    row, files = adapter.files(client, OA)
    assert row.id == str(OA) and len(files) == 1 and files[0].name == "PMC3257301.xml"
    return row, files


def files_non_oa(adapter, client):
    row, files = adapter.files(client, NON_OA)
    assert row.id == str(NON_OA) and row.domain_fields["core"]["open_access"] == "N" and files == []
    return row, files


SCENARIOS = {"search-technical": search_technical, "search-pages": search_pages, "show-oa": show_oa,
             "show-non-oa": show_non_oa, "show-preprint": show_preprint, "show-missing": show_missing,
             "files-oa": files_oa, "files-non-oa": files_non_oa}
