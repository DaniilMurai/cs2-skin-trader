import argparse
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cs2trader import cli
from cs2trader.arbitrage_cross import (
    MC,
    SKINPORT,
    WAXPEER,
    CrossParams,
    Venue,
    cross_edges,
    pair_table,
    simulate_cross,
)
from cs2trader.errors import DataCheckFailed
from cs2trader.models import AskRow, BidRow, Snapshot
from cs2trader.store import Store

D0 = date(2026, 11, 1)
# Round fees: 10% sale fee, nothing else. Money in flight: market.csgo 7 + 1 + 2 = 10 days,
# the listing venues 7 + 1 + 3 + 2 = 13 days.
FLAT = {MC: Venue(0.10, 0.0, 2, 0.0, sells_into_bids=True), WAXPEER: Venue(0.10, 0.0, 2, 0.0),
        SKINPORT: Venue(0.10, 0.0, 2, 0.0)}
P = CrossParams(venues=FLAT, undercut=0.0, min_days=1)


def ts(day: int, hour: int = 0) -> datetime:
    return datetime(D0.year, D0.month, D0.day, hour, tzinfo=UTC) + timedelta(days=day)


def build(path, quotes: dict) -> Store:
    """quotes: (day offset, hour) -> {venue: {name: ask, or (ask, bid) on market.csgo}}."""
    store = Store(path)
    for (d, hour), venues in quotes.items():
        at = ts(d, hour)
        for venue, prices in venues.items():
            if venue == MC:
                pairs = {n: v if isinstance(v, tuple) else (v, None) for n, v in prices.items()}
                store.write_snapshot(Snapshot("asks", at, at, "mac", [AskRow(n, a, 5) for n, (a, _) in pairs.items()]))
                bids = [BidRow(n, b, 5) for n, (_, b) in pairs.items() if b is not None]
                if bids:
                    store.write_snapshot(Snapshot("bids", at, at, "mac", bids))
            elif venue == WAXPEER:
                store.write_waxpeer(at, "mac", [{"market_hash_name": n, "min_price": a, "quantity": 5}
                                                for n, a in prices.items()])
            else:
                store.write_skinport(at, "mac", [
                    {"market_hash_name": n, "version": None, "tradable": True, "min_price": a,
                     "median_price": a, "quantity": 5} for n, a in prices.items()])
    store.open()
    return store


def result(store: Store, capital: float, p: CrossParams = P):
    _, candidates = cross_edges(store, p, capital)
    return simulate_cross(store, candidates, capital, p)


def mc_only(days: range, prices: dict) -> dict:
    return {(d, 0): {MC: prices} for d in days}


def test_known_edge_at_50_and_100(tmp_path):
    # Case: Waxpeer 4.00, market.csgo bid 5.00 -> 4.50 after the 10% fee: edge +12.5%, +0.50 a unit.
    # Skin: Waxpeer 5.00, bid 6.00 -> 5.40: edge +8%, +0.40 a unit.
    mc = {"Case": (6.0, 5.0), "Skin": (7.0, 6.0)}
    buy_hours = {(d, h): {WAXPEER: {"Case": 4.0, "Skin": 5.0}, MC: mc} for d in (1, 2) for h in range(3)}
    store = build(tmp_path, {**buy_hours, **mc_only(range(3, 15), mc)})
    summary, candidates = cross_edges(store, P, 100.0)
    assert candidates.height == 12 and set(candidates["venue_a"]) == {WAXPEER}
    assert candidates.row(0, named=True)["edge"] == pytest.approx(0.125)
    assert summary.filter(venue_a=WAXPEER, venue_b=MC)["hours"].to_list() == [3, 3]

    # $50: Case ties up 4.00 x 10 days = 40 of 50; Skin would add 5.00 x 10 = 50 -> does not fit
    small = result(store, 50.0)
    assert small.daily.filter("resolved")["units"].to_list() == [1, 1]
    assert small.spent_per_day == pytest.approx(4.0) and small.profit_per_day == pytest.approx(0.50)
    assert small.lots["sale_hour"].to_list() == [ts(8), ts(9)]

    # $100: 40 + 50 = 90 fits -> both items, once a day each
    big = result(store, 100.0)
    assert big.resolved_days == 2 and big.daily.filter("resolved")["units"].to_list() == [2, 2]
    assert big.profit_per_day == pytest.approx(0.90)
    table = pair_table(summary, [small, big]).filter(venue_a=WAXPEER, venue_b=MC).row(0, named=True)
    assert (table["profit_per_day_50"], table["profit_per_day_100"]) == pytest.approx((0.50, 0.90))


def test_edge_gone_during_the_lock_is_a_loss(tmp_path):
    # Bought at 4.00 on a +12.5% edge; when the lock ends the bid is 3.00 -> 2.70 back, -1.30
    quotes = {(1, 0): {WAXPEER: {"Fading": 4.0}, MC: {"Fading": (6.0, 5.0)}},
              **mc_only(range(2, 15), {"Fading": (4.0, 3.0)})}
    r = result(build(tmp_path, quotes), 50.0)
    assert r.lots["proceeds"].to_list() == pytest.approx([2.7])
    # Days 2..14 have market.csgo only: collector gaps, not buy days
    assert r.daily["buy_day"].to_list() == [D0 + timedelta(1)]
    assert r.resolved_days == 1 and r.profit_per_day == pytest.approx(-1.3)


def test_listing_sale_takes_the_lowest_price_of_the_wait_with_real_fees(tmp_path):
    # Default fees. Buy on market.csgo at 2.00 + 3.9% deposit = 2.078. Waxpeer shows 2.50:
    # 2.50 x 0.99 (undercut) x 0.94 (sale fee) x 0.98 (withdrawal) = 2.279970 -> edge +9.72%.
    # Listed on day 8; over the 3-day wait Waxpeer dips to 2.25 -> 2.25 x 0.911988 = 2.051973.
    lows = {8: 2.5, 9: 2.25, 10: 2.4, 11: 2.45}
    quotes = {(1, 0): {MC: {"Skin": 2.0}, WAXPEER: {"Skin": 2.5}},
              **{(d, 0): {WAXPEER: {"Skin": low}} for d, low in lows.items()}, **mc_only(range(12, 15), {"Skin": 9.0})}
    p = CrossParams(min_days=1)
    r = result(build(tmp_path, quotes), 50.0, p)
    lot = r.lots.row(0, named=True)
    assert lot["edge"] == pytest.approx(2.27997 / 2.078 - 1) and lot["cost"] == pytest.approx(2.078)
    assert lot["sale_hour"] == ts(11) and lot["proceeds"] == pytest.approx(2.051973)
    assert r.resolved_days == 1 and r.profit_per_day == pytest.approx(2.051973 - 2.078)
    # 19 days in flight (7 + 1 + 3 + 8): 2.078 x 19 = 39.48 fits $50, a 3.00 item would not
    assert p.cycle_days(WAXPEER) == 19 and p.cycle_days(MC) == 24


def test_venue_missing_in_an_hour_gives_no_trade(tmp_path):
    mc = {"Case": (6.0, 5.0)}
    quotes = {(1, 0): {MC: mc},                               # Waxpeer absent
              (1, 1): {WAXPEER: {"Case": 4.0}},               # market.csgo absent
              (1, 2): {WAXPEER: {"Case": 4.0}, MC: mc},       # both: the only tradable hour
              **mc_only(range(2, 15), mc)}
    store = build(tmp_path, quotes)
    _, candidates = cross_edges(store, P, 50.0)
    assert candidates["hour"].to_list() == [ts(1, 2)]
    assert simulate_cross(store, candidates, 50.0, P).lots["hour"].to_list() == [ts(1, 2)]


def test_no_quote_after_the_lock_is_unsold_and_variants_are_ignored(tmp_path):
    store = Store(tmp_path)
    # A Doppler phase and a trade-locked listing share the name with a plain item: never a quote
    store.write_skinport(ts(1), "mac", [
        {"market_hash_name": "Knife", "version": "Phase 1", "tradable": True, "min_price": 1.0,
         "median_price": 1.0, "quantity": 5},
        {"market_hash_name": "Knife", "version": None, "tradable": False, "min_price": 1.0,
         "median_price": 1.0, "quantity": 5}])
    store.close()
    quotes = {(1, 0): {WAXPEER: {"Gone": 4.0, "Knife": 9.0}, MC: {"Gone": (6.0, 5.0)}},
              **mc_only(range(2, 15), {"Other": (6.0, 5.0)})}
    store = build(tmp_path, quotes)
    _, candidates = cross_edges(store, P, 50.0)
    assert candidates["market_hash_name"].to_list() == ["Gone"]
    r = simulate_cross(store, candidates, 50.0, P)
    # market.csgo has no bid for it in the 24 h after the lock: the unit is written off
    assert r.lots["status"].to_list() == ["unsold"] and r.daily["profit"][0] == pytest.approx(-4.0)


def test_decision_at_hour_h_ignores_later_data(tmp_path):
    mc = {"Case": (6.0, 5.0), "Skin": (7.0, 6.0)}
    early = {(1, h): {WAXPEER: {"Case": 4.0, "Skin": 5.0}, MC: mc} for h in range(3)}
    a = build(tmp_path / "a", early)
    # Same history up to hour 2 of day 1; afterwards prices jump and the bids collapse
    wild = {"Case": (1.0, 0.5), "Skin": (1.0, 0.5)}
    later = {**{(1, h): {WAXPEER: {"Case": 0.5, "Skin": 0.6}, MC: wild} for h in range(3, 8)},
             **mc_only(range(2, 15), wild)}
    b = build(tmp_path / "b", {**early, **later})
    cut = ts(1, 2)

    def upto(store):
        _, candidates = cross_edges(store, P, 100.0)
        lots = simulate_cross(store, candidates, 100.0, P).lots
        cols = ["hour", "venue_a", "venue_b", "market_hash_name", "cost", "edge"]
        return candidates.filter(candidates["hour"] <= cut), lots.filter(lots["hour"] <= cut).select(cols)

    (cand_a, lots_a), (cand_b, lots_b) = upto(a), upto(b)
    assert cand_a.height == 6 and cand_a.equals(cand_b)
    assert lots_a.height == 2 and lots_a.equals(lots_b)


def test_too_short_dataset_raises_clear_error(tmp_path):
    quotes = {(d, 0): {WAXPEER: {"Case": 4.0}, MC: {"Case": (6.0, 5.0)}} for d in range(5)}
    quotes[(5, 0)] = {MC: {"Case": (6.0, 5.0)}}     # a day with one venue does not count
    with pytest.raises(DataCheckFailed, match=r"at least 14 days.*found 5 .*market.csgo 6, waxpeer 5, skinport 0"):
        cross_edges(build(tmp_path, quotes), CrossParams(), 100.0)


def test_cli_on_empty_data_dir_exits_cleanly(tmp_path):
    # data_dir defaults to ./data, so running in an empty tmp dir never touches real data
    p = subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", "arbitrage", "--mode", "cross"],
                       cwd=tmp_path, capture_output=True, text=True, check=False,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    assert p.returncode == 6 and "Traceback" not in p.stderr
    assert "at least 14 days with snapshots of two venues, found 0" in p.stderr


def test_cli_prints_assumptions_and_writes_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    mc = {"Case": (6.0, 5.2)}
    # Default fees: 4.00 x 1.039 = 4.156 on Waxpeer; bid 5.20 x 0.95 x 0.95 = 4.693 -> edge +12.9%
    build(tmp_path / "data", {**{(d, 0): {WAXPEER: {"Case": 4.0}, MC: mc} for d in range(1, 15)},
                              **mc_only(range(15, 28), mc)}).close()
    cli.cmd_arbitrage(argparse.Namespace(mode="cross", capital=None, min_days=14, min_edge=0.05, volume_share=0.10))
    out = capsys.readouterr().out
    assert "assumptions (cash-to-cash" in out and "waxpeer: deposit 3.9%" in out
    # 24 days in flight: 4.156 x 24 = 99.74 -> nothing at $50, one unit a day at $100
    assert "capital $50: +0.00 $/day over 14 resolved buy days" in out
    assert "capital $100: +0.54 $/day over 14 resolved buy days" in out
    files = {f.name for f in (tmp_path / "data" / "arbitrage").iterdir()}
    assert files == {"cross_edges.parquet", "cross_summary.parquet", "cross_lots_50.parquet",
                     "cross_lots_100.parquet", "cross_table.md"}
    assert "| waxpeer | market.csgo | 14 |" in (tmp_path / "data" / "arbitrage" / "cross_table.md").read_text()
