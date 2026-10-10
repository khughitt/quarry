"""The one HTTP client (spec §9). A throttle per service and key is shared by every client in
the process; retries back off and honour Retry-After within a wait budget; redirects are
followed for GET only, and a credential never leaves its origin or reaches a trace line or an
error detail."""
from __future__ import annotations

import email.utils
import importlib.metadata
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, unquote_plus

import httpx

from quarry.catalog import Route
from quarry.errors import QuarryError

REDACTED = "REDACTED"
RETRY_STATUS = frozenset({408, 429, *range(500, 600)})
MAX_ATTEMPTS = 5
WAIT_BUDGET = 60.0
MAX_REDIRECTS = 5
TIMEOUT = 60.0


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class Throttle:
    """The minimum interval between requests to one service under one key."""

    def __init__(self, clock):
        self.clock = clock
        self._last: dict[tuple[str, str | None], float] = {}

    def wait(self, service: str, key: str | None, interval: float) -> None:
        last = self._last.get((service, key))
        if last is not None:
            gap = interval - (self.clock.monotonic() - last)
            if gap > 0:
                self.clock.sleep(gap)
        self._last[(service, key)] = self.clock.monotonic()


PROCESS_THROTTLE = Throttle(SystemClock())


class UpstreamError(QuarryError):
    def __init__(self, detail: str):
        super().__init__("upstream_failed", detail)


def _retry_after(response: httpx.Response) -> float | None:
    """Retry-After as seconds (RFC 9110 §10.2.3: delay-seconds or an HTTP-date), or None."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return (url.scheme, url.host, url.port)


def _with_key_param(url: httpx.URL, name: str, value: str | None) -> httpx.URL:
    # Preserve echo spellings and other query values while replacing declared auth.
    query = [pair for pair in url.query.split(b"&") if pair and
             unquote_plus(pair.split(b"=", 1)[0].decode()) != name]
    if value is not None:
        query.append(str(httpx.QueryParams({name: value})).encode())
    return url.copy_with(query=b"&".join(query))


def _credential_pattern(secrets: Iterable[str]) -> re.Pattern:
    spellings = {spelling for value in secrets if value
                 for spelling in (value, quote(value, safe=""), quote(value), quote_plus(value),
                                  quote(value, safe=":/?#[]@!$&'()*+,;="))}
    patterns = [re.sub(r"%[0-9A-Fa-f]{2}", lambda m: f"(?i:{m[0]})", re.escape(spelling))
                for spelling in sorted(spellings, key=len, reverse=True)]
    return re.compile("|".join(patterns) if patterns else r"(?!)")


def _scrub(text: str, secrets: Iterable[str]) -> str:
    """Scrub literal, percent-encoded and form-encoded credential echoes."""
    return _credential_pattern(secrets).sub(REDACTED, text)


class Client:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, throttle: Throttle | None = None,
                 trace: Callable[[str], None] | None = None, environ: Mapping[str, str] | None = None):
        version = importlib.metadata.version("quarry")
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False,
                                  headers={"User-Agent": f"quarry/{version} (+https://github.com/khughitt/quarry)"})
        self._throttle = throttle or PROCESS_THROTTLE
        self._trace = trace
        self._environ = os.environ if environ is None else environ

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _key(self, route: Route) -> str | None:
        return (self._environ.get(route.key_env) or None) if route.key_env else None

    def _shown(self, url: httpx.URL, route: Route, key: str | None) -> str:
        if route.key_param and route.key_param in url.params:
            url = _with_key_param(url, route.key_param, REDACTED)
        return _scrub(str(url), [key] if key else [])

    def _log(self, line: str) -> None:
        if self._trace is not None:
            self._trace(line)

    def _send(self, route: Route, request: httpx.Request, shown: str, key: str | None, interval: float,
              credentials: dict[str, str]) -> httpx.Response:
        """Send, following GET redirects. Every hop waits on the throttle and is traced, redacted.
        Each hop is a fresh request: on the original origin it carries the credential header and
        the key parameter again; a hop elsewhere carries neither."""
        origin = _origin(request.url)
        for hop in range(MAX_REDIRECTS + 1):
            if hop:
                self._throttle.wait(route.service, key, interval)
            response = self._http.send(request)
            self._log(f"{request.method} {self._shown(request.url, route, key)} -> {response.status_code}")
            if not response.is_redirect:
                return response
            if request.method != "GET":
                raise UpstreamError(f"{request.method} {shown}: redirected; quarry follows redirects for GET only")
            target = request.url.join(response.headers["Location"])
            same = _origin(target) == origin
            if route.key_param:
                target = _with_key_param(target, route.key_param, key if same else None)
            if not same and key and _credential_pattern([key]).search(str(target)):
                raise UpstreamError(f"{shown}: foreign redirect contains a credential")
            request = self._http.build_request("GET", target, headers=credentials if same else {})
        raise UpstreamError(f"{shown}: more than {MAX_REDIRECTS} redirects")

    def request(self, route: Route, method: str, url: str, *, params: Mapping | None = None,
                json: object = None, data: Mapping | None = None) -> httpx.Response:
        key = self._key(route)
        params = dict(params or {})
        headers = {}
        if key:
            if route.key_param:
                params[route.key_param] = key
            if route.key_header:
                headers[route.key_header] = key
        full = url if url.startswith(("http://", "https://")) else f"{route.url.rstrip('/')}/{url.lstrip('/')}"
        shown = self._shown(httpx.URL(full, params=params), route, key)
        interval = route.keyed_min_interval if key and route.keyed_min_interval is not None else route.min_interval
        sleep = self._throttle.clock.sleep
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle.wait(route.service, key, interval)
            request = self._http.build_request(method, full, params=params, json=json, data=data, headers=headers)
            try:
                response = self._send(route, request, shown, key, interval, headers)
            except httpx.TransportError as error:
                self._log(f"{method} {shown} -> {type(error).__name__}")
                if attempt == MAX_ATTEMPTS:
                    raise UpstreamError(f"{method} {shown}: {type(error).__name__} after {attempt} attempts") from None
                sleep(2 ** (attempt - 1))
                continue
            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                delay = _retry_after(response)
                if delay is not None and delay > WAIT_BUDGET:
                    raise UpstreamError(f"{method} {shown}: HTTP {response.status_code}, asked to wait "
                                        f"{delay:.0f} s, beyond the {WAIT_BUDGET:.0f} s budget")
                sleep(delay if delay is not None else 2 ** (attempt - 1))
                continue
            if response.status_code >= 400:
                raise UpstreamError(f"{method} {shown}: HTTP {response.status_code}")
            return response
        raise AssertionError("unreachable: the last attempt returns or raises")
