"""The shared query: phrases OR, filters AND, --native instead of both."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from quarry.query import Query, Window, parse_instant, parse_when, parse_window
from quarry.results import Result
from quarry.search import completeness, distinct, trimmed
from quarry.errors import QuarryError

UTC = timezone.utc


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_valid_queries():
    Query(phrases=("myeloma", "plasmacytoma"), filters={"organism": "ncbitaxon:9606"}, limit=10)
    Query(native="cancer[Title]", limit=10)          # -n stays allowed beside --native


@pytest.mark.parametrize("name", ["organism", "other"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_blank_filters_are_refused_at_the_shared_query_boundary(name, value):
    with pytest.raises(ValueError, match=f"--{name} must not be empty"):
        Query(phrases=("x",), filters={name: value})


@pytest.mark.parametrize("kwargs, message", [
    ({}, "needs phrases, a domain filter or --native"),
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


def test_distinct_passes_unique_rows_and_fails_on_a_repeated_id():
    rows = [Result(id=f"x:{i}", resource="x", kind="dataset", title="t") for i in (1, 2)]
    assert distinct(rows) == rows
    with pytest.raises(QuarryError) as caught:
        distinct([*rows, rows[0]])
    assert caught.value.kind == "incomplete_listing" and "x:1" in caught.value.detail


@pytest.mark.parametrize("text", ["999999d", "999999999999d"])
def test_relative_date_outside_supported_range_is_invalid(text):
    with pytest.raises(ValueError, match="relative date is outside the supported range"):
        parse_when(text, date(2026, 10, 10))


def test_valid_phraseless_queries_carry_a_domain_filter():
    window = Window(utc(2025, 10, 1), utc(2025, 10, 2))
    assert Query(during=window).filter_names() == {"during"}
    assert Query(at=utc(2025, 10, 1)).filter_names() == {"at"}
    assert Query(filters={"organism": "Homo sapiens"}).filter_names() == {"organism"}
    assert Query(phrases=("x",)).filter_names() == set()


@pytest.mark.parametrize("kwargs, message", [
    ({"exact": True, "filters": {"organism": "y"}}, "--exact needs phrases"),
    ({"phrases": ("a",), "during": Window(utc(2025, 10, 1), utc(2025, 10, 2)), "at": utc(2025, 10, 1)},
     "--during and --at cannot be combined"),
    ({"native": "x", "during": Window(utc(2025, 10, 1), utc(2025, 10, 2))}, "refused beside --native"),
    ({"native": "x", "at": utc(2025, 10, 1)}, "refused beside --native"),
    ({"phrases": ("a",), "at": datetime(2025, 10, 1)}, "--at must be a UTC datetime"),
    ({"phrases": ("a",), "at": datetime(2025, 10, 1, tzinfo=timezone(timedelta(hours=1)))}, "--at must be a UTC datetime"),
    ({"phrases": ("a",), "at": datetime(2025, 1, 1, tzinfo=ZoneInfo("Europe/London"))}, "--at must be a UTC datetime"),
])
def test_invalid_time_queries(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Query(**kwargs)


@pytest.mark.parametrize("start, end, message", [
    (datetime(2025, 10, 1), utc(2025, 10, 2), "UTC"),
    (utc(2025, 10, 1), datetime(2025, 10, 2, tzinfo=timezone(timedelta(hours=-5))), "UTC"),
    (utc(2025, 10, 2), utc(2025, 10, 1), "START must be before END"),
    (utc(2025, 10, 1), utc(2025, 10, 1), "START must be before END"),
    (utc(2025, 1, 1), datetime(2025, 1, 2, tzinfo=ZoneInfo("Europe/London")), "UTC"),  # +00:00 in winter, not UTC
])
def test_invalid_windows(start, end, message):
    with pytest.raises(ValueError, match=message):
        Window(start, end)


@pytest.mark.parametrize("text, start, end", [
    ("2025-10-01/2025-10-01", utc(2025, 10, 1), utc(2025, 10, 2)),               # a date END is its whole day
    ("2025-10-01/2025-10-03", utc(2025, 10, 1), utc(2025, 10, 4)),
    ("2025-10-01T00:00Z/2025-10-01T01:00Z", utc(2025, 10, 1), utc(2025, 10, 1, 1)),  # a datetime END is exclusive
    ("2025-10-01T12:00Z/2025-10-01", utc(2025, 10, 1, 12), utc(2025, 10, 2)),     # mixed endpoints
    ("2025-09-30/2025-10-01T00:00:30.5Z", utc(2025, 9, 30), utc(2025, 10, 1, 0, 0, 30, 500000)),
    ("2025-10-01T00:00:00.000001Z/2025-10-01T00:00:01Z", utc(2025, 10, 1, 0, 0, 0, 1), utc(2025, 10, 1, 0, 0, 1)),
])
def test_parse_window(text, start, end):
    assert parse_window(text) == Window(start, end)


@pytest.mark.parametrize("text, message", [
    ("2025-10-01", "START/END"),
    ("2025-10-01/2025-10-02/2025-10-03", "START/END"),
    ("2025-10-01/", "date or a UTC datetime"),
    ("20251001/20251002", "date or a UTC datetime"),
    ("2025-10-01T00:00+00:00/2025-10-02", "date or a UTC datetime"),
    ("2025-10-01T00:00z/2025-10-02", "date or a UTC datetime"),
    ("2025-10-01t00:00Z/2025-10-02", "date or a UTC datetime"),
    ("2025-10-01 00:00Z/2025-10-02", "date or a UTC datetime"),
    ("2025-10-01T00:00/2025-10-02", "date or a UTC datetime"),
    ("2025-10-01T00:00:00.1234567Z/2025-10-02", "date or a UTC datetime"),
    ("2025-02-30/2025-03-01", "not a calendar"),
    ("2025-10-01T24:00Z/2025-10-02", "not a calendar"),
    ("2025-10-02/2025-10-01", "START must be before END"),
    ("2025-10-01T01:00Z/2025-10-01T01:00Z", "START must be before END"),
    ("2025-10-01/9999-12-31", "outside the supported range"),
])
def test_parse_window_refuses(text, message):
    with pytest.raises(ValueError, match=message):
        parse_window(text)


def test_parse_instant():
    assert parse_instant("2025-10-01T00:00:37Z") == utc(2025, 10, 1, 0, 0, 37)
    for text in ("2025-10-01", "2025-10-01T00:00+01:00", "2025-10-01T00:00"):
        with pytest.raises(ValueError, match="UTC datetime"):
            parse_instant(text)


WINDOW = Window(utc(2025, 10, 1, 0), utc(2025, 10, 1, 1))


@pytest.mark.parametrize("r_start, r_end, expected", [
    (utc(2025, 9, 30, 23), utc(2025, 10, 1, 0, 30), True),   # a span that began before S
    (utc(2025, 10, 1, 0, 30), utc(2025, 10, 1, 2), True),    # a span that continues after E
    (utc(2025, 9, 30), utc(2025, 10, 2), True),              # a span around the window
    (utc(2025, 9, 30, 23), utc(2025, 10, 1, 0), False),      # a span ending at S: [r_start, r_end) misses S
    (utc(2025, 10, 1, 1), utc(2025, 10, 1, 2), False),       # a span starting at E
    (utc(2025, 10, 1, 0), None, True),                       # an instant at S
    (utc(2025, 10, 1, 0), utc(2025, 10, 1, 0), True),        # an instant written with r_end = r_start
    (utc(2025, 10, 1, 1), None, False),                      # an instant at E
    (utc(2025, 9, 30, 23, 59), None, False),
])
def test_window_overlap(r_start, r_end, expected):
    assert WINDOW.admits("overlap", r_start, r_end) is expected


@pytest.mark.parametrize("r_start, r_end, expected", [
    (utc(2025, 9, 30, 23), utc(2025, 10, 1, 0, 30), False),  # began before S: start-within misses it
    (utc(2025, 10, 1, 0), utc(2025, 10, 3), True),
    (utc(2025, 10, 1, 1), None, False),
])
def test_window_start(r_start, r_end, expected):
    assert WINDOW.admits("start", r_start, r_end) is expected


def test_window_admits_only_its_relations():
    with pytest.raises(ValueError, match="nearest"):
        WINDOW.admits("nearest", utc(2025, 10, 1), None)


@pytest.mark.parametrize("args, expected", [
    (dict(kept=4, fetched=10, native_total=10, short_page=False, limit=None), (4, "total")),   # listing fetched to its end
    (dict(kept=4, fetched=10, native_total=None, short_page=True, limit=None), (4, "total")),
    (dict(kept=4, fetched=10, native_total=10, short_page=False, limit=5), (4, "total")),      # ended under -n
    (dict(kept=5, fetched=8, native_total=40, short_page=False, limit=5), (None, "limited")),  # stopped for -n
    (dict(kept=5, fetched=8, native_total=None, short_page=False, limit=5), (None, "limited")),
    (dict(kept=0, fetched=0, native_total=0, short_page=True, limit=None), (0, "total")),
])
def test_trimmed(args, expected):
    assert trimmed(**args) == expected


@pytest.mark.parametrize("args", [
    dict(kept=4, fetched=8, native_total=10, short_page=False, limit=None),   # stopped early with no -n
    dict(kept=4, fetched=8, native_total=10, short_page=True, limit=None),    # a short page before the total
    dict(kept=3, fetched=8, native_total=40, short_page=False, limit=5),      # stopped before -n was reached
    dict(kept=4, fetched=8, native_total=None, short_page=False, limit=None),
    dict(kept=4, fetched=11, native_total=10, short_page=False, limit=None),  # more rows than the total
])
def test_trimmed_refuses_an_unestablished_listing(args):
    with pytest.raises(QuarryError) as caught:
        trimmed(**args)
    assert caught.value.kind == "incomplete_listing"


def test_trimmed_refuses_more_kept_rows_than_the_limit():
    with pytest.raises(ValueError, match="-n"):
        trimmed(kept=6, fetched=8, native_total=None, short_page=False, limit=5)
