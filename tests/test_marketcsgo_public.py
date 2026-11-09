import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.sources.marketcsgo import MarketCsgoPublic, parse_feed

FIX = Path(__file__).parent / "fixtures"
ASKS = (FIX / "mc_asks.json").read_text()
BIDS = (FIX / "mc_bids.json").read_text()


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def public(tmp_path, handler, **kw) -> MarketCsgoPublic:
    return MarketCsgoPublic("gha", tmp_path / "q", backoff_s=0, client=client(handler), **kw)


def route(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=BIDS if "orders" in request.url.path else ASKS)


def test_asks_parse(tmp_path):
    snap = public(tmp_path, route).asks()
    raw = json.loads(ASKS)
    assert snap.kind == "asks" and snap.host == "gha"
    assert snap.feed_time == datetime.fromtimestamp(raw["time"], UTC)
    assert len(snap.rows) == len(raw["items"]) >= 20
    assert snap.rows[0].ask == float(raw["items"][0]["price"])
    assert snap.rows[0].listings == raw["items"][0]["volume"]
    assert snap.fetched_at.tzinfo is not None


def test_bids_parse(tmp_path):
    snap = public(tmp_path, route).bids()
    raw = json.loads(BIDS)
    assert snap.kind == "bids"
    assert snap.rows[0].bid == raw["items"][0]["price"]
    assert snap.rows[0].orders == raw["items"][0]["volume"]


def test_malformed_goes_to_quarantine(tmp_path):
    bad = json.loads(ASKS)
    bad["items"][3]["price"] = "n/a"

    mc = public(tmp_path, lambda r: httpx.Response(200, text=json.dumps(bad)))
    with pytest.raises(SchemaError) as e:
        mc.asks()
    assert e.value.source == "mc_public"
    files = list((tmp_path / "q").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) == bad


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(success=False),
    lambda b: b.pop("time"),
    lambda b: b.pop("items"),
    lambda b: b.update(currency="EUR"),
])
def test_parse_feed_rejects(mutate):
    body = json.loads(ASKS)
    mutate(body)
    with pytest.raises(SchemaError):
        parse_feed("asks", body)


def test_price_type_is_per_kind():
    with pytest.raises(SchemaError):
        parse_feed("asks", json.loads(BIDS))
    with pytest.raises(SchemaError):
        parse_feed("bids", json.loads(ASKS))


def test_non_json_body_quarantined(tmp_path):
    mc = public(tmp_path, lambda r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(SchemaError):
        mc.asks()
    assert len(list((tmp_path / "q").glob("*.json"))) == 1


def test_retries_then_succeeds(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(503) if len(calls) == 2 else route(request)

    assert len(public(tmp_path, handler).asks().rows) > 0
    assert len(calls) == 3


@pytest.mark.parametrize("status", [503, 429])
def test_exhausted_retries(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(SourceUnavailable):
        public(tmp_path, handler, max_retries=2).asks()
    assert len(calls) == 3
