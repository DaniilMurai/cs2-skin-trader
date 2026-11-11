import math
from datetime import date, timedelta

import polars as pl
from test_benchmarks import FILLS, FakeStore

from cs2trader.backtest.engine import run
from cs2trader.models import RunSpec

D0 = date(2024, 1, 1)
DAYS = 330


def make_bars(price_fn, cats: dict[str, str], volume: int = 1000) -> pl.DataFrame:
    rows = [{"market_hash_name": n, "t": D0 + timedelta(d), "price": price_fn(n, d), "volume": volume,
             "ask": None, "bid": None, "category": c}
            for n, c in cats.items() for d in range(-200, DAYS)]
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def go(strategy: str, bars: pl.DataFrame, start: int = 0, params=None):
    spec = RunSpec(strategy, params or {}, 1000.0, D0 + timedelta(start), D0 + timedelta(DAYS - 1), fills=FILLS)
    return run(spec, FakeStore(bars))


def test_quality_basket_does_not_trade_when_the_ranking_is_stable():
    cats = {f"item {i}": "weapon_skin" for i in range(12)}
    bars = make_bars(lambda n, d: 20.0 + int(n.split()[1]), cats)
    res = go("quality_basket", bars, params={"n_items": 5})
    assert res.trades.filter(pl.col("side") == "sell").is_empty()
    assert res.trades.filter(pl.col("qty_filled") > 0)["market_hash_name"].n_unique() == 5


def test_case_dip_buys_only_cases_and_only_in_a_falling_market():
    cats = {"case a": "case", "case b": "case", **{f"skin {i}": "weapon_skin" for i in range(25)}}
    bars = make_bars(lambda n, d: 10.0 * math.exp(-0.01 * d), cats)
    res = go("case_dip", bars, start=60)
    got = res.trades.filter(pl.col("qty_filled") > 0)
    assert got.height > 0 and set(got["market_hash_name"]) <= {"case a", "case b"}
    assert got["side"].unique().to_list() == ["buy"]  # the market never turns, so nothing is sold


def test_cheap_reversion_skips_items_above_max_price():
    cats = {"cheap": "weapon_skin", "dear": "weapon_skin", "flat": "weapon_skin"}

    def price(n, d):
        if n == "flat":
            return 10.0
        base = 10.0 if n == "cheap" else 500.0
        return base * (0.6 if d == 100 else 1.0 + 0.01 * ((d % 5) - 2))

    res = go("cheap_reversion", make_bars(price, cats), start=60)
    got = res.trades.filter((pl.col("side") == "buy") & (pl.col("qty_filled") > 0))
    assert set(got["market_hash_name"]) == {"cheap"}
