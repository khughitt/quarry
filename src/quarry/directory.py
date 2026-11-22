"""Shared helpers for HTTP(S) directory-tree and bulk-archive adapters:
HTML index parsing, HTTP header parsing, UTC ISO formatting, and calendar bucket enumeration."""
from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import unquote

from quarry.http import UpstreamError
from quarry.query import Window

APACHE_ISO_DATE_RE = re.compile(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\s+[0-9]{2}:[0-9]{2}")
APACHE_DMY_DATE_RE = re.compile(
    r"\b([0-9]{1,2})-([A-Za-z]{3})-([0-9]{4})\s+[0-9]{2}:[0-9]{2}"
)
MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(frozen=True)
class IndexEntry:
    name: str
    is_dir: bool
    modified_date: str | None = None


def iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_http_datetime(header: str | None) -> datetime | None:
    if not header or not header.strip():
        return None
    try:
        dt = email.utils.parsedate_to_datetime(header.strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_http_date(header: str | None) -> str | None:
    dt = parse_http_datetime(header)
    return dt.date().isoformat() if dt is not None else None


def parse_content_length(header: str | None) -> int | None:
    if header is None:
        return None
    stripped = header.strip()
    if not stripped or not stripped.isdigit():
        return None
    return int(stripped)


def data_media_type(header: str | None, fallback: str, source: str) -> str:
    """Return the media type of a data-file HEAD response, failing early on HTML error/landing pages."""
    raw = (header or "").split(";", 1)[0].strip()
    if raw.lower() in HTML_MEDIA_TYPES:
        raise UpstreamError(f"{source}: HEAD returned {raw!r} instead of a data file")
    return raw if raw else fallback


def _extract_modified_date(text: str) -> str | None:
    m = APACHE_ISO_DATE_RE.search(text)
    if m:
        try:
            return date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            return None
    m2 = APACHE_DMY_DATE_RE.search(text)
    if m2:
        month = MONTH_ABBR.get(m2.group(2).lower())
        if month is not None:
            try:
                return date(int(m2.group(3)), month, int(m2.group(1))).isoformat()
            except ValueError:
                return None
    return None


class _IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_html = False
        self._in_a = False
        self._current_href: str | None = None
        self._trail: list[str] = []
        self.raw: list[tuple[str, str]] = []

    def _flush(self) -> None:
        if self._current_href is not None:
            self.raw.append((self._current_href, " ".join(self._trail)))
            self._current_href = None
            self._trail = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.saw_html = True
        low = tag.lower()
        if low == "a":
            self._flush()
            href = dict(attrs).get("href")
            self._current_href = href if href is not None else None
            self._in_a = True
        elif low in ("tr", "li", "br", "hr"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low == "a":
            self._in_a = False
        elif low in ("tr", "li", "pre", "table", "body"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._current_href is None or self._in_a:
            return
        if "\n" in data:
            head, _ = data.split("\n", 1)
            if head:
                self._trail.append(head)
            self._flush()
        else:
            self._trail.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def parse_index(html: str, source: str) -> list[IndexEntry]:
    """Parse an Apache/HTTP HTML directory listing into relative file/directory entries."""
    parser = _IndexParser()
    parser.feed(html)
    parser.close()
    if not parser.saw_html:
        raise UpstreamError(f"{source}: expected an HTML directory listing")

    entries: list[IndexEntry] = []
    for href, trail in parser.raw:
        raw_href = href.strip()
        if (
            not raw_href
            or raw_href.startswith(("?", "#", "/", "."))
            or "://" in raw_href
            or raw_href.lower().startswith(("mailto:", "javascript:"))
        ):
            continue
        is_dir = raw_href.endswith("/")
        cleaned = raw_href.rstrip("/")
        if "/" in cleaned or "\\" in cleaned or cleaned in ("", ".", ".."):
            continue
        name = unquote(cleaned)
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            continue
        entries.append(IndexEntry(name=name, is_dir=is_dir, modified_date=_extract_modified_date(trail)))
    return entries


def months_in_window(window: Window) -> list[tuple[int, int]]:
    """Every (year, month) whose UTC month span overlaps the half-open window [start, end)."""
    last = window.end - timedelta(microseconds=1)
    y, m = window.start.year, window.start.month
    end_ym = (last.year, last.month)
    out: list[tuple[int, int]] = []
    while (y, m) <= end_ym:
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def days_in_window(window: Window) -> list[date]:
    """Every UTC calendar date whose day span overlaps the half-open window [start, end)."""
    cur = window.start.date()
    last = (window.end - timedelta(microseconds=1)).date()
    out: list[date] = []
    while cur <= last:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def month_span(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return start, end


def day_span(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), timezone.utc)
    return start, start + timedelta(days=1)


def is_safe_relative_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or any(c.isspace() for c in path):
        return False
    if "?" in path or "#" in path:
        return False
    parts = path.split("/")
    return all(p and p not in (".", "..") for p in parts)
