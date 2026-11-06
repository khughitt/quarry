"""The PubMed exchanges tests/test_pubmed.py replays."""
from datetime import date

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

SMOLDERING = Query(phrases=("smoldering myeloma",), since=date(2024, 1, 1), until=date(2024, 3, 31))
MISSING = "999999999"          # far above any PubMed uid issued so far (about 41 million in 2026)
OA_PMID = Curie("pubmed", "29401521")               # pmc:PMC5798764 (OA CC BY in PMC)
NON_OA_PMID = Curie("pubmed", "20097884")           # pmc:PMC2830579 (non-OA publisher deposit in PMC)
MANUSCRIPT_NON_OA_PMID = Curie("pubmed", "41805121")  # pmc:PMC13274558 (non-OA NIH author manuscript in pmc-open)
NO_PMC_PMID = Curie("pubmed", "20079422")           # PMID-only, not in PMC


def show_first(adapter, client):
    _, rows = adapter.search(client, SMOLDERING)
    if not rows:
        raise ValueError("PubMed smoldering scenario returned nothing; widen SMOLDERING before recording show-first")
    return adapter.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1]))


def show_missing(adapter, client):
    """The recording must end in not_found: any other outcome means the missing-uid shape changed."""
    try:
        adapter.show(client, Curie("pubmed", MISSING))
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError(f"PubMed answered a record for uid {MISSING}; pick a uid it does not hold")


def files_oa(adapter, client):
    record, listed = adapter.files(client, OA_PMID)
    assert record.domain_fields["core"]["open_access"] == "Y"
    assert [f.name for f in listed] == ["PMC5798764.xml", "PMC5798764.bioc.json"]
    return record, listed


def files_non_oa(adapter, client):
    record, listed = adapter.files(client, NON_OA_PMID)
    assert record.domain_fields["core"] == {"open_access": "N", "in_epmc": "Y", "in_pmc": "Y"}
    assert listed == []
    return record, listed


def files_manuscript_non_oa(adapter, client):
    record, listed = adapter.files(client, MANUSCRIPT_NON_OA_PMID)
    assert record.domain_fields["core"] == {"open_access": "N", "in_epmc": "Y", "in_pmc": "Y"}
    assert listed == []
    return record, listed


def files_no_pmc(adapter, client):
    record, listed = adapter.files(client, NO_PMC_PMID)
    assert record.domain_fields["core"] == {"in_pmc": "N"}
    assert listed == []
    return record, listed


def files_missing(adapter, client):
    try:
        adapter.files(client, Curie("pubmed", MISSING))
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError(f"PubMed answered files for uid {MISSING}; pick a uid it does not hold")


SCENARIOS = {
    "search-smoldering": lambda adapter, client: adapter.search(client, SMOLDERING),
    "show-first": show_first,
    "show-missing": show_missing,
    "files-oa": files_oa,
    "files-non-oa": files_non_oa,
    "files-manuscript-non-oa": files_manuscript_non_oa,
    "files-no-pmc": files_no_pmc,
    "files-missing": files_missing,
}
