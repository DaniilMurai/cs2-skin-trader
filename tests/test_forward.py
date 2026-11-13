import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import polars as pl
import pytest
from test_lookahead import D0 as DAY0
from test_lookahead import synthetic_bars
from test_report import NAMES, SETTINGS
from test_walk_forward import Store as FakeStore
from test_walk_forward import Toy

from cs2trader.backtest.engine import run
from cs2trader.config import Settings, load_settings
from cs2trader.errors import DataCheckFailed, LookAheadError
from cs2trader.evaluate import make_spec
from cs2trader.forward import ROW_SCHEMA, forward, save_rows, summary_row
from cs2trader.models import AskRow, BidRow, Order, Snapshot
from cs2trader.report import build_report, forward_section, results_hash
from cs2trader.store import Store
from cs2trader.strategies import registry

T0 = datetime(2026, 11, 2, tzinfo=UTC)


def hour(h: float) -> datetime:
    return T0 + timedelta(hours=h)


def build(path, quotes: dict) -> Store:
    """quotes: feed hour (may be fractional) -> {name: (ask, bid)}; None leaves that side out."""
    store = Store(path)
    for h, rows in quotes.items():
        feed = hour(h)
        asks = [AskRow(n, a, 5) for n, (a, _) in rows.items() if a is not None]
        bids = [BidRow(n, b, 5) for n, (_, b) in rows.items() if b is not None]
        store.write_snapshot(Snapshot("asks", feed, feed, "gha", asks))
        store.write_snapshot(Snapshot("bids", feed, feed, "gha", bids))
    return store


class Scripted:
    """Orders by decision hour (offset from T0); logs what every decision could see."""

    name = "scripted"
    script: ClassVar[dict[int, list[Order]]] = {}
    log: ClassVar[list[tuple]] = []

    def __init__(self, params) -> None:
        self.params = params

    def decide(self, market, book):
        hist = market.history(30)
        h = round((market.now - T0).total_seconds() / 3600)
        type(self).log.append((h, hist["t"].max(), hist.filter(pl.col("t") == market.now).sort("market_hash_name")["ask"].to_list(),
                               market.universe(), market.regime(), market.index("all", 14).height))
        return self.script.get(h, [])


@pytest.fixture(autouse=True)
def strategies(monkeypatch):
    monkeypatch.setattr(Scripted, "script", {})
    monkeypatch.setattr(Scripted, "log", [])
    for name, cls in (("scripted", Scripted), ("toy", Toy)):
        monkeypatch.setitem(registry.REGISTRY, name, registry.StrategyEntry(
            name, "momentum", cls, {"lookback": [3, 10]}, "test"))


def paper(store: Store, start: float, end: float, capital: float = 100.0):
    return forward(registry.REGISTRY["scripted"], {}, capital, store, hour(start), hour(end), Settings(), min_days=1)


def test_bar_h_holds_only_feeds_up_to_h(tmp_path):
    # 1.0 is exactly on the hour; 1.1 (6 minutes later) is first known at hour 2
    store = build(tmp_path, {1.0: {"AK": (10.0, 9.0)}, 1.1: {"AK": (11.0, 9.5)}, 1.9: {"AK": (12.0, None)}})
    bars = store.hourly_bars(hour(0), hour(5))
    assert bars.select("t", "price", "volume", "ask", "bid").rows() == [
        (hour(1), 10.0, 0, 10.0, 9.0),
        # the newest snapshot of (1, 2] wins per side: the 1.9 feed has an ask and no bid
        (hour(2), 12.0, 0, 12.0, 9.5),
    ]
    assert store.hourly_bars(hour(0), hour(1)).height == 1


def test_volume_counts_only_sales_already_fetched_at_h(tmp_path):
    store = build(tmp_path, {h: {"AK": (10.0, 9.0)} for h in (1, 2)})
    sale = {"host": ["mac"] * 3, "market_hash_name": ["AK"] * 3, "price_raw": [900.0, 901.0, 902.0],
            "currency": ["RUB"] * 3, "ts": [hour(0.5), hour(0.6), hour(1.5)],
            # the second sale of hour 1 was polled a day later: a decision at hour 1 could not know it
            "fetched_at": [hour(0.9), hour(25), hour(1.8)]}
    day = tmp_path / "collector" / "sales" / "day=2026-11-02"
    day.mkdir(parents=True)
    pl.DataFrame(sale, schema_overrides={"ts": pl.Datetime("us", "UTC"),
                                         "fetched_at": pl.Datetime("us", "UTC")}).write_parquet(day / "x-mac.parquet")
    store.open()
    assert store.hourly_bars(hour(0), hour(3))["volume"].to_list() == [1, 1]


def test_decision_at_h_never_sees_a_later_snapshot(tmp_path):
    quotes = {h + 0.1: {"AK": (10.0 + h, 9.0)} for h in range(6)}
    full = build(tmp_path / "full", quotes)
    paper(full, 0, 8)
    seen = list(Scripted.log)
    # the feed at h + 0.1 is in bar h + 1: the decision at hour h sees the ask of the feed at h - 0.9
    assert [(h, t, asks) for h, t, asks, *_ in seen] == [(h, hour(h), [9.0 + h]) for h in range(1, 7)]

    # the same decisions when the later snapshots do not exist yet
    Scripted.log.clear()
    paper(build(tmp_path / "cut", {h: q for h, q in quotes.items() if h < 3}), 0, 8)
    assert Scripted.log == seen[:3]

    with pytest.raises(LookAheadError):
        from cs2trader.forward import HourlyData
        HourlyData(full.hourly_bars(hour(0), hour(8)), None, "").view(hour(2)).at(hour(3))


def test_buys_fill_at_the_ask_and_sells_at_the_bid(tmp_path):
    q = {"AK": (10.0, 9.0)}
    store = build(tmp_path, {1: q, 2: q, 3: q, 100: {"AK": (13.0, 11.0)}, 170: {"AK": (14.0, 12.0)},
                             171: {"AK": (15.0, 12.5), "M4": (None, 3.0)}})
    Scripted.script = {1: [Order("AK", "buy", 2)], 3: [Order("AK", "sell", 1)], 100: [Order("AK", "sell", 1)],
                       170: [Order("AK", "sell", 1), Order("M4", "buy", 1)]}
    res = paper(store, 0, 171)
    assert res.spec.freq == "1h" and res.spec.fills.spread_penalty == 0.0
    fills = res.trades.select("t", "market_hash_name", "side", "qty_filled", "price", pl.col("fee").round(9), "reason").rows()
    assert fills == [
        # only the best ask is known: one unit at 10.00, the second is cancelled
        (hour(2), "AK", "buy", 1, 10.0, 0.0, "volume_cap"),
        # bought at hour 2, locked for 7 days = until hour 170
        (hour(100), "AK", "sell", 0, 11.0, 0.0, "locked"),
        (hour(170), "AK", "sell", 1, 12.0, 0.6, "ok"),
        (hour(171), "AK", "sell", 0, 12.5, 0.0, "no_position"),
        # no ask in the bar: nothing to buy from
        (hour(171), "M4", "buy", 0, 0.0, 0.0, "no_data"),
    ]
    eq = {r["t"]: r for r in res.equity.iter_rows(named=True)}
    assert eq[hour(1)]["balance"] == 100.0
    # one unit held, valued at the bid less the 5% seller fee: 90 + 9 * 0.95
    assert eq[hour(3)]["balance"] == 90.0 and eq[hour(3)]["holdings_value"] == pytest.approx(8.55)
    # sold at 12.00 less 5%: 90 + 11.40; cash-out takes another 5%, the Steam wallet 2%
    last = eq[hour(171)]
    assert last["balance"] == pytest.approx(101.4) and last["holdings_value"] == 0.0
    assert last["equity_cash"] == pytest.approx(96.33) and last["equity_steam"] == pytest.approx(99.372)
    assert last["fees_paid"] == pytest.approx(0.6)
    assert res.cashout_available == hour(170) + timedelta(days=8)
    # hours without a snapshot are not steps
    assert res.equity.height == 6


def test_steam_context_stops_before_the_decision_day(tmp_path):
    store = build(tmp_path, {h: {"AK": (10.0, 9.0)} for h in (1, 30)})
    day = T0.date()
    # 30 days of history (universe.min_history_days) are complete on T0's day, not on the day before
    store.db.executemany(
        "INSERT INTO steam_points VALUES ('AK', ?, 'day', 0, 'USD', 50, 1, ?, 10.0)",
        [(T0 + timedelta(days=d), day + timedelta(days=d)) for d in range(-30, 2)])
    paper(store, 0, 30)
    # hour 1 may use Steam bars up to the day before T0; T0's own bar is still forming then
    assert [(h, uni) for h, _, _, uni, *_ in Scripted.log] == [(1, []), (30, ["AK"])]


def test_run_id_follows_the_hours_and_the_snapshots(tmp_path):
    store = build(tmp_path, {h: {"AK": (10.0, 9.0)} for h in (1, 2)})
    a, b = paper(store, 0, 2), paper(store, 0, 2)
    assert a.run_id == b.run_id != paper(store, 1, 2).run_id
    store.write_snapshot(Snapshot("asks", hour(3), hour(3), "gha", [AskRow("AK", 10.0, 5)]))
    assert paper(store, 0, 2).run_id != a.run_id


def test_too_short_dataset_raises_clear_error(tmp_path):
    store = build(tmp_path, {24 * d: {"AK": (10.0, 9.0)} for d in range(5)})
    with pytest.raises(DataCheckFailed, match="at least 28 days.*found 5"):
        forward(registry.REGISTRY["cash"], {}, 100.0, store, hour(0), hour(200), Settings())


def cs2(tmp_path, *args):
    return subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", *args],
                          cwd=tmp_path, capture_output=True, text=True, check=False,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})


def test_cli_on_empty_or_short_data_exits_cleanly(tmp_path):
    # data_dir defaults to ./data, so running in a tmp dir never touches real data
    p = cs2(tmp_path, "forward", "--strategy", "cash")
    assert p.returncode == 6 and "Traceback" not in p.stderr
    assert "at least 28 days" in p.stderr and "found 0" in p.stderr

    build(tmp_path / "data", {24 * d: {"AK": (10.0, 9.0)} for d in range(5)}).close()
    p = cs2(tmp_path, "forward", "--strategy", "cash")
    assert p.returncode == 6 and "Traceback" not in p.stderr
    assert "at least 28 days" in p.stderr and "found 5" in p.stderr


def test_cli_writes_the_run_and_the_report_row(tmp_path):
    build(tmp_path / "data", {24 * d: {"AK": (10.0, 9.0)} for d in range(3)}).close()
    p = cs2(tmp_path, "forward", "--strategy", "cash", "--capital", "100", "--min-days", "3")
    assert p.returncode == 0, p.stderr
    assert "forward " in p.stdout and "choices:" in p.stdout and "168 hours" in p.stdout
    # no Steam history in this fixture: the same-dates backtest is skipped, not fatal
    assert "backtest for the same dates skipped" in p.stdout
    rows = pl.read_parquet(tmp_path / "data" / "forward" / "results.parquet")
    assert rows.select("strategy", "capital", "n_trades", "bt_run_id").rows() == [("cash", 100.0, 0, None)]
    assert rows["net_return_cash"][0] == pytest.approx(-0.05)
    run_dir = tmp_path / "data" / "runs" / rows["run_id"][0]
    assert (run_dir / "equity.parquet").exists() and '"freq": "1h"' in (run_dir / "spec.json").read_text()


def test_report_renders_the_forward_column_and_keeps_the_hash(tmp_path):
    bars = synthetic_bars(7, 200)
    plain = build_report(FakeStore(bars), tmp_path / "a", SETTINGS, NAMES)
    assert "Forward paper test" not in plain.read_text()

    row = {"strategy": "toy", "capital": 100.0, "start": "2026-11-02T00:00:00+00:00",
           "end": "2026-11-30T00:00:00+00:00", "params_json": '{"lookback": 3}', "run_id": "f" * 16,
           "net_return_cash": -0.0712, "net_return_steam": -0.04, "max_drawdown_cash": 0.031, "n_trades": 9,
           "fees_paid": 1.25, "bt_run_id": "b" * 16, "bt_net_return_cash": 0.0234, "bt_net_return_steam": 0.05,
           "bt_max_drawdown_cash": 0.02, "bt_n_trades": 4}
    # a later run of the same strategy and capital replaces the row; one without a backtest shows "-"
    save_rows(tmp_path / "data" / "forward" / "results.parquet", [{**row, "n_trades": 1}])
    save_rows(tmp_path / "data" / "forward" / "results.parquet",
              [row, {k: v for k, v in row.items() if not k.startswith("bt_")} | {"capital": 1000.0}])
    with_fwd = build_report(FakeStore(bars), tmp_path / "b",
                            Settings(wf=SETTINGS.wf, capitals=SETTINGS.capitals, data_dir=tmp_path / "data"), NAMES)
    page = with_fwd.read_text()
    assert "<th>backtest</th><th>forward</th>" in page
    assert ("<td>toy</td><td>$100</td><td>2026-11-02T00h .. 2026-11-30T00h</td><td>{&quot;lookback&quot;: 3}</td>"
            "<td>+2.34%</td><td>-7.12%</td><td>2.00%</td><td>3.10%</td><td>4</td><td>9</td><td>1.25</td>") in page
    assert "<td>toy</td><td>$1,000</td>" in page and "<td>-</td><td>-7.12%</td>" in page
    assert "168 hours" in page

    hashes = [results_hash(pl.read_parquet(p.parent / "results.parquet")) for p in (plain, with_fwd)]
    assert hashes[0] == hashes[1] and hashes[0] in page
    assert forward_section(None) == "" and forward_section(pl.DataFrame(schema=ROW_SCHEMA)) == ""


def test_daily_run_ids_do_not_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bars = synthetic_bars(7, 60)
    res = run(make_spec("toy", {"lookback": 3}, 100.0, DAY0, DAY0 + timedelta(40), load_settings()),
              FakeStore(bars), load_settings())
    # pinned on main before the hourly path was added to the engine
    assert (res.run_id, res.trades.height, round(res.equity["equity_cash"][-1], 6)) == PINNED


def test_summary_row_has_every_column(tmp_path):
    store = build(tmp_path, {h: {"AK": (10.0, 9.0)} for h in (1, 2)})
    row = summary_row(paper(store, 0, 2), None, hour(0), hour(2))
    assert set(row) <= set(ROW_SCHEMA) and row["net_return_cash"] == pytest.approx(-0.05)


PINNED = ("83476ef1a63a68f2", 19, 63.182566)
