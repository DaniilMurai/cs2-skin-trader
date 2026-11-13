import argparse
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cs2trader import cli
from cs2trader.arbitrage import ArbParams, hourly_edges, simulate, steam_bars
from cs2trader.backtest.engine import run
from cs2trader.config import load_settings
from cs2trader.errors import DataCheckFailed
from cs2trader.evaluate import make_spec
from cs2trader.models import AskRow, Snapshot
from cs2trader.store import Store

D0 = date(2026, 11, 1)
P = ArbParams(min_days=1)


def ts(day: date, hour: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def build(path, steam: dict, asks: dict) -> Store:
    """steam: name -> {day offset: (price, volume)}; asks: (day offset, hour) -> {name: ask}."""
    store = Store(path)
    store.db.executemany(
        "INSERT INTO steam_points VALUES (?, ?, 'day', 0, 'USD', ?, 1, ?, ?)",
        [(name, ts(D0 + timedelta(d)), vol, D0 + timedelta(d), price)
         for name, days in steam.items() for d, (price, vol) in days.items()])
    for (d, hour), prices in asks.items():
        feed = ts(D0 + timedelta(d), hour)
        store.write_snapshot(Snapshot("asks", feed, feed, "gha", [AskRow(n, a, 5) for n, a in prices.items()]))
    store.open()
    return store


def flat(price: float, volume: int, days: range = range(-5, 13)) -> dict:
    return {d: (price, volume) for d in days}


def result(store: Store, capital: float, p: ArbParams = P):
    bars = steam_bars(store, D0)
    return simulate(hourly_edges(store, p, bars), bars, capital, p)


def test_known_edge_and_volume_cap(tmp_path):
    # Steam 11.5 -> the seller keeps 10.0; ask 8.0 -> edge +25%, +2.00 per unit.
    # Volume 30 at a 10% share -> at most 3 units per day.
    store = build(tmp_path, {"Case": flat(11.5, 30)},
                  {(d, h): {"Case": 8.0} for d in (1, 2) for h in range(6)})
    edges = hourly_edges(store, P)
    assert edges.height == 12
    row = edges.row(0, named=True)
    assert row["edge"] == pytest.approx(0.25) and row["cap_units"] == 3 and row["ref_day"] == D0

    # $100 over an 8-day cycle = $12.50 a day: one unit at 8.0 fits, a second does not
    small = result(store, 100.0)
    assert small.resolved_days == 2
    assert small.spent_per_day == pytest.approx(8.0)
    assert small.profit_per_day == pytest.approx(2.0)

    # $1,000 = $125 a day: money is no limit, the volume cap is -> 3 units a day
    big = result(store, 1000.0)
    assert big.daily["units"].to_list() == [3, 3]
    assert big.profit_per_day == pytest.approx(6.0)
    assert big.lots["sale_day"].unique().sort().to_list() == [D0 + timedelta(9), D0 + timedelta(10)]
    assert big.over_cap_units == 0


def test_edge_gone_during_the_lock_is_a_loss(tmp_path):
    # Bought on day 1 at 8.0 on a +25% edge; by the sale day (day 9) Steam is 6.9 -> 6.0 kept, -2.00
    steam = {"Fading": {**flat(11.5, 30, range(-5, 3)), **flat(6.9, 30, range(3, 13))}}
    store = build(tmp_path, steam, {(1, 0): {"Fading": 8.0}})
    r = result(store, 100.0)
    assert r.lots["proceeds"].to_list() == pytest.approx([6.0])
    assert r.resolved_days == 1 and r.profit_per_day == pytest.approx(-2.0)


def test_decision_at_hour_h_ignores_later_data(tmp_path):
    cut = ts(D0 + timedelta(1), 3)
    asks = {(1, h): {"Case": 8.0, "Skin": 4.0} for h in range(4)}
    steam = {"Case": flat(11.5, 30, range(-5, 1)), "Skin": flat(5.75, 50, range(-5, 1))}
    a = build(tmp_path / "a", steam, asks)
    # Same history up to `cut`; afterwards the asks collapse and Steam, from the decision day on, jumps
    later_asks = {**asks, **{(1, h): {"Case": 1.0, "Skin": 1.0} for h in range(4, 8)}}
    later_steam = {n: {**days, **flat(100.0, 500, range(1, 13))} for n, days in steam.items()}
    b = build(tmp_path / "b", later_steam, later_asks)

    def upto(store):
        bars = steam_bars(store, D0)
        edges = hourly_edges(store, P, bars)
        lots = simulate(edges, bars, 1000.0, P).lots
        cols = ["feed_time", "market_hash_name", "cost", "edge"]
        return edges.filter(edges["feed_time"] <= cut), lots.filter(lots["feed_time"] <= cut).select(cols)

    (edges_a, lots_a), (edges_b, lots_b) = upto(a), upto(b)
    assert edges_a.height == 8 and edges_a.equals(edges_b)
    assert lots_a.height > 0 and lots_a.equals(lots_b)


def test_unresolved_days_are_not_counted(tmp_path):
    # Steam data ends on day 12: a buy on day 1 sells on day 9, a buy on day 6 would sell on day 14
    store = build(tmp_path, {"Case": flat(11.5, 30)}, {(1, 0): {"Case": 8.0}, (6, 0): {"Case": 8.0}})
    r = result(store, 100.0)
    assert r.lots["status"].to_list() == ["sold", "pending"]
    # days 1..4 have their sale day in the data (three of them with no buys); 5 and 6 do not
    assert r.resolved_days == 4 and r.profit_per_day == pytest.approx(0.5)


def test_too_short_dataset_raises_clear_error(tmp_path):
    store = build(tmp_path, {"Case": flat(11.5, 30)}, {(d, 0): {"Case": 8.0} for d in range(5)})
    with pytest.raises(DataCheckFailed, match="at least 14 days.*found 5"):
        hourly_edges(store, ArbParams())


def test_no_steam_bar_near_the_snapshots_raises_clear_error(tmp_path):
    store = build(tmp_path, {"Case": flat(11.5, 30, range(-30, -20))}, {(1, 0): {"Case": 8.0}})
    with pytest.raises(DataCheckFailed, match="backfill steam --refresh"):
        hourly_edges(store, P)


def test_cli_on_empty_data_dir_exits_cleanly(tmp_path):
    # data_dir defaults to ./data, so running in an empty tmp dir never touches real data
    p = subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", "arbitrage"],
                       cwd=tmp_path, capture_output=True, text=True, check=False,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    assert p.returncode == 6 and "Traceback" not in p.stderr
    assert "at least 14 days" in p.stderr


def test_cli_prints_assumptions_and_both_capitals(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    build(tmp_path / "data", {"Case": flat(11.5, 30, range(-5, 25))},
          {(d, 0): {"Case": 8.0} for d in range(1, 16)}).close()
    cli.cmd_arbitrage(argparse.Namespace(capital=None, min_days=14, min_edge=0.05, volume_share=0.10))
    out = capsys.readouterr().out
    assert "assumptions:" in out and "7-day trade lock" in out
    # one snapshot a day -> one unit a day at either capital, +2.00 each
    assert "capital $100 (budget $12.50/day): +2.00 wallet $/day over 15 resolved buy days" in out
    assert "capital $1,000 (budget $125.00/day): +2.00 wallet $/day" in out
    assert (tmp_path / "data" / "arbitrage" / "edges.parquet").exists()


def test_backtest_default_run_id_is_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    store = build(tmp_path / "data", {"Case": flat(11.5, 30, range(40))}, {})
    settings = load_settings()
    start, end = D0, D0 + timedelta(30)
    # The spec as `cs2 backtest` built it before it passed the store
    expected = run(make_spec("cash", {}, 100.0, start, end, settings), store, settings).run_id
    store.close()
    cli.cmd_backtest(argparse.Namespace(strategy="cash", params="{}", capital=100.0,
                                        date_from=start.isoformat(), date_to=end.isoformat()))
    assert f"run {expected} " in capsys.readouterr().out
