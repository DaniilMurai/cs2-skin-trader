from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec

D0 = date(2024, 1, 1)
END = D0 + timedelta(59)
FILLS = FillModel(participation_cap=0.5, spread_penalty=0.03)

PRICES = {"dear knife": (1800.0, "knife"), "cheap knife": (0.5, "knife"),
          "cheap glove": (0.8, "glove"), "case a": (0.2, "case"), "case b": (0.1, "case")}


def bars(volume: int = 1000) -> pl.DataFrame:
    rows = [{"market_hash_name": n, "t": D0 + timedelta(d), "price": p, "volume": volume,
             "ask": None, "bid": None, "category": c}
            for n, (p, c) in PRICES.items() for d in range(-60, 60)]
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


def go(strategy: str, capital: float, volume: int = 1000):
    return run(RunSpec(strategy, {}, capital, D0, END, fills=FILLS), FakeStore(bars(volume)))


def bought(res) -> pl.DataFrame:
    return res.trades.filter(pl.col("qty_filled") > 0)


def test_cash_equity_is_flat():
    res = go("cash", 100.0)
    assert res.trades.is_empty()
    assert res.equity["equity_cash"].to_list() == pytest.approx([100.0 * 0.95] * res.equity.height)


def test_bh_universe_skips_dear_item_and_invests():
    res = go("bh_universe", 100.0)
    names = set(bought(res)["market_hash_name"])
    assert names and "dear knife" not in names
    last = res.equity.row(-1, named=True)
    assert last["holdings_value"] > 0
    spent = 100.0 - last["balance"]
    assert spent >= 95.0


def test_bh_universe_waits_for_volume_cap():
    # cap = 0.5 * 60 = 30 units per bar, so filling takes many days; the retry window covers it
    res = go("bh_universe", 100.0, volume=60)
    assert bought(res)["t"].n_unique() > 1
    assert (res.trades["side"] == "sell").sum() == 0
    assert 100.0 - res.equity.row(-1, named=True)["balance"] >= 95.0


def test_bh_knives_gloves_holds_only_knives_and_gloves():
    res = go("bh_knives_gloves", 100.0)
    names = set(bought(res)["market_hash_name"])
    assert names == {"cheap knife", "cheap glove"}


@pytest.mark.parametrize("strategy", ["cash", "bh_universe", "bh_knives_gloves"])
@pytest.mark.parametrize("capital", [100.0, 1000.0])
def test_runs_at_both_capitals(strategy, capital):
    res = go(strategy, capital)
    assert res.equity.height > 0
    assert (res.equity["balance"] >= 0).all()


def test_equal_split_invests_on_a_large_universe():
    from cs2trader.strategies.benchmarks import equal_split
    prices = {f"item {i}": 0.5 + i * 0.37 for i in range(5000)}
    for budget in (100.0, 1000.0):
        qty = equal_split(prices, budget)
        spent = sum(q * prices[n] for n, q in qty.items())
        assert len(qty) >= 5 and budget * 0.75 < spent <= budget
        assert qty == equal_split(prices, budget)


def test_equal_split_single_dear_item_for_small_budget():
    from cs2trader.strategies.benchmarks import equal_split
    assert equal_split({"knife": 90.0, "other knife": 300.0}, 100.0) == {"knife": 1}
    assert equal_split({"knife": 300.0}, 100.0) == {}


def test_window_without_bars_is_a_clear_error():
    from cs2trader.errors import Cs2Error
    spec = RunSpec("cash", {}, 100.0, date(2010, 1, 1), date(2010, 3, 1), fills=FILLS)
    with pytest.raises(Cs2Error, match="no bars"):
        run(spec, FakeStore(bars()))
