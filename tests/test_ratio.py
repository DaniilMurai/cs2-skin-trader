import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cs2trader.config import RatioSettings
from cs2trader.errors import DataCheckFailed
from cs2trader.market.ratio import measured_spread, steam_to_market_ratio
from cs2trader.models import AskRow, BidRow, Snapshot
from cs2trader.store import Store

S = RatioSettings(min_days=14, min_items=20)
D0 = date(2026, 11, 1)
# category -> (items, steam price, ask, bid)
CATS = {"case": (25, 10.0, 8.0, 7.0), "knife": (5, 100.0, 150.0, 130.0)}


def build(tmp_path, days: int) -> Store:
    store = Store(tmp_path)
    names = {}
    for cat, (n, *_rest) in CATS.items():
        for i in range(n):
            names[f"{cat} {i}"] = cat
    store.db.executemany("INSERT INTO items (name, category) VALUES (?, ?)", list(names.items()))
    for d in range(days):
        day = D0 + timedelta(d)
        ts = datetime(day.year, day.month, day.day, tzinfo=UTC)
        store.db.executemany(
            "INSERT INTO steam_points VALUES (?, ?, 'day', 0, 'USD', 5, 1, ?, ?)",
            [(name, ts, day, CATS[cat][1]) for name, cat in names.items()])
        feed = ts + timedelta(hours=12)
        asks = [AskRow(n, CATS[c][2], 5) for n, c in names.items()]
        bids = [BidRow(n, CATS[c][3], 5) for n, c in names.items()]
        store.write_snapshot(Snapshot("asks", feed, feed, "gha", asks))
        store.write_snapshot(Snapshot("bids", feed, feed, "gha", bids))
    store.open()  # views are created on open, so re-open after the Parquet files exist
    return store


def test_ratio_per_category_and_side(tmp_path):
    r = steam_to_market_ratio(build(tmp_path, 15), S)
    case_ask = r.filter((r["category"] == "case") & (r["side"] == "ask")).row(0, named=True)
    assert case_ask["median"] == pytest.approx(0.8)
    assert case_ask["n_items"] == 25 and not case_ask["fallback"]
    case_bid = r.filter((r["category"] == "case") & (r["side"] == "bid")).row(0, named=True)
    assert case_bid["median"] == pytest.approx(0.7)


def test_five_item_category_falls_back_and_says_so(tmp_path):
    r = steam_to_market_ratio(build(tmp_path, 15), S)
    knife = r.filter((r["category"] == "knife") & (r["side"] == "ask")).row(0, named=True)
    assert knife["fallback"] and knife["n_items"] == 5
    # all-items median over 25 items at 0.8 and 5 at 1.5 is the case value, not the knife's own 1.5
    assert knife["median"] == pytest.approx(0.8)


def test_spread_is_ask_minus_bid_over_mid(tmp_path):
    sp = measured_spread(build(tmp_path, 15), S)
    case = sp.filter(sp["category"] == "case").row(0, named=True)
    assert case["median"] == pytest.approx(1 / 7.5)
    assert sp.filter(sp["category"] == "knife")["fallback"][0]


def test_too_short_dataset_raises_clear_error(tmp_path):
    with pytest.raises(DataCheckFailed, match="at least 14 days"):
        steam_to_market_ratio(build(tmp_path, 5), S)


def test_store_round_trip(tmp_path):
    store = build(tmp_path, 15)
    r = steam_to_market_ratio(store, S)
    assert store.write_ratio(r) == r.height
    assert store.db.execute("SELECT count(*) FROM steam_to_market_ratio").fetchone()[0] == r.height


def test_cli_on_empty_data_dir_exits_cleanly(tmp_path):
    # data_dir defaults to ./data, so running in an empty tmp dir never touches real data
    p = subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", "ratio"],
                       cwd=tmp_path, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    assert p.returncode == 6 and "Traceback" not in p.stderr and "DataCheckFailed" in p.stderr
