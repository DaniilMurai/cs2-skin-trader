from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import registry
from cs2trader.strategies.video import DipKnives

D0 = date(2024, 1, 1)
NO_COST_FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0)


def path(prices: dict[int, float], days: int) -> list[float]:
    out, p = [], prices[0]
    for d in range(days):
        p = prices.get(d, p)
        out.append(p)
    return out


def bars(series: dict[str, tuple[str, list[float]]], start_offset: int = -60) -> pl.DataFrame:
    rows = []
    for name, (cat, prices) in series.items():
        for i, price in enumerate(prices):
            rows.append({"market_hash_name": name, "t": D0 + timedelta(start_offset + i), "price": price,
                         "volume": 100, "ask": None, "bid": None, "category": cat})
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
def v1(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "v1_dip_knives",
                        registry.StrategyEntry("v1_dip_knives", "video", DipKnives, {}, ""))


N = 140  # 60 warmup days + 80 traded days
FLAT = [50.0] * N


def drop_at(day: int, depth: float, recover: float | None = None) -> list[float]:
    # day counted from D0; series starts 60 days earlier
    d = day + 60
    pts = {0: 50.0, d: 50.0 * (1 - depth)}
    if recover is not None:
        pts[d + 10] = 50.0 * (1 - depth) * recover
    return path(pts, N)


def fills(res):
    if not res.trades.height:
        return pl.DataFrame(schema={"t": pl.Date, "market_hash_name": pl.String, "side": pl.String,
                                    "qty_filled": pl.Int64, "price": pl.Float64})
    return res.trades.filter(pl.col("qty_filled") > 0)


def test_buys_only_dipping_knives_and_gloves():
    b = bars({
        "★ Knife A": ("knife", drop_at(10, 0.15)),
        "★ Gloves B": ("glove", drop_at(20, 0.12)),
        "★ Knife C": ("knife", FLAT),
        "Case D": ("case", drop_at(10, 0.30)),
    })
    res = run(RunSpec("v1_dip_knives", {"drop_7d": 0.10}, 1000.0, D0, D0 + timedelta(79), fills=NO_COST_FILLS),
              FakeStore(b))
    buys = fills(res).filter(pl.col("side") == "buy")
    assert set(buys["market_hash_name"]) == {"★ Knife A", "★ Gloves B"}
    a = buys.filter(pl.col("market_hash_name") == "★ Knife A")["t"].min()
    assert a == D0 + timedelta(11)  # signal at day 10 close, filled next bar


def test_small_drop_is_ignored():
    b = bars({"★ Knife A": ("knife", drop_at(10, 0.04)), "★ Knife C": ("knife", FLAT)})
    res = run(RunSpec("v1_dip_knives", {"drop_7d": 0.05}, 1000.0, D0, D0 + timedelta(79), fills=NO_COST_FILLS),
              FakeStore(b))
    assert fills(res).height == 0


def test_stop_loss_sells_at_first_unlocked_bar_below_stop():
    # Drop 15% on day 10 (buy fills day 11 at 42.5), then another 25% on day 12 (31.875 < 42.5 × 0.8).
    prices = path({0: 50.0, 70: 42.5, 72: 31.875}, N)
    b = bars({"★ Knife A": ("knife", prices), "★ Knife C": ("knife", FLAT)})
    with_stop = run(RunSpec("v1_dip_knives", {"drop_7d": 0.10, "stop_loss": 0.20, "max_hold": 30},
                            1000.0, D0, D0 + timedelta(79), fills=NO_COST_FILLS), FakeStore(b))
    sells = fills(with_stop).filter(pl.col("side") == "sell")
    # Bought day 11 → unlocks day 18, which may be sold on (decided at day 17 close).
    assert sells["t"].min() == D0 + timedelta(18)
    rebuys = fills(with_stop).filter((pl.col("side") == "buy") & (pl.col("t") > D0 + timedelta(18)))
    assert rebuys["t"].min() is None or rebuys["t"].min() >= D0 + timedelta(26)  # 7-day cooldown
    no_stop = run(RunSpec("v1_dip_knives", {"drop_7d": 0.10, "stop_loss": None, "max_hold": 30},
                          1000.0, D0, D0 + timedelta(79), fills=NO_COST_FILLS), FakeStore(b))
    first_sell = fills(no_stop).filter(pl.col("side") == "sell")["t"].min()
    assert first_sell == D0 + timedelta(42)  # max_hold 30 days after the day-11 buy, filled next bar


def test_take_profit():
    prices = path({0: 50.0, 70: 42.5, 75: 47.0}, N)
    b = bars({"★ Knife A": ("knife", prices), "★ Knife C": ("knife", FLAT)})
    res = run(RunSpec("v1_dip_knives", {"drop_7d": 0.10, "take_profit": 0.10}, 1000.0, D0, D0 + timedelta(79),
                      fills=NO_COST_FILLS), FakeStore(b))
    sells = fills(res).filter(pl.col("side") == "sell")
    assert sells["t"].min() == D0 + timedelta(18)
    assert sells["price"][0] == pytest.approx(47.0)


def test_one_hundred_dollars_skips_unaffordable():
    b = bars({"★ Knife A": ("knife", [p * 10 for p in drop_at(10, 0.15)]),
              "★ Knife B": ("knife", drop_at(10, 0.15)), "★ Knife C": ("knife", FLAT)})
    res = run(RunSpec("v1_dip_knives", {"drop_7d": 0.10, "max_positions": 3}, 100.0, D0, D0 + timedelta(79),
                      fills=NO_COST_FILLS), FakeStore(b))
    buys = fills(res).filter(pl.col("side") == "buy")
    assert set(buys["market_hash_name"]) == {"★ Knife B"}
    assert buys["qty_filled"][0] == 1  # a $33 slot can't hold a $42.50 knife; one unit is bought anyway
