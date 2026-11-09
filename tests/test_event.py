import random
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.event import EventStrategy

START, END = date(2025, 4, 1), date(2025, 12, 31)
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)
ITEMS = {
    "Austin 2025 Legends Sticker Capsule": "capsule",
    "Austin 2025 Challengers Sticker Capsule": "capsule",
    "Copenhagen 2024 Legends Sticker Capsule": "capsule",
    "Fracture Case": "case",
    "Recoil Case": "case",
    **{f"AK-47 | Filler {i}": "weapon_skin" for i in range(10)},
}


def bars(seed: int = 0) -> pl.DataFrame:
    rng = random.Random(seed)
    rows = []
    for name, cat in ITEMS.items():
        price = rng.uniform(0.3, 3.0)
        d = START - timedelta(120)
        while d <= END:
            price *= 1 + rng.gauss(0, 0.01)
            rows.append({"market_hash_name": name, "t": d, "price": price, "volume": 500,
                         "ask": None, "bid": None, "category": cat})
            d += timedelta(1)
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


class FakeStore:
    def __init__(self, b: pl.DataFrame) -> None:
        self.b = b

    def daily_bars(self, start, end):
        return self.b.filter(pl.col("t").is_between(start, end))

    def items_frame(self):
        return self.b.select(pl.col("market_hash_name").alias("name"), "category").unique()

    def data_version(self):
        return "fake"


@pytest.fixture(autouse=True)
def event(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "event", registry.StrategyEntry("event", "event", EventStrategy, {}, ""))


def filled(res):
    return res.trades.filter(pl.col("qty_filled") > 0)


def test_majors_window():
    res = run(RunSpec("event", {"rule": "majors", "pre": 14, "post": 14}, 1000.0, START, END, fills=FILLS),
              FakeStore(bars()))
    f = filled(res)
    buys, sells = f.filter(pl.col("side") == "buy"), f.filter(pl.col("side") == "sell")
    assert set(buys["market_hash_name"]) == {"Austin 2025 Legends Sticker Capsule",
                                             "Austin 2025 Challengers Sticker Capsule"}
    assert buys["t"].min() == date(2025, 6, 3) - timedelta(14) + timedelta(1)  # decided at window open
    # Window closes on end + 14 = 2025-07-06: decided at that close, filled the next bar.
    assert sells["t"].min() == date(2025, 7, 7)
    assert res.equity.row(-1, named=True)["holdings_value"] == 0


def test_case_pool_exit():
    res = run(RunSpec("event", {"rule": "cases", "hold": 30}, 1000.0, START, END, fills=FILLS), FakeStore(bars()))
    f = filled(res)
    buys = f.filter(pl.col("side") == "buy")
    assert buys["market_hash_name"].to_list() == ["Fracture Case"]
    assert buys["t"].item() == date(2025, 9, 18)  # exit announced 2025-09-17
    sell = f.filter(pl.col("side") == "sell")
    assert sell["t"].item() == date(2025, 9, 17) + timedelta(30) + timedelta(1)


@pytest.mark.parametrize("rule,cut", [("majors", date(2025, 6, 10)), ("cases", date(2025, 9, 30))])
def test_no_look_ahead(rule, cut):
    b = bars(1)
    full = run(RunSpec("event", {"rule": rule}, 100.0, START, END, fills=FILLS), FakeStore(b))
    part = run(RunSpec("event", {"rule": rule}, 100.0, START, cut, fills=FILLS),
               FakeStore(b.filter(pl.col("t") <= cut)))
    assert part.trades.equals(full.trades.filter(pl.col("t") <= cut))


@pytest.mark.parametrize("capital", [100.0, 1000.0])
def test_both_capitals(capital):
    for rule in ("majors", "cases"):
        run(RunSpec("event", {"rule": rule}, capital, START, END, fills=FILLS), FakeStore(bars()))
