"""The shared query: phrases OR, filters AND, --native instead of both (spec §8.2)."""
from datetime import date

import pytest

from quarry.query import Query, parse_when
from quarry.search import completeness
from quarry.errors import QuarryError


def test_valid_queries():
    Query(phrases=("myeloma", "plasmacytoma"), filters={"organism": "ncbitaxon:9606"}, limit=10)
    Query(native="cancer[Title]", limit=10)          # -n stays allowed beside --native


@pytest.mark.parametrize("name", ["organism", "other"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_blank_filters_are_refused_at_the_shared_query_boundary(name, value):
    with pytest.raises(ValueError, match=f"--{name} must not be empty"):
        Query(phrases=("x",), filters={name: value})


@pytest.mark.parametrize("kwargs, message", [
    ({}, "needs phrases or --native"),
    ({"native": "x", "phrases": ("a",)}, "--native replaces phrases"),
    ({"native": "x", "filters": {"organism": "y"}}, "--native replaces phrases"),
    ({"native": "x", "exact": True}, "--native replaces phrases"),
    ({"native": "x", "since": date(2020, 1, 1)}, "dates belong in the native query"),
    ({"phrases": ("a",), "limit": 0}, "-n must be at least 1"),
    ({"phrases": ("a",), "since": date(2021, 1, 1), "until": date(2020, 1, 1)}, "--since is after --until"),  # Review Focus 4
    ({"phrases": ("",)}, "a phrase is empty"),
])
def test_invalid_queries(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Query(**kwargs)


def test_parse_when():
    today = date(2026, 10, 10)
    assert parse_when("2024-01-31", today) == date(2024, 1, 31)
    assert parse_when("10d", today) == date(2026, 9, 30)
    assert parse_when("2w", today) == date(2026, 9, 26)
    with pytest.raises(ValueError):
        parse_when("yesterday", today)


@pytest.mark.parametrize("args, expected", [
    (dict(total=3, returned=3, limit=None, short_page=False), "total"),
    (dict(total=3, returned=3, limit=5, short_page=False), "total"),
    (dict(total=9, returned=5, limit=5, short_page=False), "limited"),
    (dict(total=None, returned=5, limit=5, short_page=False), "limited"),
    (dict(total=None, returned=7, limit=None, short_page=True), "short-page"),
])
def test_completeness(args, expected):
    assert completeness(**args) == expected


@pytest.mark.parametrize("args", [
    dict(total=9, returned=7, limit=None, short_page=False),
    dict(total=9, returned=4, limit=5, short_page=False),
    dict(total=None, returned=7, limit=None, short_page=False),
])
def test_incomplete_listing_is_a_failure(args):
    with pytest.raises(QuarryError) as caught:
        completeness(**args)
    assert caught.value.kind == "incomplete_listing"


@pytest.mark.parametrize("text", ["999999d", "999999999999d"])
def test_relative_date_outside_supported_range_is_invalid(text):
    with pytest.raises(ValueError, match="relative date is outside the supported range"):
        parse_when(text, date(2026, 10, 10))
