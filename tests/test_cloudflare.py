import json
from datetime import UTC, datetime

import httpx
import pytest

from cs2trader.errors import SourceUnavailable
from cs2trader.sources.cloudflare import CfBuffer, import_raw
from cs2trader.store import Store

TOKEN = "tok-123456"
T = 1794414000


def feed(kind, t=T):
    price = "10.5" if kind == "asks" else 9.5
    return json.dumps({"success": True, "currency": "USD", "time": t,
                       "items": [{"market_hash_name": "AK", "volume": 3, "price": price}]}).encode()


class FakeWorker:
    def __init__(self, objects):
        self.objects, self.deleted = dict(objects), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-CS2-Token") != TOKEN:
            return httpx.Response(404)
        path = request.url.path
        if path == "/list":
            keys = sorted(self.objects)
            cursor = request.url.params.get("cursor")
            start = int(cursor) if cursor else 0
            page = keys[start:start + 2]  # tiny pages exercise the cursor
            nxt = str(start + 2) if start + 2 < len(keys) else None
            return httpx.Response(200, json={"objects": [{"key": k, "size": 1, "uploaded": "t"} for k in page],
                                             "cursor": nxt})
        if path == "/get":
            return httpx.Response(200, content=self.objects[request.url.params["key"]])
        if path == "/delete":
            keys = json.loads(request.content)["keys"]
            self.deleted += keys
            for k in keys:
                self.objects.pop(k, None)
            return httpx.Response(200, json={"deleted": len(keys)})
        return httpx.Response(404)


def buffer(worker):
    return CfBuffer("https://w.test", TOKEN, httpx.Client(transport=httpx.MockTransport(worker)))


def key(kind, stamp="2026-11-11T16-22-38-096Z"):
    return f"raw/{kind}/day=2026-11-11/{stamp}.json"


def test_imports_every_page_and_deletes_after_writing(tmp_path):
    w = FakeWorker({key("asks"): feed("asks"), key("bids"): feed("bids"),
                    key("asks", "2026-11-11T17-22-38-096Z"): feed("asks", T + 3600)})
    store = Store(tmp_path, in_memory=True)
    rep = import_raw(store, buffer(w), tmp_path / "q")
    assert (rep.imported, rep.bad, rep.deleted) == (3, 0, 3) and not w.objects
    assert store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM bids").fetchone()[0] == 1
    host, fetched = store.db.execute("SELECT host, CAST(fetched_at AS VARCHAR) FROM bids").fetchone()
    assert host == "cf" and fetched.startswith("2026-11-11 16:22:38.096")


def test_unparseable_file_is_quarantined_and_removed(tmp_path):
    w = FakeWorker({key("asks"): b"<html>not json</html>", key("bids"): feed("bids")})
    rep = import_raw(Store(tmp_path, in_memory=True), buffer(w), tmp_path / "q")
    assert (rep.imported, rep.bad) == (1, 1)
    assert len(list((tmp_path / "q").glob("*.json"))) == 1


def test_nothing_is_deleted_when_the_worker_fails_midway(tmp_path):
    w = FakeWorker({key("asks"): feed("asks")})
    real = w.__call__

    def flaky(request):
        return httpx.Response(500) if request.url.path == "/get" else real(request)
    with pytest.raises(SourceUnavailable):
        import_raw(Store(tmp_path, in_memory=True), buffer(flaky), tmp_path / "q")
    assert w.deleted == [] and w.objects


def test_wrong_token_is_an_error_without_leaking_it(tmp_path):
    bad = CfBuffer("https://w.test", "nope", httpx.Client(transport=httpx.MockTransport(FakeWorker({}))))
    with pytest.raises(SourceUnavailable) as e:
        list(bad.keys())
    assert "nope" not in str(e.value) and TOKEN not in str(e.value)


def test_same_snapshot_twice_is_one_file(tmp_path):
    store = Store(tmp_path, in_memory=True)
    for _ in range(2):
        import_raw(store, buffer(FakeWorker({key("asks"): feed("asks")})), tmp_path / "q")
    assert store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 1
    assert datetime.fromtimestamp(T, UTC)


def test_imports_waxpeer_and_skinport_raw_files(tmp_path):
    import polars as pl
    wax = json.dumps({"success": True, "items": [{"name": "AK", "count": 4, "min": 10500}]}).encode()
    sk = json.dumps([{"market_hash_name": "AK", "version": None, "min_price": 11.0, "median_price": 12.0,
                      "quantity": 7}, {"market_hash_name": "Gone", "min_price": None, "quantity": 0}]).encode()
    w = FakeWorker({key("waxpeer"): wax, key("skinport"): sk})
    rep = import_raw(Store(tmp_path, in_memory=True), buffer(w), tmp_path / "q")
    assert (rep.imported, rep.bad, rep.deleted) == (2, 0, 2) and not w.objects
    waxdf = pl.read_parquet(next((tmp_path / "collector" / "waxpeer").rglob("*.parquet")))
    skdf = pl.read_parquet(next((tmp_path / "collector" / "skinport").rglob("*.parquet")))
    assert waxdf["min_price"].to_list() == [10.5] and waxdf["host"].to_list() == ["cf"]
    assert skdf["market_hash_name"].to_list() == ["AK"] and skdf["tradable"].to_list() == [True]
