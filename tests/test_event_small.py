import math
import random
from datetime import date, timedelta

import polars as pl
import pytest
from test_lookahead import FakeStore

from cs2trader.backtest.engine import run
from cs2trader.models import FillModel, RunSpec
from cs2trader.strategies import event, event_small
from cs2trader.strategies.registry import REGISTRY

START, END = date(2025, 2, 1), date(2025, 9, 29)
# Shanghai 2024 ended on 2024-12-15: with wait 90 and hold 180 its window is 2025-03-15 … 2025-09-10.
OPENS, CLOSES = date(2025, 3, 15), date(2025, 9, 11)
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.03, position_cap=0.05)
LEGENDS, CHALLENGERS = "Shanghai 2024 Legends Sticker Capsule", "Shanghai 2024 Challengers Sticker Capsule"
# name: (category, price, daily volume)
ITEMS = {
    LEGENDS: ("capsule", 0.50, 400),
    CHALLENGERS: ("capsule", 0.80, 600),
    "Shanghai 2024 Contenders Sticker Capsule": ("capsule", 0.50, 8),         # under the volume floor
    "Shanghai 2024 Champions Autograph Capsule": ("capsule", 12.0, 400),      # over $5
    "Shanghai 2024 Penny Sticker Capsule": ("capsule", 0.05, 400),            # under $0.10
    "Shanghai 2024 Mirage Souvenir Package": ("other", 1.00, 400),            # not a capsule
    "Copenhagen 2024 Legends Sticker Capsule": ("capsule", 0.50, 400),        # its window closed in 2024
    "Fracture Case": ("case", 0.50, 400),
}


def bars(prints: dict | None = None) -> pl.DataFrame:
    """Flat prices; `prints` {(name, day): factor} multiplies one bar's price."""
    rows = []
    for name, (cat, price, volume) in ITEMS.items():
        d = START - timedelta(60)
        while d <= END:
            rows.append({"market_hash_name": name, "t": d, "price": price * (prints or {}).get((name, d), 1.0),
                         "volume": volume, "ask": None, "bid": None, "category": cat})
            d += timedelta(1)
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def go(b: pl.DataFrame, params: dict, capital: float = 100.0, end: date = END):
    return run(RunSpec("event_small", params, capital, START, end, fills=FILLS), FakeStore(b))


def filled(res, side: str) -> pl.DataFrame:
    return res.trades.filter((pl.col("qty_filled") > 0) & (pl.col("side") == side))


def test_registered_next_to_the_old_event_strategy():
    entry = REGISTRY["event_small"]
    assert entry.family == "event"
    assert math.prod(len(v) for v in entry.grid.values()) <= 8
    assert set(entry.grid) <= set(event_small.EVENT_SMALL_DEFAULTS)
    # The old strategy's grid is part of its run_ids.
    assert REGISTRY["event"].grid == event.EVENT_GRID == {
        "rule": ["majors", "cases"], "pre": [0, 14], "post": [0, 14, 30], "hold": [30, 90]}


@pytest.mark.parametrize("capital", [50.0, 100.0])
def test_nothing_before_the_decision_day_and_only_in_band_capsules(capital):
    res = go(bars(), {"wait": 90, "hold": 180}, capital)
    assert set(res.trades["market_hash_name"]) == {LEGENDS, CHALLENGERS}
    assert res.trades["t"].min() == OPENS + timedelta(1)
    buys = filled(res, "buy")
    assert buys["t"].to_list() == [OPENS + timedelta(1)] * 2
    # A fifth of the equity each, cut to 5% of the median volume (20 units of the $0.50 capsule at $100).
    qty = dict(buys.select("market_hash_name", "qty_filled").iter_rows())
    assert qty == {LEGENDS: min(math.floor(capital / 5 / 0.515), 20),
                   CHALLENGERS: min(math.floor(capital / 5 / 0.824), 30)}
    assert set(res.trades["reason"]) == {"ok"}
    sells = filled(res, "sell")
    assert sells["t"].to_list() == [CLOSES + timedelta(1)] * 2
    assert res.equity.row(-1, named=True)["holdings_value"] == 0


def test_wait_moves_the_decision_day():
    res = go(bars(), {"wait": 120, "hold": 180})
    assert res.trades["t"].min() == date(2024, 12, 15) + timedelta(120) + timedelta(1)
    # Window open before the run starts: bought on the first bar of the run, not earlier.
    early = go(bars(), {"wait": 0, "hold": 180})
    assert filled(early, "buy")["t"].to_list() == [START + timedelta(1)] * 2


def test_nothing_is_bought_near_the_end_of_the_window():
    late = date(2025, 8, 20)
    res = run(RunSpec("event_small", {"wait": 90, "hold": 180}, 100.0, late, END, fills=FILLS), FakeStore(bars()))
    assert res.trades.height == 0
    res = run(RunSpec("event_small", {"wait": 90, "hold": 180, "min_left": 10}, 100.0, late, END, fills=FILLS),
              FakeStore(bars()))
    assert filled(res, "buy").height == 2


def test_nothing_is_sold_before_the_lock_ends():
    # The window closes 5 days after it opens, 2 days before the units bought on its first day unlock.
    res = go(bars(), {"wait": 90, "hold": 5, "min_left": 0})
    buys, sells = filled(res, "buy"), filled(res, "sell")
    assert buys["t"].to_list() == [OPENS + timedelta(1)] * 2
    assert "locked" not in set(res.trades["reason"])
    assert sells["t"].to_list() == [OPENS + timedelta(1) + timedelta(7)] * 2
    assert res.equity.row(-1, named=True)["holdings_value"] == 0


def test_one_odd_print_is_neither_a_signal_nor_a_fill():
    dear = "Shanghai 2024 Champions Autograph Capsule"
    # The $12 capsule prints at $2.40 on the decision day; Legends prints 30% up on its fill day.
    res = go(bars({(dear, OPENS): 0.2, (LEGENDS, OPENS + timedelta(1)): 1.3}), {"wait": 90, "hold": 180})
    assert dear not in set(res.trades["market_hash_name"])
    first = res.trades.filter((pl.col("market_hash_name") == LEGENDS) & (pl.col("t") == OPENS + timedelta(1)))
    assert first.select("qty_filled", "reason").rows() == [(0, "limit")]
    assert filled(res, "buy").filter(pl.col("market_hash_name") == LEGENDS)["t"].to_list() == [OPENS + timedelta(2)]


def noisy_bars(seed: int) -> pl.DataFrame:
    rng = random.Random(seed)
    names = [f"{major} {kind} Sticker Capsule" for major in ("Copenhagen 2024", "Shanghai 2024", "Austin 2025")
             for kind in ("Legends", "Challengers", "Contenders", "Champions")]
    rows = []
    for name in names:
        price = rng.uniform(0.15, 6.0)
        d = START - timedelta(60)
        while d <= END:
            d += timedelta(1)
            if rng.random() < 0.1:
                continue
            price *= math.exp(rng.gauss(0, 0.05))
            rows.append({"market_hash_name": name, "t": d, "price": price, "volume": rng.randint(0, 900),
                         "ask": None, "bid": None, "category": "capsule"})
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


@pytest.mark.parametrize("params", [{"wait": 90, "hold": 180}, {"wait": 0, "hold": 365}, {"wait": 60, "hold": 40, "min_left": 5}])
def test_truncated_data_gives_the_same_trades(params):
    total = 0
    for seed in range(4):
        b = noisy_bars(seed)
        cut = OPENS + timedelta(20 + 45 * seed)
        full = go(b, params)
        part = go(b.filter(pl.col("t") <= cut), params, end=cut)
        assert part.trades.equals(full.trades.filter(pl.col("t") <= cut))
        assert part.equity.equals(full.equity.filter(pl.col("t") <= cut))
        total += filled(part, "buy").height + filled(part, "sell").height
    assert total > 8
