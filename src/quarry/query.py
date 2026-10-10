"""The shared query (spec §8.2): phrases combine with OR, filters with AND; --native replaces
both and carries its own dates, while -n stays quarry's."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

import click

RELATIVE = re.compile(r"^(\d+)([dw])$")


@dataclass(frozen=True)
class Query:
    phrases: tuple[str, ...] = ()
    filters: Mapping[str, str] = field(default_factory=dict)
    since: date | None = None
    until: date | None = None
    limit: int | None = None
    exact: bool = False
    native: str | None = None

    def __post_init__(self) -> None:
        if self.native is not None:
            if self.phrases or self.filters or self.exact:
                raise ValueError("--native replaces phrases, filters and --exact; give it alone")
            if self.since or self.until:
                raise ValueError("--since and --until are refused beside --native: dates belong in the native query")
        elif not self.phrases:
            raise ValueError("search needs phrases or --native")
        if any(not p.strip() for p in self.phrases):
            raise ValueError("a phrase is empty")
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
