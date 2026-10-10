"""The shared client: one throttle per service and key across the process, retries within a
wait budget, credentials that never leave their origin or reach a log (spec §9)."""
import email.utils
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from quarry.catalog import Route
from quarry.http import Client, Throttle, UpstreamError
from quarry.testing import RecordingTransport, ReplayTransport, exchange, exchange_key


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def route(service="eutils", interval=0.34, **kw):
    return Route(kind="rest", url="https://eutils.example/entrez", min_interval=interval, service=service,
                 auth=kw.pop("auth", "none"), reports_total=True, **kw)


def keyed(**kw):
    return route(auth="optional-key", key_env="TEST_KEY", keyed_min_interval=0.1, **kw)


def mock(handler):
    return httpx.MockTransport(handler)


def client(handler, clock=None, **kw):
    return Client(transport=mock(handler), throttle=Throttle(clock or FakeClock()), **kw)


def test_one_throttle_per_service_across_routes_and_clients():
    clock, times = FakeClock(), []
    throttle = Throttle(clock)

    def ok(request):
        times.append(clock.t)
        return httpx.Response(200)
    first, second = Client(transport=mock(ok), throttle=throttle), Client(transport=mock(ok), throttle=throttle)
    geo, pubmed = route(), route()
    for c, r in ((first, geo), (second, pubmed), (first, pubmed), (second, geo)):
        c.request(r, "GET", "esearch.fcgi")
    assert all(b - a >= 0.34 - 1e-9 for a, b in zip(times, times[1:]))
    before = clock.t
    first.request(route(service="ebi-ena"), "GET", "x")        # another service waits on nothing
    assert clock.t == before


def test_clients_share_the_process_throttle_by_default():
    a, b = Client(transport=mock(lambda r: httpx.Response(200))), Client(transport=mock(lambda r: httpx.Response(200)))
    assert a._throttle is b._throttle


def test_keyed_interval_and_key_parameter():
    seen, clock = [], FakeClock()
    c = client(lambda r: (seen.append(r.url), httpx.Response(200))[1], clock, environ={"TEST_KEY": "k1"})
    c.request(keyed(key_param="api_key"), "GET", "a")
    c.request(keyed(key_param="api_key"), "GET", "a")
    assert seen[0].params["api_key"] == "k1" and clock.slept == [pytest.approx(0.1)]


def test_empty_key_variable_is_unset():
    seen = []
    client(lambda r: (seen.append(r.url), httpx.Response(200))[1], environ={"TEST_KEY": ""}).request(
        keyed(key_param="api_key"), "GET", "a")
    assert "api_key" not in seen[0].params


def statuses(*codes, headers=None):
    codes = list(codes)
    return lambda request: httpx.Response(codes.pop(0), headers=headers or {})


def test_retries_transient_statuses_with_backoff():
    clock = FakeClock()
    assert client(statuses(503, 429, 200), clock).request(route(interval=0), "GET", "x").status_code == 200
    assert clock.slept == [1, 2]


def test_retry_after_seconds_and_http_date():
    clock = FakeClock()
    client(statuses(429, 200, headers={"Retry-After": "3"}), clock).request(route(interval=0), "GET", "x")
    assert clock.slept == [3]
    when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    clock = FakeClock()
    client(statuses(503, 200, headers={"Retry-After": when}), clock).request(route(interval=0), "GET", "x")
    assert 15 <= clock.slept[0] <= 21


def test_retry_after_beyond_the_budget_fails_without_retrying_early():    # Review Focus 5
    clock = FakeClock()
    with pytest.raises(UpstreamError, match="asked to wait 3600 s"):
        client(statuses(429, 200, headers={"Retry-After": "3600"}), clock).request(route(interval=0), "GET", "x")
    assert clock.slept == []


def test_client_errors_fail_at_once_and_exhaustion_fails():
    with pytest.raises(UpstreamError, match="HTTP 404"):
        client(statuses(404)).request(route(), "GET", "x")
    with pytest.raises(UpstreamError, match="HTTP 503"):
        client(statuses(*[503] * 5)).request(route(interval=0), "GET", "x")


def test_transport_errors_retry_then_fail():
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    clock = FakeClock()
    with pytest.raises(UpstreamError, match="ConnectError after 5 attempts"):
        client(down, clock).request(route(interval=0), "GET", "x")
    assert clock.slept == [1, 2, 4, 8]


def test_redirects_keep_credentials_on_their_origin_only():
    sentinel, seen = "SENTINEL-7f3a", []

    def hop(request):
        seen.append(request)
        if request.url.host == "eutils.example" and request.url.path.endswith("/start"):
            return httpx.Response(302, headers={"Location": "https://eutils.example/entrez/moved"})
        if request.url.path.endswith("/moved"):
            return httpx.Response(302, headers={"Location": f"https://elsewhere.example/x?api_key={sentinel}"})
        return httpx.Response(200)
    c = client(hop, environ={"TEST_KEY": sentinel})
    c.request(keyed(key_param="api_key", key_header="X-Api-Key"), "GET", "start")
    same, other = seen[1], seen[2]
    assert same.headers["X-Api-Key"] == sentinel                       # same origin keeps it
    assert other.url.host == "elsewhere.example"
    assert sentinel not in str(other.url) and "X-Api-Key" not in other.headers


def test_same_origin_redirect_keeps_a_query_key_and_every_hop_is_throttled_and_traced():
    sentinel, seen, lines, clock = "SENTINEL-7f3a", [], [], FakeClock()

    def hop(request):
        seen.append((clock.t, request))
        if request.url.path.endswith("/start"):
            return httpx.Response(302, headers={"Location": "/entrez/moved"})
        return httpx.Response(200)
    c = client(hop, clock, environ={"TEST_KEY": sentinel}, trace=lines.append)
    c.request(keyed(key_param="api_key"), "GET", "start")
    (t0, first), (t1, moved) = seen
    assert moved.url.params["api_key"] == sentinel               # query-only auth survives the hop
    assert t1 - t0 >= 0.1 - 1e-9                                 # the hop waited on the throttle
    assert len(lines) == 2 and all(sentinel not in line for line in lines)


def test_a_redirected_post_fails():
    with pytest.raises(UpstreamError, match="redirected"):
        client(lambda r: httpx.Response(307, headers={"Location": "https://eutils.example/y"})).request(
            route(), "POST", "x", data={"a": "1"})


def test_the_credential_never_leaks():
    sentinel, lines = "SENTINEL-7f3a", []

    def echo(request):
        return httpx.Response(500 if request.url.path.endswith("fail") else 200, text=f"echo {request.url}")
    recorder = RecordingTransport(frozenset({"api_key"}), [sentinel], inner=mock(echo))
    c = Client(transport=recorder, throttle=Throttle(FakeClock()), trace=lines.append, environ={"TEST_KEY": sentinel})
    c.request(keyed(key_param="api_key"), "GET", "ok", params={"term": "x"})
    with pytest.raises(UpstreamError) as caught:
        c.request(keyed(key_param="api_key"), "GET", "fail")
    recorded = json.dumps(recorder.exchanges)
    assert sentinel not in "\n".join(lines) and sentinel not in caught.value.detail and sentinel not in recorded
    assert "REDACTED" in "\n".join(lines)


def test_encoded_echoes_of_the_key_are_redacted_in_fixtures():
    sentinel = "k+ey/with=odd"

    def echo(request):
        encoded = httpx.QueryParams({"api_key": sentinel})
        return httpx.Response(302 if request.url.path.endswith("go") else 200,
                              headers={"Location": f"https://eutils.example/entrez/back?{encoded}"},
                              text=f"seen {encoded} and {sentinel}")
    recorder = RecordingTransport(frozenset({"api_key"}), [sentinel], inner=mock(echo))
    c = Client(transport=recorder, throttle=Throttle(FakeClock()), environ={"TEST_KEY": sentinel})
    c.request(keyed(key_param="api_key"), "GET", "go")
    recorded = json.dumps(recorder.exchanges)
    for spelling in (sentinel, "k%2Bey%2Fwith%3Dodd", "k%2Bey/with%3Dodd"):
        assert spelling not in recorded, spelling


def test_replay_answers_in_recorded_order_with_headers():
    first = exchange("GET", "https://eutils.example/entrez/x", status=503, headers={"retry-after": "1"})
    second = exchange("GET", "https://eutils.example/entrez/x", body='{"ok": true}')
    clock = FakeClock()
    c = Client(transport=ReplayTransport([first, second]), throttle=Throttle(clock))
    assert c.request(route(interval=0), "GET", "x").json() == {"ok": True} and clock.slept == [1]
    with pytest.raises(LookupError, match="no recorded exchange"):
        c.request(route(interval=0), "GET", "x")                       # each recording answers once


def test_replay_matches_method_url_sorted_query_and_body_but_not_the_key():
    ex = exchange("POST", "https://eutils.example/entrez/esummary.fcgi", {"db": "gds", "retmode": "json"},
                  form={"id": "1,2"}, body='{"ok": true}')
    http = httpx.Client(transport=ReplayTransport([ex], frozenset({"api_key"})))
    with pytest.raises(LookupError, match="no recorded exchange"):
        http.post("https://eutils.example/entrez/esummary.fcgi", params={"db": "gds", "retmode": "json"},
                  data={"id": "3"})
    hit = http.post("https://eutils.example/entrez/esummary.fcgi",
                    params={"retmode": "json", "db": "gds", "api_key": "secret"}, data={"id": "1,2"})
    assert hit.json() == {"ok": True}


def test_exchange_key_shape():
    request = httpx.Request("GET", "https://h.example/p", params={"b": "2", "a": "1"})
    assert exchange_key(request, frozenset()) == {"method": "GET", "url": "https://h.example/p",
                                                  "query": [["a", "1"], ["b", "2"]], "body": None}


@pytest.mark.parametrize('code', [408, 501, 505])
def test_all_transient_statuses_retry(code):
    clock = FakeClock()
    assert client(statuses(code, 200), clock).request(route(interval=0), 'GET', 'x').status_code == 200
    assert clock.slept == [1]


@pytest.mark.parametrize('header_only', [False, True])
@pytest.mark.parametrize('echo', ['k+ey/with=odd space', 'k%2Bey%2Fwith%3Dodd%20space',
                                  'k%2Bey/with%3Dodd%20space', 'k%2Bey%2Fwith%3Dodd+space'])
def test_foreign_redirect_refuses_echoed_credentials_before_sending(header_only, echo):
    seen, lines = [], []
    def hop(request):
        seen.append(request)
        return httpx.Response(302, headers={'Location': f'https://foreign.example/x?echo={echo}'})
    c = client(hop, environ={'TEST_KEY': 'k+ey/with=odd space'}, trace=lines.append)
    auth = {'key_header': 'X-Api-Key'} if header_only else {'key_param': 'api_key'}
    with pytest.raises(UpstreamError, match='credential') as caught:
        c.request(keyed(**auth), 'GET', 'start')
    assert len(seen) == 1 and seen[0].url.host == 'eutils.example'
    assert caught.value.kind == 'upstream_failed'
    assert echo not in caught.value.detail


@pytest.mark.parametrize('header_only', [False, True])
def test_complete_displayed_urls_scrub_credentials(header_only):
    sentinel, lines = 'k+ey/with=odd space', []
    def hop(request):
        if request.url.path.endswith('/start'):
            return httpx.Response(302, headers={'Location': '/entrez/k%2Bey/with%3Dodd%20space?echo=k%2Bey%2Fwith%3Dodd+space'})
        return httpx.Response(404)
    auth = {'key_header': 'X-Api-Key'} if header_only else {'key_param': 'api_key'}
    c = client(hop, environ={'TEST_KEY': sentinel}, trace=lines.append)
    with pytest.raises(UpstreamError) as caught:
        c.request(keyed(**auth), 'GET', 'start', params={'echo': sentinel})
    output = '\n'.join(lines) + caught.value.detail
    for spelling in (sentinel, 'k%2Bey%2Fwith%3Dodd%20space', 'k%2Bey/with%3Dodd%20space',
                     'k%2Bey%2Fwith%3Dodd+space'):
        assert spelling not in output
    assert 'REDACTED' in output and len(lines) == 2


def test_retry_after_hour_http_date_fails_without_sleep(monkeypatch):
    import quarry.http as http_mod
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(http_mod, 'datetime', FixedDatetime)
    when = email.utils.format_datetime(now + timedelta(hours=1), usegmt=True)
    clock = FakeClock()
    with pytest.raises(UpstreamError, match='asked to wait 3600 s'):
        client(statuses(503, 200, headers={'Retry-After': when}), clock).request(route(interval=0), 'GET', 'x')
    assert clock.slept == []


def test_recorded_request_echoes_and_body_are_scrubbed(tmp_path):
    sentinel = 'k+ey/with=odd'
    recorder = RecordingTransport(frozenset({'api_key'}), [sentinel], inner=mock(lambda r: httpx.Response(200)))
    with httpx.Client(transport=recorder) as c:
        c.post(f'https://eutils.example/entrez/x?api_key={sentinel}', params={'echo': sentinel}, data={'echo': sentinel})
    taken = recorder.take()
    assert recorder.exchanges == []
    path = tmp_path / 'record.json'
    RecordingTransport.save(taken, path)
    recorded = path.read_text()
    assert sentinel not in recorded and 'k%2Bey%2Fwith%3Dodd' not in recorded
    assert 'REDACTED' in recorded


def test_replay_matches_distinct_keys_independently_and_reads_saved_files(tmp_path):
    exs = [exchange('GET', 'https://eutils.example/entrez/a', body='first'),
           exchange('GET', 'https://eutils.example/entrez/b', body='other'),
           exchange('GET', 'https://eutils.example/entrez/a', body='last')]
    path = tmp_path / 'exchanges.json'
    RecordingTransport.save(exs, path)
    from quarry.testing import replay_client
    c = replay_client(ReplayTransport.from_files([path]))
    assert c.request(route(), 'GET', 'b').text == 'other'
    assert c.request(route(), 'GET', 'a').text == 'first'
    assert c.request(route(), 'GET', 'a').text == 'last'


def test_redirect_loop_fails_with_bound():
    seen = []
    def hop(request):
        seen.append(request)
        return httpx.Response(302, headers={'Location': '/entrez/start'})
    with pytest.raises(UpstreamError, match='more than 5 redirects'):
        client(hop).request(route(), 'GET', 'start')
    assert len(seen) == 6


@pytest.mark.parametrize('sentinel,echo', [('REDACTED', 'REDACTED'),
    ('k+ey/with=odd', 'k%2bey%2fwith%3dodd')])
def test_foreign_redirect_detects_credentials_even_if_scrubbing_would_not_change_text(sentinel, echo):
    seen = []
    def hop(request):
        seen.append(request)
        return httpx.Response(302, headers={'Location': f'https://foreign.example/x?echo={echo}'})
    with pytest.raises(UpstreamError, match='credential'):
        client(hop, environ={'TEST_KEY': sentinel}).request(keyed(key_header='X-Api-Key'), 'GET', 'start')
    assert len(seen) == 1


def test_scrubbed_same_origin_redirect_echoes_replay():
    sentinel = 'k+ey/with=odd space'
    def hop(request):
        if request.url.path.endswith('/start'):
            return httpx.Response(302, headers={'Location': '/entrez/done?echo=k%2Bey%2Fwith%3Dodd+space'})
        return httpx.Response(200, text='done')
    recorder = RecordingTransport(frozenset({'api_key'}), [sentinel], inner=mock(hop))
    c = Client(transport=recorder, throttle=Throttle(FakeClock()), environ={'TEST_KEY': sentinel})
    assert c.request(keyed(key_param='api_key'), 'GET', 'start').text == 'done'
    replay = ReplayTransport(recorder.exchanges, frozenset({'api_key'}))
    c = Client(transport=replay, throttle=Throttle(FakeClock()), environ={'TEST_KEY': sentinel})
    assert c.request(keyed(key_param='api_key'), 'GET', 'start').text == 'done'
    assert replay.pending == []


def test_recording_closes_its_inner_transport():
    class Transport(httpx.BaseTransport):
        closed = False
        def close(self):
            self.closed = True
    inner = Transport()
    RecordingTransport(frozenset(), [], inner=inner).close()
    assert inner.closed


@pytest.mark.parametrize('header', ['', 'not a date', 'Sat, 10 Oct 2026 12:00:00'])
def test_invalid_retry_after_uses_backoff(header):
    clock = FakeClock()
    assert client(statuses(503, 200, headers={'Retry-After': header}), clock).request(
        route(interval=0), 'GET', 'x').status_code == 200
    assert clock.slept == [1]


def test_same_origin_literal_echo_is_scrubbed_without_reencoding_other_parameters():
    sentinel, lines, seen = 'k+ey/with=odd space', [], []
    def hop(request):
        seen.append(request)
        if request.url.path.endswith('/start'):
            return httpx.Response(302, headers={'Location': '/entrez/done?echo=k+ey/with=odd space'})
        return httpx.Response(200)
    c = client(hop, environ={'TEST_KEY': sentinel}, trace=lines.append)
    c.request(keyed(key_param='api_key'), 'GET', 'start')
    assert 'echo=k+ey/with=odd%20space' in str(seen[1].url)
    assert all('k+ey' not in line for line in lines)
