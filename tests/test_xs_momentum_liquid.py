import math
from datetime import date, timedelta

import polars as pl
from test_lookahead import FakeStore, synthetic_bars

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies.registry import REGISTRY

D0 = date(2024, 1, 1)
WARM = 60
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.0, position_cap=0.05)
# name: (price 60 days before D0, daily log drift, daily volume)
ITEMS = {
    "in band A": (2.0, 0.006, 400),
    "in band B": (3.0, 0.005, 400),
    "in band, flat": (4.0, 0.0, 400),     # under the min_return hurdle
    "penny": (0.2, 0.008, 400),           # in the universe, under min_price
    "thin": (2.0, 0.02, 8),               # in the universe, under min_volume
    "dear": (400.0, 0.01, 400),           # one unit costs more than a share
}


def bars(days: int, crash_at: int | None = None, prints: dict | None = None) -> pl.DataFrame:
    """Steady drifts; on day `crash_at` every price loses 30% at once and then drifts on.
    `prints` {(name, day): factor} multiplies one bar's price without touching the days after."""
    rows = []
    for name, (price, drift, volume) in ITEMS.items():
        for d in range(-WARM, days):
            price *= math.exp(drift) * (0.7 if d == crash_at else 1.0)
            rows.append({"market_hash_name": name, "t": D0 + timedelta(d),
                         "price": price * (prints or {}).get((name, d), 1.0),
                         "volume": volume, "ask": None, "bid": None, "category": "rifle"})
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def go(b: pl.DataFrame, params: dict, capital: float, days: int):
    return run(RunSpec("xs_momentum_liquid", params, capital, D0, D0 + timedelta(days - 1), fills=FILLS),
               FakeStore(b))


def filled(res, side: str) -> pl.DataFrame:
    return res.trades.filter((pl.col("qty_filled") > 0) & (pl.col("side") == side))


def test_registered_with_a_small_grid():
    entry = REGISTRY["xs_momentum_liquid"]
    assert entry.family == "momentum"
    assert math.prod(len(v) for v in entry.grid.values()) <= 12
    assert REGISTRY["xs_momentum"].grid == {"lookback": [14, 30, 90], "k": [3, 10], "rebalance": [7, 14, 28]}


def test_only_in_band_items_are_bought():
    res = go(bars(60), {"k": 3}, 100.0, 60)
    assert set(res.trades["market_hash_name"]) == {"in band A", "in band B"}
    assert set(filled(res, "buy")["market_hash_name"]) == {"in band A", "in band B"}
    # Sized inside 5% of the median volume (20 units), so the engine never has to cut an order.
    assert set(res.trades["reason"]) == {"ok"}


def test_one_odd_print_neither_ranks_an_item_nor_fills_a_buy():
    # The penny item prints 20x on the ranking day (inside the band at that price); "in band A"
    # prints 2x on the day its buy would fill.
    res = go(bars(30, prints={("penny", 0): 20.0, ("in band A", 1): 2.0}), {"k": 3}, 100.0, 30)
    assert "penny" not in set(res.trades["market_hash_name"])
    first = res.trades.filter((pl.col("market_hash_name") == "in band A") & (pl.col("t") == D0 + timedelta(1)))
    assert first.select("qty_filled", "reason").rows() == [(0, "limit")]
    # The order is repeated the next day, at a normal price.
    assert filled(res, "buy").filter(pl.col("market_hash_name") == "in band A")["t"].min() == D0 + timedelta(2)


def test_stop_sells_on_the_first_unlocked_bar_after_the_trigger():
    crash = 4
    trigger = D0 + timedelta(crash)
    res = go(bars(120, crash_at=crash), {"k": 2, "stop": 0.15, "cooldown": 28}, 50.0, 120)
    buys, sells = filled(res, "buy"), filled(res, "sell")
    before = buys.filter(pl.col("t") <= trigger)
    # Units bought in the last week before the crash are still locked on the next bar.
    assert before["t"].max() > trigger - timedelta(6)
    expected: dict[tuple, int] = {}
    for r in before.iter_rows(named=True):
        key = (r["market_hash_name"], max(trigger + timedelta(1), r["t"] + timedelta(7)))
        expected[key] = expected.get(key, 0) + r["qty_filled"]
    first_exit = sells.filter(pl.col("t") <= trigger + timedelta(8))
    assert {(r["market_hash_name"], r["t"]): r["qty_filled"] for r in first_exit.iter_rows(named=True)} == expected
    assert first_exit["qty_filled"].sum() == before["qty_filled"].sum()

    # In cash for the whole cool-down; back in once it is over and the market no longer falls.
    later = buys.filter(pl.col("t") > trigger)
    assert later.height > 0
    assert later["t"].min() > trigger + timedelta(28)
    flat = res.equity.filter(pl.col("t").is_between(trigger + timedelta(9), trigger + timedelta(28)))
    assert (flat["holdings_value"] == 0).all()


def test_without_a_fall_the_stop_never_fires():
    res = go(bars(120), {"k": 2, "stop": 0.15}, 50.0, 120)
    assert filled(res, "sell").height == 0


def test_truncated_data_gives_the_same_trades():
    params = {"k": 3, "lookback": 14, "rebalance": 7, "stop": 0.1, "cooldown": 7,
              "min_price": 0.5, "min_volume": 5, "min_return": 0.0}
    total = 0
    for seed in range(4):
        b = synthetic_bars(seed, 120)
        cut = D0 + timedelta(45 + 15 * seed)
        full = go(b, params, 1000.0, 120)
        part = go(b.filter(pl.col("t") <= cut), params, 1000.0, 120)
        assert part.trades.equals(full.trades.filter(pl.col("t") <= cut))
        assert part.equity.equals(full.equity.filter(pl.col("t") <= cut))
        total += filled(part, "buy").height + filled(part, "sell").height
    assert total > 20
