import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cs2trader import cli
from cs2trader.collect import collect_once, run_collect, worst_error
from cs2trader.config import load_settings
from cs2trader.errors import ConfigError, SchemaError, SourceUnavailable
from cs2trader.sources.marketcsgo import MarketCsgoKeyed, MarketCsgoPublic
from cs2trader.store import Store

FIX = Path(__file__).parent / "fixtures"
ASKS = (FIX / "mc_asks.json").read_text()
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def public(tmp_path, bids_status=200) -> MarketCsgoPublic:
    def h(request: httpx.Request) -> httpx.Response:
        if "orders" in request.url.path:
            return httpx.Response(bids_status, text=(FIX / "mc_bids.json").read_text())
        return httpx.Response(200, text=ASKS)
    return MarketCsgoPublic("mac", tmp_path / "q", max_retries=0, backoff_s=0,
                            client=httpx.Client(transport=httpx.MockTransport(h)))


def keyed(tmp_path, handler) -> MarketCsgoKeyed:
    return MarketCsgoKeyed("K", 0, 20, tmp_path / "qk", max_retries=0, sleep=lambda s: None,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))


def sales_ok(requests):
    def h(request: httpx.Request) -> httpx.Response:
        names = request.url.params.get_list("list_hash_name[]")
        requests.append(names)
        data = {n: {"history": [[1794232665, 1.0]]} for n in names}
        return httpx.Response(200, json={"success": True, "currency": "RUB", "data": data})
    return h


def setup(tmp_path):
    settings = load_settings(tmp_path / "none.toml", data_dir=str(tmp_path / "data"))
    return settings, Store(settings.data_dir)


def test_bids_down_asks_still_written(tmp_path):
    settings, store = setup(tmp_path)
    report, failed, _ = run_collect(store, ["asks", "bids"], "mac", NOW, settings, public(tmp_path, 503))
    assert report.kinds_ok == ("asks",) and report.kinds_failed == ("bids",)
    assert len(report.files) == 1 and report.files[0].exists()
    assert isinstance(failed["bids"], SourceUnavailable)
    assert worst_error(list(failed.values())).exit_code == 4


def test_bad_key_keeps_asks_bids(tmp_path):
    settings, store = setup(tmp_path)
    k = keyed(tmp_path, lambda r: httpx.Response(401))
    report, failed, _ = run_collect(store, ["asks", "bids", "sales"], "mac", NOW, settings, public(tmp_path), k)
    assert report.kinds_ok == ("asks", "bids") and report.kinds_failed == ("sales",)
    assert len(report.files) == 2
    assert worst_error(list(failed.values())).exit_code == 3


def test_missing_key_is_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKETCSGO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    settings, store = setup(tmp_path)
    _, failed, _ = run_collect(store, ["asks", "sales"], "mac", NOW, settings, public(tmp_path))
    assert isinstance(failed["sales"], ConfigError) and failed["sales"].exit_code == 2


def test_sales_cap_respected_overdue_first(tmp_path):
    settings = load_settings(tmp_path / "none.toml", data_dir=str(tmp_path / "data"),
                             **{"mc.sales_max_names_per_run": 25})
    store = Store(settings.data_dir)
    collect_once(store, ["asks"], "mac", NOW, settings, public(tmp_path))
    n_names = store.db.sql("SELECT count(DISTINCT market_hash_name) FROM asks").fetchall()[0][0]
    assert n_names > 25
    requests: list[list[str]] = []
    report = collect_once(store, ["sales"], "mac", NOW, settings, keyed=keyed(tmp_path, sales_ok(requests)))
    assert report.kinds_ok == ("sales",)
    assert [len(b) for b in requests] == [20, 5]
    assert store.db.sql("SELECT count(DISTINCT market_hash_name) FROM sales").fetchall()[0][0] == 25


def test_worst_error_priority():
    errs = [SourceUnavailable("mc_public", "x"), SchemaError("mc_public", "y"), ConfigError("z")]
    assert worst_error(errs).exit_code == 5
    assert worst_error(errs[:1] + errs[2:]).exit_code == 2
    assert worst_error([]) is None


def test_cli_exit_code_and_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    real = cli.run_collect
    monkeypatch.setattr(cli, "run_collect", lambda store, kinds, host, now, settings: real(
        store, kinds, host, now, settings, public(tmp_path, 503)))
    monkeypatch.setattr(sys, "argv", ["cs2", "collect", "run", "--kinds", "asks,bids", "--host", "mac"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    out = capsys.readouterr().out
    assert exc.value.code == 4
    assert "asks: ok," in out and "bids: failed" in out


SKINPORT = [
    {"market_hash_name": "Chroma 2 Case", "version": None, "currency": "USD", "min_price": 0.8,
     "median_price": 0.9, "quantity": 120},
    {"market_hash_name": "Out Of Stock Knife", "version": None, "currency": "USD", "min_price": None,
     "median_price": None, "quantity": 0},
]


def skinport(tmp_path, status=200):
    from cs2trader.sources.skinport import SkinportPublic

    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=SKINPORT)
    return SkinportPublic(tmp_path / "q", max_retries=0, backoff_s=0,
                          client=httpx.Client(transport=httpx.MockTransport(h)))


def test_skinport_writes_both_views_and_skips_out_of_stock(tmp_path):
    import polars as pl

    settings, store = setup(tmp_path)
    report, failed, rows = run_collect(store, ["skinport"], "mac", NOW, settings, skinport=skinport(tmp_path))
    assert not failed and rows["skinport"] == 2
    df = pl.read_parquet(report.files[0])
    assert sorted(df["tradable"].to_list()) == [False, True]
    assert set(df["market_hash_name"]) == {"Chroma 2 Case"}
    assert df["min_price"].to_list() == [0.8, 0.8]


def test_skinport_unavailable_is_reported_not_raised(tmp_path):
    settings, store = setup(tmp_path)
    _, failed, _ = run_collect(store, ["skinport"], "mac", NOW, settings, skinport=skinport(tmp_path, 503))
    assert isinstance(failed["skinport"], SourceUnavailable)


def waxpeer(tmp_path, status=200):
    from cs2trader.sources.waxpeer import WaxpeerPublic
    body = {"success": True, "items": [{"name": "Chroma 2 Case", "count": 50, "min": 812},
                                        {"name": "Odd Item", "count": 0, "min": None}]}
    return WaxpeerPublic(tmp_path / "q", max_retries=0, backoff_s=0,
                         client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))))


def test_waxpeer_converts_thousandths_and_skips_missing_price(tmp_path):
    import polars as pl

    settings, store = setup(tmp_path)
    report, failed, rows = run_collect(store, ["waxpeer"], "mac", NOW, settings, waxpeer=waxpeer(tmp_path))
    assert not failed and rows["waxpeer"] == 1
    df = pl.read_parquet(report.files[0])
    assert df["min_price"].to_list() == [0.812] and df["quantity"].to_list() == [50]


def test_waxpeer_unavailable_is_reported_not_raised(tmp_path):
    settings, store = setup(tmp_path)
    _, failed, _ = run_collect(store, ["waxpeer"], "mac", NOW, settings, waxpeer=waxpeer(tmp_path, 503))
    assert isinstance(failed["waxpeer"], SourceUnavailable)
