import math
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.video import CategoryMomentum

D0 = date(2024, 1, 1)
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)
WARM = 60


def bars(daily_drift: dict[str, float], days: int = 120, n: int = 6, start_rise: int = 0) -> pl.DataFrame:
    rows = []
    for cat, drift in daily_drift.items():
        for i in range(n):
            price = 5.0 + i
            for d in range(-WARM, days):
                if d >= start_rise:
                    # item i moves slightly faster than item i-1, so within-category ranks are fixed
                    price *= math.exp(drift * (1 + 0.05 * i))
                rows.append({"market_hash_name": f"{cat} {i}", "t": D0 + timedelta(d), "price": price,
                             "volume": 200, "ask": None, "bid": None, "category": cat})
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
def v2(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "v2_category_momentum",
                        registry.StrategyEntry("v2_category_momentum", "video", CategoryMomentum, {}, ""))


def fills(res):
    return res.trades.filter(pl.col("qty_filled") > 0)


def test_holds_only_top_growing_category_and_its_best_items():
    b = bars({"knife": 0.004, "case": 0.0, "sticker": -0.003})
    res = run(RunSpec("v2_category_momentum", {"top_k": 1, "items_per_cat": 3, "window": 30},
                      1000.0, D0, D0 + timedelta(59), fills=FILLS), FakeStore(b))
    bought = set(fills(res).filter(pl.col("side") == "buy")["market_hash_name"])
    assert bought == {"knife 3", "knife 4", "knife 5"}


def test_exposure_scales_with_growth():
    def invested_share(drift: float) -> float:
        b = bars({"knife": drift, "case": 0.0, "sticker": -0.003}, start_rise=-WARM)
        res = run(RunSpec("v2_category_momentum", {"top_k": 1, "full_growth": 0.20, "window": 30},
                          1000.0, D0, D0 + timedelta(10), fills=FILLS), FakeStore(b))
        row = res.equity.row(-1, named=True)
        return row["holdings_value"] / (row["holdings_value"] + row["balance"])

    weak, strong = invested_share(0.0017), invested_share(0.008)  # ≈ 5–6% vs ≈ 27% in 30 days
    assert 0.15 < weak < 0.45
    assert strong > 0.9


def test_adds_exposure_while_growth_persists():
    # The rise starts on day 0: a 30-day window sees more of it at every weekly rebalance.
    b = bars({"knife": 0.002, "case": 0.0, "sticker": -0.003}, start_rise=0)
    res = run(RunSpec("v2_category_momentum", {"top_k": 1, "full_growth": 0.10, "window": 30,
                                               "rebalance_days": 7},
                      1000.0, D0, D0 + timedelta(40), fills=FILLS), FakeStore(b))
    eq = res.equity.with_columns((pl.col("holdings_value") / (pl.col("holdings_value") + pl.col("balance")))
                                 .alias("share"))
    week1 = eq.filter(pl.col("t") == D0 + timedelta(2))["share"].item()
    week4 = eq.filter(pl.col("t") == D0 + timedelta(30))["share"].item()
    assert week4 > week1 + 0.2


def test_falling_market_stays_in_cash():
    b = bars({"knife": -0.004, "case": -0.002, "sticker": -0.003})
    res = run(RunSpec("v2_category_momentum", {}, 1000.0, D0, D0 + timedelta(30), fills=FILLS), FakeStore(b))
    assert res.trades.height == 0 or fills(res).height == 0
