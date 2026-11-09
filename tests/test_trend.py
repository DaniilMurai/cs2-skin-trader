from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.backtest.view import MarketData
from cs2trader.config import RegimesSettings, UniverseSettings
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.trend import TrendFollow

D0 = date(2024, 1, 1)
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)
WARM = 60
DAYS = 100
N_ITEMS = 12  # the all-market index needs at least 10 contributors


def series(points: dict[int, float]) -> list[float]:
    # Piecewise-linear price by day offset from D0 (day -WARM .. DAYS-1).
    keys = sorted(points)
    out = []
    for d in range(-WARM, DAYS):
        lo = max([k for k in keys if k <= d], default=keys[0])
        hi = min([k for k in keys if k >= d], default=keys[-1])
        out.append(points[lo] if lo == hi else points[lo] + (points[hi] - points[lo]) * (d - lo) / (hi - lo))
    return out


def bars(items: dict[str, tuple[int, list[float]]]) -> pl.DataFrame:
    rows = []
    for name, (vol, prices) in items.items():
        for i, price in enumerate(prices):
            rows.append({"market_hash_name": name, "t": D0 + timedelta(i - WARM), "price": price,
                         "volume": vol, "ask": None, "bid": None, "category": "knife"})
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
def trend(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "trend_ma",
                        registry.StrategyEntry("trend_ma", "trend", TrendFollow, {}, ""))


def fills(res):
    return res.trades.filter(pl.col("qty_filled") > 0)


def go(b, params, capital=1000.0, days=DAYS - 1):
    return run(RunSpec("trend_ma", params, capital, D0, D0 + timedelta(days), fills=FILLS), FakeStore(b))


def flock(points: dict[int, float]) -> dict[str, tuple[int, list[float]]]:
    # Same path for every item (scaled), so the index follows it; volume falls with the item number.
    return {f"I{i:02d}": (100 * (N_ITEMS - i), [p * (1 + 0.1 * i) for p in series(points)]) for i in range(N_ITEMS)}


def test_invested_in_most_liquid_items_when_above_ma():
    res = go(bars(flock({-WARM: 10.0, DAYS: 40.0})), {"ma_days": 20, "n_items": 2}, days=20)
    assert set(fills(res).filter(pl.col("side") == "buy")["market_hash_name"]) == {"I00", "I01"}
    assert fills(res).filter(pl.col("side") == "sell").height == 0


def test_crossing_below_ma_sells_next_bar():
    # Rises to day 30, then falls; the index drops below its 20-day MA a few days into the fall.
    b = bars(flock({-WARM: 10.0, 30: 40.0, DAYS: 15.0}))
    res = go(b, {"ma_days": 20, "n_items": 3})
    market = MarketData(b, FakeStore(b).items_frame(), UniverseSettings(), RegimesSettings())
    idx = market.index["all"].filter(pl.col("t") >= D0).sort("t")
    ma = idx["level"].rolling_mean(20)  # contiguous calendar days, so 20 rows = 20 days
    crossed = idx.with_columns(ma.alias("ma")).filter(
        (pl.col("t") > D0 + timedelta(30)) & (pl.col("level") <= pl.col("ma")))["t"].min()
    f = fills(res)
    buys, sells = f.filter(pl.col("side") == "buy"), f.filter(pl.col("side") == "sell")
    assert buys.height and sells.height
    assert sells["t"].min() == crossed + timedelta(1)
    assert buys["t"].max() < crossed
    assert res.equity["holdings_value"][-1] == 0


def test_locked_lots_are_sold_on_unlock_day():
    # The index tops out and breaks below its MA two bars after the first purchases fill, so the lots
    # are still locked at the signal; they must go on the bar they unlock (bought + 7), not earlier.
    b = bars(flock({-WARM: 10.0, 0: 10.0, 3: 12.0, 4: 6.0, DAYS: 6.0}))
    res = go(b, {"ma_days": 20, "n_items": 1}, days=40)
    f = fills(res)
    buys, sells = f.filter(pl.col("side") == "buy"), f.filter(pl.col("side") == "sell")
    assert buys.height and sells.height
    assert sells["t"].min() == buys["t"].min() + timedelta(days=7)
    assert res.equity["holdings_value"][-1] == 0


@pytest.mark.parametrize("capital", [100.0, 1000.0])
def test_runs_at_both_capitals(capital):
    res = go(bars(flock({-WARM: 10.0, 30: 40.0, 60: 15.0, DAYS: 15.0})),
             {"ma_days": 20, "n_items": 5}, capital=capital)
    assert res.equity.height > 0
    assert fills(res).filter(pl.col("side") == "buy").height > 0
