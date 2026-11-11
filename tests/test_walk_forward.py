from datetime import date, timedelta

import polars as pl
import pytest
from test_lookahead import synthetic_bars

from cs2trader.config import Settings, WfSettings
from cs2trader.evaluate import folds, grid_variants, holdout, make_spec, walk_forward
from cs2trader.models import Order
from cs2trader.strategies import registry

D0 = date(2024, 1, 1)


class Store:
    def __init__(self, bars: pl.DataFrame) -> None:
        self.bars = bars
        self.variants: list = []

    def daily_bars(self, start, end):
        return self.bars.filter(pl.col("t").is_between(start, end))

    def items_frame(self):
        return self.bars.select(pl.col("market_hash_name").alias("name"), "category").unique()

    def data_version(self):
        return "fake"

    def record_variants(self, rows):
        self.variants += rows

    def last_day(self):
        return self.bars["t"].max()


class Toy:
    """Holds the item with the best `lookback`-day return; sells it when unlocked and no longer best."""

    name = "toy"

    def __init__(self, params) -> None:
        self.params = params

    def decide(self, market, book):
        hist = market.history(self.params["lookback"] + 1)
        first = hist.sort("t").group_by("market_hash_name").agg(pl.col("price").first().alias("p0"),
                                                                 pl.col("price").last().alias("p1"))
        if first.is_empty():
            return []
        best = first.sort((pl.col("p1") / pl.col("p0")), "market_hash_name", descending=[True, False])
        top = best["market_hash_name"][0]
        price = best["p1"][0]
        held = {l.market_hash_name for l in book.lots()}
        orders = [Order(n, "sell", q) for n in held if n != top and (q := book.sellable(n))]
        if top not in held and book.balance > price * 1.1:
            orders.append(Order(top, "buy", int(book.balance // (price * 1.1))))
        return orders


@pytest.fixture(autouse=True)
def toy(monkeypatch):
    entry = registry.StrategyEntry("toy", "momentum", Toy, {"lookback": [3, 10, 20]}, "")
    monkeypatch.setitem(registry.REGISTRY, "toy", entry)
    return entry


WF = WfSettings(start=D0, end=D0 + timedelta(149), train_days=60, test_days=30,
                holdout_start=D0 + timedelta(150))


def test_fold_boundaries_default_settings():
    f = folds(WfSettings())
    assert f[0] == (date(2019, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 3, 31))
    assert f[1][2] == date(2020, 4, 1)
    assert all(a[3] + timedelta(1) == b[2] for a, b in zip(f, f[1:]))
    assert f[-1][3] == date(2025, 9, 30)
    assert all(tr1 + timedelta(1) == te0 and (tr1 - tr0).days == 364 for tr0, tr1, te0, _ in f)
    assert len(f) == 24


def test_grid_variants_cover_the_grid():
    assert len(grid_variants({"a": [1, 2], "b": [None, 0.1, 0.2]})) == 6
    assert grid_variants({}) == [{}]


def test_tuning_never_reads_the_holdout(toy):
    bars = synthetic_bars(7, 200)
    full = walk_forward(toy, 1000.0, Store(bars), WF, Settings())
    cut = walk_forward(toy, 1000.0, Store(bars.filter(pl.col("t") < WF.holdout_start)), WF, Settings())
    assert full.chosen_params == cut.chosen_params
    assert [(a, b, p) for a, b, p, _ in full.folds] == [(a, b, p) for a, b, p, _ in cut.folds]
    assert [m for *_, m in full.folds] == [m for *_, m in cut.folds]
    assert full.n_variants == 3 * (len(folds(WF)) + 1)


def test_tuning_ignores_poisoned_holdout_in_shared_market(toy):
    # report.evaluate_all hands walk_forward a market that includes the holdout; its prices must not matter.
    from cs2trader.backtest.engine import load_market
    bars = synthetic_bars(7, 200)
    poisoned = bars.with_columns(
        pl.when(pl.col("t") >= WF.holdout_start).then(pl.col("price") * 1000).otherwise(pl.col("price"))
        .alias("price"))
    runs = []
    for b in (bars, poisoned):
        st = Store(b)
        data = load_market(st, WF.start, WF.end, Settings())
        runs.append(walk_forward(toy, 1000.0, st, WF, Settings(), data=data))
    clean, dirty = runs
    assert clean.chosen_params == dirty.chosen_params
    assert [m for *_, m in clean.folds] == [m for *_, m in dirty.folds]
    assert clean.test_returns == dirty.test_returns


def test_variants_are_recorded(toy):
    store = Store(synthetic_bars(3, 200))
    res = walk_forward(toy, 100.0, store, WF, Settings())
    assert len(store.variants) == res.n_variants
    assert {v[1] for v in store.variants} == {'{"lookback": 3}', '{"lookback": 10}', '{"lookback": 20}'}


def test_holdout_runs_from_holdout_start_to_last_day(toy):
    store = Store(synthetic_bars(3, 200))
    res = holdout(toy, {"lookback": 10}, 1000.0, store, WF, settings=Settings())
    assert res.equity["t"].min() == WF.holdout_start
    assert res.equity["t"].max() == store.last_day()


def test_stress_scales_costs_and_spread():
    base = make_spec("toy", {}, 100.0, D0, D0, Settings())
    hard = make_spec("toy", {}, 100.0, D0, D0, Settings(), stress=True)
    assert hard.costs[0].seller_fee == pytest.approx(base.costs[0].seller_fee * 1.5)
    assert hard.costs[0].withdraw_fee_cash == pytest.approx(base.costs[0].withdraw_fee_cash * 1.5)
    assert hard.fills.spread_penalty == pytest.approx(base.fills.spread_penalty * 2)
