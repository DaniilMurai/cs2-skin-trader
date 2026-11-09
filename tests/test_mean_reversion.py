from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.mean_reversion import MR_GRID, MR_SPEC, MeanReversion

D0 = date(2024, 1, 1)
NO_COST_FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)
N = 140  # 60 warmup days + 80 traded days
DIP = 70  # series index of the first dip bar (traded day 10)


def series(dip_depth: float, dip_len: int = 9) -> list[float]:
    out = [50.0 + (0.5 if i % 2 else -0.5) for i in range(N)]
    for i in range(DIP, DIP + dip_len):
        out[i] = 50.0 * (1 - dip_depth)
    return out


def bars(prices: dict[str, list[float]]) -> pl.DataFrame:
    rows = [{"market_hash_name": n, "t": D0 + timedelta(-60 + i), "price": p, "volume": 100,
             "ask": None, "bid": None, "category": "case"}
            for n, ps in prices.items() for i, p in enumerate(ps)]
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
def mr(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "mean_reversion",
                        registry.StrategyEntry("mean_reversion", "mean_reversion", MeanReversion, MR_GRID, MR_SPEC))


def go(b, capital=1000.0, **params):
    res = run(RunSpec("mean_reversion", params, capital, D0, D0 + timedelta(79), fills=NO_COST_FILLS), FakeStore(b))
    t = res.trades
    return t.filter(pl.col("qty_filled") > 0) if t.height else t


def test_buys_after_dip_and_sells_at_recovery():
    f = go(bars({"Case A": series(0.20), "Case B": series(0.0)}), z=2.0, lookback=30)
    assert set(f["market_hash_name"]) == {"Case A"}
    buy = f.filter(pl.col("side") == "buy")
    assert buy["t"].min() == D0 + timedelta(11)  # signal at the dip bar's close, fill next bar
    sells = f.filter(pl.col("side") == "sell")
    # Dip lasts through traded day 18; recovery on day 19, unlocked since day 18.
    assert sells["t"].min() == D0 + timedelta(20)
    assert sells["price"][0] > buy["price"][0]


def test_no_trade_when_z_not_reached():
    # Wide alternating noise (sigma 3) makes a 3.5-point dip worth well under 2 sigma.
    noisy = [50.0 + (3.0 if i % 2 else -3.0) for i in range(N)]
    noisy[DIP] = 46.5
    assert go(bars({"Case A": noisy}), z=2.0, lookback=30).height == 0


def test_stricter_z_skips_moderate_dip():
    b = bars({"Case A": series(0.02)})
    assert go(b, z=1.5, lookback=30).height > 0
    assert go(b, z=3.0, lookback=30).height == 0


@pytest.mark.parametrize("capital", [100.0, 1000.0])
def test_runs_at_both_capitals(capital):
    f = go(bars({"Case A": series(0.20)}), capital=capital)
    assert f.filter(pl.col("side") == "buy").height >= 1
