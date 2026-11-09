import math
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.momentum import XS_GRID, XsMomentum

D0 = date(2024, 1, 1)
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)
WARM = 60
N = 8


def bars(days: int = 120, flip_at: int | None = None) -> pl.DataFrame:
    """Item i drifts at (i - 3.5) * 0.001 per day; after `flip_at` the drift order is reversed."""
    rows = []
    for i in range(N):
        price = 5.0 + i
        for d in range(-WARM, days):
            sign = -1 if flip_at is not None and d >= flip_at else 1
            price *= math.exp(sign * (i - 3.5) * 0.001)
            rows.append({"market_hash_name": f"item {i}", "t": D0 + timedelta(d), "price": price,
                         "volume": 200, "ask": None, "bid": None, "category": "knife"})
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
def xs(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "xs_momentum",
                        registry.StrategyEntry("xs_momentum", "momentum", XsMomentum, XS_GRID, ""))


def go(b, params, capital=1000.0, days=59):
    return run(RunSpec("xs_momentum", params, capital, D0, D0 + timedelta(days), fills=FILLS), FakeStore(b))


def fills(res):
    return res.trades.filter(pl.col("qty_filled") > 0)


def test_picks_known_top_k():
    res = go(bars(), {"k": 3, "lookback": 30, "rebalance": 14})
    bought = set(fills(res).filter(pl.col("side") == "buy")["market_hash_name"])
    assert bought == {"item 5", "item 6", "item 7"}


def test_rotates_and_never_sells_before_unlock():
    res = go(bars(flip_at=10), {"k": 2, "lookback": 14, "rebalance": 7}, days=80)
    f = fills(res)
    sells = f.filter(pl.col("side") == "sell")
    assert sells.height > 0
    buys = f.filter(pl.col("side") == "buy")
    # FIFO per item: the n-th unit sold comes from the earliest buys, so every sell date must be
    # at least 7 days after the earliest buy date of units not yet sold.
    for name in sells["market_hash_name"].unique():
        b = buys.filter(pl.col("market_hash_name") == name).sort("t")
        s = sells.filter(pl.col("market_hash_name") == name).sort("t")
        lots = [[row["t"], row["qty_filled"]] for row in b.iter_rows(named=True)]
        for row in s.iter_rows(named=True):
            left = row["qty_filled"]
            while left:
                lot = next(l for l in lots if l[1] > 0 and l[0] + timedelta(7) <= row["t"])
                take = min(left, lot[1])
                lot[1] -= take
                left -= take


@pytest.mark.parametrize("capital", [100.0, 1000.0])
def test_runs_at_both_capitals(capital):
    res = go(bars(), {}, capital=capital)
    assert res.equity.height > 0
    assert fills(res).height > 0
