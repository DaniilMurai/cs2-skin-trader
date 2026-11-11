import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from cs2trader.backfill import backfill
from cs2trader.errors import SourceUnavailable, SteamAuthError
from cs2trader.models import FxRate, SteamPoint
from cs2trader.sources.steam import SteamClient, EmptyHistory
from cs2trader.store import Store

FX = FxRate(date(2026, 11, 9), "UAH", 41.5, "nbu", False)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "steam_pricehistory.json").read_text())


def points(name: str, n: int = 3) -> list[SteamPoint]:
    return [SteamPoint(name, datetime(2024, 1, d + 1, 1, tzinfo=UTC), 100.0 + d, "UAH", 5, "day")
            for d in range(n)]


class FakeSource:
    def __init__(self, fail: dict[str, BaseException] | None = None) -> None:
        self.fail = fail or {}
        self.calls: list[str] = []

    def price_history(self, name: str) -> list[SteamPoint]:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]
        return points(name)


def count(store: Store) -> int:
    return store.db.execute("SELECT count(*) FROM steam_points").fetchone()[0]


NAMES = [f"item {i:02d}" for i in range(20)]


def test_interrupted_run_resumes_to_same_row_count(tmp_path):
    full = Store(tmp_path / "full")
    backfill(full, FakeSource(), NAMES, FX, echo=lambda _: None)

    part = Store(tmp_path / "part")
    killer = FakeSource(fail={NAMES[10]: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        backfill(part, killer, NAMES, FX, echo=lambda _: None)
    resumed = FakeSource()
    rep = backfill(Store(tmp_path / "part"), resumed, NAMES, FX, echo=lambda _: None)

    assert resumed.calls == NAMES[10:]
    assert rep.skipped == 10 and rep.ok == 10
    assert count(Store(tmp_path / "part")) == count(full) == 60


def test_stops_after_errors_in_a_row(tmp_path):
    store = Store(tmp_path)
    src = FakeSource(fail={n: SourceUnavailable("steam", "HTTP 502") for n in NAMES[2:]})
    with pytest.raises(SourceUnavailable, match="in a row"):
        backfill(store, src, NAMES, FX, stop_after_errors=10, echo=lambda _: None)
    assert len(src.calls) == 12
    statuses = dict(store.db.execute("SELECT status, count(*) FROM steam_fetch_log GROUP BY 1").fetchall())
    assert statuses == {"ok": 2, "error": 10}


def test_isolated_errors_do_not_stop(tmp_path):
    src = FakeSource(fail={n: SourceUnavailable("steam", "x") for n in NAMES[::2]})
    rep = backfill(Store(tmp_path), src, NAMES, FX, stop_after_errors=2, echo=lambda _: None)
    assert rep.ok == 10 and rep.errors == 10


def test_auth_error_keeps_stored_items(tmp_path):
    store = Store(tmp_path)
    src = FakeSource(fail={NAMES[5]: SteamAuthError("steam", "expired")})
    with pytest.raises(SteamAuthError):
        backfill(store, src, NAMES, FX, echo=lambda _: None)
    assert store.fetched_names() == set(NAMES[:5])
    assert count(store) == 15


def test_unknown_item_is_empty_and_not_retried(tmp_path):
    store = Store(tmp_path)
    rep = backfill(store, FakeSource(fail={NAMES[0]: EmptyHistory(NAMES[0])}), NAMES[:3], FX,
                   echo=lambda _: None)
    assert rep.empty == 1 and rep.ok == 2
    assert NAMES[0] not in store.fetched_names()


def test_limit_and_refresh(tmp_path):
    store = Store(tmp_path)
    backfill(store, FakeSource(), NAMES, FX, limit=4, echo=lambda _: None)
    assert store.fetched_names() == set(NAMES[:4])
    again = FakeSource()
    backfill(store, again, NAMES[:4], FX, refresh=True, echo=lambda _: None)
    assert again.calls == NAMES[:4]


def client(responses: list[httpx.Response], log: list | None = None, sleeps: list | None = None) -> SteamClient:
    it = iter(responses)
    http = httpx.Client(transport=httpx.MockTransport(lambda req: next(it)))
    return SteamClient("cookie", interval_s=0, max_retries=3, backoff_s=60, client=http,
                       sleep=(sleeps.append if sleeps is not None else lambda s: None),
                       on_request=(lambda ts, st: log.append(st)) if log is not None else None)


def test_first_429_is_auth_error():
    with pytest.raises(SteamAuthError):
        client([httpx.Response(429, text="")]).price_history("x")


def test_429_after_success_backs_off_then_succeeds():
    sleeps, log = [], []
    c = client([httpx.Response(200, json=FIXTURE), httpx.Response(429), httpx.Response(429),
                httpx.Response(200, json=FIXTURE)], log=log, sleeps=sleeps)
    c.price_history("a")
    assert c.price_history("b")
    assert log == [200, 429, 429, 200]
    assert sleeps == [60, 120]


def test_5xx_exhausted_is_source_unavailable():
    with pytest.raises(SourceUnavailable):
        client([httpx.Response(502)] * 4).price_history("x")


def test_unknown_name_shape():
    body = {"success": False, "price_prefix": "", "price_suffix": "₴", "prices": False}
    with pytest.raises(EmptyHistory):
        client([httpx.Response(500, json=body)]).price_history("Nonexistent")


def test_empty_body_is_auth_error():
    with pytest.raises(SteamAuthError):
        client([httpx.Response(200, json=[])]).price_history("x")


def test_never_sold_item_is_empty_when_canary_has_prices():
    empty = {"success": True, "price_prefix": "", "price_suffix": "₴", "prices": []}
    with pytest.raises(EmptyHistory):
        client([httpx.Response(200, json=empty), httpx.Response(200, json=FIXTURE)]).price_history("x")


def test_empty_item_and_empty_canary_is_auth_error():
    empty = {"success": True, "price_prefix": "", "price_suffix": "₴", "prices": []}
    with pytest.raises(SteamAuthError):
        client([httpx.Response(200, json=empty)] * 2).price_history("x")


def test_merge_from_other_store(tmp_path):
    from cs2trader.backfill import merge_from

    other = Store(tmp_path / "other")
    backfill(other, FakeSource(), NAMES[:3], FX, echo=lambda _: None)
    other.db.close()
    main = Store(tmp_path / "main")
    backfill(main, FakeSource(), NAMES[2:4], FX, echo=lambda _: None)
    counts = merge_from(main, tmp_path / "other")
    assert counts == {"steam_points": 9, "steam_fetch_log": 3}
    assert main.fetched_names() == set(NAMES[:4])
    assert count(main) == 12


def test_rate_is_looked_up_per_item_so_a_run_can_cross_midnight(tmp_path):
    store = Store(tmp_path / "s")
    rates = iter([FX, FxRate(date(2026, 11, 10), "UAH", 45.0, "nbu", False)])
    backfill(store, FakeSource(), NAMES[:2], lambda: next(rates), echo=lambda _: None)
    days = store.db.execute("SELECT name, fx_day FROM steam_points GROUP BY ALL ORDER BY name").fetchall()
    assert days == [(NAMES[0], date(2026, 11, 9)), (NAMES[1], date(2026, 11, 10))]


def test_unparseable_body_is_quarantined(tmp_path):
    from cs2trader.errors import SchemaError
    body = {"success": True, "price_suffix": "₴", "prices": [["2026-11-09T13:00", 1.0, "1"]]}
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    c = SteamClient("cookie", interval_s=0, max_retries=3, client=http, quarantine_dir=tmp_path / "q")
    with pytest.raises(SchemaError):
        c.price_history("x")
    assert len(list((tmp_path / "q").glob("*.json"))) == 1
