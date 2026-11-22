"""The shared query: phrases combine with OR, filters with AND; --native replaces
both and carries its own dates, while -n stays quarry's. A domain's time filters,
--during (an observation interval) and --at (an instant), are typed here."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import click

RELATIVE = re.compile(r"^(\d+)([dw])$")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
INSTANT = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,6}))?)?Z")
RELATIONS = ("overlap", "start")      # how a record's observation time can satisfy a Window


def _utc(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.utcoffset() == timedelta(0)


@dataclass(frozen=True)
class Window:
    """The half-open observation interval [start, end) of --during, both ends UTC."""
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not (_utc(self.start) and _utc(self.end)):
            raise ValueError("--during takes UTC times")
        if self.start >= self.end:
            raise ValueError("--during START must be before END")

    def admits(self, relation: str, r_start: datetime, r_end: datetime | None) -> bool:
        """Whether a record observed over [r_start, r_end), or at the instant r_start when r_end is
        None or equal to it, satisfies the relation: overlap, or start (r_start within the window)."""
        if relation == "start":
            return self.start <= r_start < self.end
        if relation != "overlap":
            raise ValueError(f"a window admits records by {' or '.join(RELATIONS)}, not {relation}")
        return r_start < self.end and ((r_end is not None and r_end > self.start) or r_start >= self.start)


@dataclass(frozen=True)
class Query:
    phrases: tuple[str, ...] = ()
    filters: Mapping[str, str] = field(default_factory=dict)
    since: date | None = None
    until: date | None = None
    limit: int | None = None
    exact: bool = False
    native: str | None = None
    during: Window | None = None
    at: datetime | None = None

    def filter_names(self) -> set[str]:
        """Every domain filter the query carries, the time filters included."""
        names = set(self.filters)
        if self.during is not None:
            names.add("during")
        if self.at is not None:
            names.add("at")
        return names

    def __post_init__(self) -> None:
        if self.native is not None:
            if self.phrases or self.filters or self.exact:
                raise ValueError("--native replaces phrases, filters and --exact; give it alone")
            if self.since or self.until:
                raise ValueError("--since and --until are refused beside --native: dates belong in the native query")
            if self.during is not None or self.at is not None:
                raise ValueError("--during and --at are refused beside --native: times belong in the native query")
        elif not self.phrases and not self.filter_names():
            raise ValueError("search needs phrases, a domain filter or --native")
        if self.exact and not self.phrases and self.native is None:
            raise ValueError("--exact needs phrases")
        if self.during is not None and self.at is not None:
            raise ValueError("--during and --at cannot be combined")
        if self.at is not None and not _utc(self.at):
            raise ValueError("--at must be a UTC datetime")
        if any(not p.strip() for p in self.phrases):
            raise ValueError("a phrase is empty")
        for name, value in self.filters.items():
            if not value.strip():
                raise ValueError(f"--{name} must not be empty")
        if self.limit is not None and self.limit < 1:
            raise ValueError("-n must be at least 1")
        if self.since and self.until and self.since > self.until:
            raise ValueError("--since is after --until")


def parse_when(text: str, today: date) -> date:
    match = RELATIVE.match(text)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        try:
            return today - timedelta(days=n if unit == "d" else 7 * n)
        except OverflowError:
            raise ValueError("relative date is outside the supported range") from None
    return date.fromisoformat(text)


class When(click.ParamType):
    name = "when"

    def convert(self, value, param, ctx):
        if isinstance(value, date):
            return value
        try:
            return parse_when(value, date.today())
        except ValueError:
            self.fail(f"{value!r} is not YYYY-MM-DD, <n>d or <n>w", param, ctx)


WHEN = When()


def parse_instant(text: str) -> datetime:
    """A UTC datetime written YYYY-MM-DDTHH:MM[:SS[.ffffff]]Z, and nothing looser."""
    match = INSTANT.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not a UTC datetime (YYYY-MM-DDTHH:MM[:SS[.ffffff]]Z)")
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0),
                        int((fraction or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"{text!r} is not a calendar time") from None


def _endpoint(text: str, *, end: bool) -> datetime:
    """A --during endpoint: a date starts at 00:00Z, and a date END runs to the end of its day."""
    if DATE.fullmatch(text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a calendar date") from None
        try:
            return datetime.combine(day + timedelta(days=1) if end else day, time(), timezone.utc)
        except OverflowError:
            raise ValueError(f"{text!r} as END is outside the supported range") from None
    if INSTANT.fullmatch(text):
        return parse_instant(text)
    raise ValueError(f"{text!r} is not a date or a UTC datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS[.ffffff]]Z)")


def parse_window(text: str) -> Window:
    """--during START/END as the half-open window [S, E) (spec 2026-11-22 §5.1-5.2)."""
    parts = text.split("/")
    if len(parts) != 2:
        raise ValueError(f"{text!r} is not START/END")
    return Window(_endpoint(parts[0], end=False), _endpoint(parts[1], end=True))


class WindowType(click.ParamType):
    name = "window"

    def convert(self, value, param, ctx):
        if isinstance(value, Window):
            return value
        try:
            return parse_window(value)
        except ValueError as error:
            self.fail(str(error), param, ctx)


class InstantType(click.ParamType):
    name = "instant"

    def convert(self, value, param, ctx):
        if isinstance(value, datetime):
            return value
        try:
            return parse_instant(value)
        except ValueError as error:
            self.fail(str(error), param, ctx)


WINDOW = WindowType()
INSTANT_TIME = InstantType()
