"""Recorded exchanges for tests (spec §12). A request key of method, URL, sorted query and
body, with credential parameters left out; a replaying transport that answers each recording
once, in recorded order, and refuses any request it has none left for; and a recording
transport that scrubs credentials before saving."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

import httpx

from quarry.catalog import Catalog
from quarry.http import REDACTED, Client, Throttle, _scrub, _with_key_param

KEPT_HEADERS = ("content-type", "retry-after", "location")


class VirtualClock:
    """A clock that advances when slept on, without sleeping: replays wait for nothing."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def replay_client(transport: httpx.BaseTransport) -> Client:
    """A client for tests: its own throttle on a virtual clock, never the process throttle."""
    return Client(transport=transport, throttle=Throttle(VirtualClock()))


def secret_params(catalog: Catalog) -> frozenset[str]:
    return frozenset(r.key_param for res in catalog.resources.values() for r in res.routes if r.key_param)


def exchange_key(request: httpx.Request, secrets: frozenset[str]) -> dict:
    url = request.url
    query = sorted([k, v] for k, v in url.params.multi_items() if k not in secrets)
    body = request.content.decode() if request.content else None
    path = url.raw_path.split(b"?", 1)[0].decode("ascii")
    # shortcut: catalog routes use default ports; include the port when one needs otherwise.
    return {"method": request.method, "url": f"{url.scheme}://{url.host}{path}", "query": query, "body": body}


def exchange(method: str, url: str, params: dict | None = None, *, status: int = 200, body: str = "",
             content_type: str = "application/json", headers: dict | None = None, json_body: object = None,
             form: dict | None = None) -> dict:
    """A hand-made exchange, keyed exactly as a live request with these arguments would be.
    `body` is the response body; `json_body` and `form` are the request's."""
    request = httpx.Request(method, url, params=params, json=json_body, data=form)
    return {"request": exchange_key(request, frozenset()),
            "response": {"status": status, "headers": {"content-type": content_type, **(headers or {})}, "body": body}}


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, exchanges: list[dict], secrets: frozenset[str] = frozenset()):
        self.pending = list(exchanges)
        self.secrets = secrets

    @classmethod
    def from_files(cls, paths: Iterable[Path], secrets: frozenset[str] = frozenset()) -> ReplayTransport:
        return cls([e for p in paths for e in json.loads(Path(p).read_text())], secrets)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = exchange_key(request, self.secrets)
        for index, ex in enumerate(self.pending):
            if ex["request"] == key:
                del self.pending[index]
                r = ex["response"]
                return httpx.Response(r["status"], headers=r["headers"], content=r["body"].encode(), request=request)
        raise LookupError(f"no recorded exchange left for {json.dumps(key)}")


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, secrets: frozenset[str], secret_values: Iterable[str], inner: httpx.BaseTransport | None = None):
        self.secrets = secrets
        self.secret_values = [v for v in secret_values if v]
        self.inner = inner or httpx.HTTPTransport()
        self.exchanges: list[dict] = []

    def _scrub(self, text: str) -> str:
        """Replace each credential in every spelling a response can echo it in: as is, and
        percent- or form-encoded (a key with `+` or `/` comes back as `%2B` or `%2F`)."""
        return _scrub(text, self.secret_values)

    def _scrub_request(self, value):
        if isinstance(value, dict):
            return {self._scrub(k): self._scrub_request(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub_request(v) for v in value]
        return self._scrub(value) if isinstance(value, str) else value

    def _scrub_body(self, text: str) -> str:
        try:
            json.loads(text)
        except ValueError:
            return self._scrub(text)

        def scrub_token(match):
            value = json.loads(match[0]) if match[0].startswith('"') else match[0]
            scrubbed = self._scrub(value)
            return json.dumps(scrubbed) if scrubbed != value else match[0]

        # Decode tokens, preserving whitespace and untouched escapes for exact replay.
        return re.sub(r'"(?:[^"\\]|\\.)*"|[^{}\[\],:\s]+', scrub_token, text)

    def _scrub_url(self, text: str) -> str:
        url = httpx.URL(text)
        for name in self.secrets & set(url.params):
            url = _with_key_param(url, name, REDACTED)
        return self._scrub(str(url))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        content = response.read()
        recorded_request = self._scrub_request(exchange_key(request, self.secrets))
        if request.content:
            recorded_request["body"] = self._scrub_body(request.content.decode())
        self.exchanges.append({
            "request": recorded_request,
            "response": {"status": response.status_code,
                         "headers": {h: (self._scrub_url if h == "location" else self._scrub)(response.headers[h])
                                     for h in KEPT_HEADERS if h in response.headers},
                         "body": self._scrub_body(content.decode(errors="replace"))},
        })
        return httpx.Response(response.status_code, headers=response.headers, content=content, request=request)

    def close(self) -> None:
        self.inner.close()

    def take(self) -> list[dict]:
        """The exchanges recorded since the last take, for one scenario's file."""
        taken, self.exchanges = self.exchanges, []
        return taken

    @staticmethod
    def save(exchanges: list[dict], path: Path) -> None:
        path.write_text(json.dumps(exchanges, indent=1, sort_keys=True) + "\n")
