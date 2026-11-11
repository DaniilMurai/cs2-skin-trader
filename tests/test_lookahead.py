import math
import random
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.errors import LookAheadError
from cs2trader.models import FillModel, Order, RunSpec
from cs2trader.strategies import registry

D0 = date(2024, 1, 1)


def synthetic_bars(seed: int, days: int, n_items: int = 12) -> pl.DataFrame:
    rng = random.Random(seed)
    rows = []
    for i in range(n_items):
        price = rng.uniform(0.5, 80)
        for d in range(-60, days):
            if rng.random() < 0.15:
                continue
            price *= math.exp(rng.gauss(0, 0.04))
            rows.append({"market_hash_name": f"item {i:02d}", "t": D0 + timedelta(d), "price": price,
                         "volume": rng.randint(0, 40), "ask": None, "bid": None,
                         "category": "knife" if i % 3 == 0 else "case"})
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


class FakeStore:
    def __init__(self, bars: pl.DataFrame) -> None:
        self.bars = bars

    def daily_bars(self, start, end):
        return self.bars.filter(pl.col("t").is_between(start, end))

    def items_frame(self):
        return self.bars.select(pl.col("market_hash_name").alias("name"), "category").unique()

    def data_version(self):
        return "fake"


class RandomStrategy:
    """Deterministic given its seed and what it is shown, so any difference between a full and
    a truncated run can only come from data after `now` leaking into the views."""

    name = "random"

    def __init__(self, params) -> None:
        self.params = params
        self.rng = random.Random(params["seed"])
        self.log: list[tuple] = []

    def decide(self, market, book) -> list[Order]:
        hist = market.history(self.rng.choice([1, 7, 30]))
        uni = market.universe()
        idx = market.index(self.rng.choice(["all", "category"]), 14)
        regime = market.regime()
        names = sorted(set(hist["market_hash_name"])) or ["item 00"]
        orders = []
        for _ in range(self.rng.randint(0, 4)):
            name = self.rng.choice(names + ["not traded"])
            side = self.rng.choice(["buy", "sell"])
            qty = self.rng.choice([1, 2, 5, 50, 10_000])
            limit = self.rng.choice([None, None, self.rng.uniform(0.1, 100)])
            orders.append(Order(name, side, qty, limit))
        self.log.append((market.now, round(hist["price"].sum(), 9), tuple(uni),
                         round(idx["level"].sum(), 9), regime, tuple(orders), round(book.balance, 9)))
        return orders


@pytest.fixture
def registered(monkeypatch):
    made: list[RandomStrategy] = []

    def factory(params):
        made.append(RandomStrategy(params))
        return made[-1]

    monkeypatch.setitem(registry.REGISTRY, "random",
                        registry.StrategyEntry("random", "benchmark", factory, {}, ""))
    return made


FILLS = FillModel(participation_cap=0.5, spread_penalty=0.03)


@pytest.mark.parametrize("seed", range(6))
def test_truncated_data_gives_identical_decisions(registered, seed):
    bars = synthetic_bars(seed, 120)
    end = D0 + timedelta(119)
    cut = D0 + timedelta(random.Random(seed).randint(20, 110))
    run(RunSpec("random", {"seed": seed}, 1000.0, D0, end, fills=FILLS), FakeStore(bars))
    run(RunSpec("random", {"seed": seed}, 1000.0, D0, end, fills=FILLS),
        FakeStore(bars.filter(pl.col("t") <= cut)))
    full, part = registered
    assert part.log == [row for row in full.log if row[0] <= cut]
    assert len(part.log) > 10


def test_property_no_negative_qty_or_overspend(registered):
    for seed in range(200):
        bars = synthetic_bars(1000 + seed, 40, n_items=6)
        res = run(RunSpec("random", {"seed": seed}, random.Random(seed).choice([100.0, 1000.0]),
                          D0, D0 + timedelta(39), fills=FILLS), FakeStore(bars))
        assert (res.equity["balance"] >= 0).all()
        assert (res.equity["holdings_value"] >= 0).all()
        if res.trades.height:
            filled = res.trades.filter(pl.col("qty_filled") > 0)
            assert (filled["qty_filled"] <= filled["qty_requested"]).all()
        # Cash conservation: the clamp at zero must not hide overspending.
        cost = res.spec.costs[0]
        flow = sum((-1 if f["side"] == "buy" else 1) * f["qty_filled"] * f["price"]
                   * (1 + (cost.buyer_fee if f["side"] == "buy" else -cost.seller_fee))
                   for f in res.trades.iter_rows(named=True))
        assert res.equity["balance"][-1] == pytest.approx(res.spec.capital + flow, abs=1e-6)


def test_reading_the_future_raises(monkeypatch):
    class Peeker:
        name = "peek"
        params: dict = {}

        def __init__(self, params):
            pass

        def decide(self, market, book):
            market.at(market.now + timedelta(1))
            return []

    monkeypatch.setitem(registry.REGISTRY, "peek", registry.StrategyEntry("peek", "benchmark", Peeker, {}, ""))
    with pytest.raises(LookAheadError):
        run(RunSpec("peek", {}, 100.0, D0, D0 + timedelta(5)), FakeStore(synthetic_bars(0, 10)))
