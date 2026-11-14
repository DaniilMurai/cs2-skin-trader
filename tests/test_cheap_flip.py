import math
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from test_lookahead import FakeStore, synthetic_bars

from cs2trader.backtest.engine import run
from cs2trader.backtest.view import MarketData
from cs2trader.config import RegimesSettings, Settings, UniverseSettings
from cs2trader.forward import HourlyData
from cs2trader.models import CostModel, FillModel, RunSpec
from cs2trader.strategies import flip
from cs2trader.strategies.registry import REGISTRY

D0 = date(2024, 1, 1)
WARM = 60
FILLS = FillModel(participation_cap=1.0, spread_penalty=0.03, position_cap=0.05)
# name: (usual price, price during the dip, daily volume)
ITEMS = {
    "dip A": (2.0, 1.4, 400),         # margin 1.843 / 1.442 - 1 = 27.8%
    "dip B": (4.0, 3.0, 400),         # margin 3.686 / 3.090 - 1 = 19.3%
    "shallow dip": (2.0, 1.6, 400),   # margin 1.843 / 1.648 - 1 = 11.8%: over 5%, under 15%
    "no dip": (2.0, 2.0, 400),
    "dear dip": (20.0, 12.0, 400),    # same 40% dip, but not under $5
    "thin dip": (2.0, 1.4, 8),        # under the volume floor
    "penny dip": (0.12, 0.10, 400),   # margin 2.9% with a $0.01 minimum fee, 6.8% with $0.001
}


def bars(days: int, dip: tuple[int, int] = (-2, 3), prints: dict | None = None) -> pl.DataFrame:
    """Every item sits at its usual price and trades at its dip price on days dip[0]..dip[1].
    `prints` {(name, day): factor} multiplies one bar's price."""
    rows = []
    for name, (usual, low, volume) in ITEMS.items():
        for d in range(-WARM, days):
            price = low if dip[0] <= d <= dip[1] else usual
            rows.append({"market_hash_name": name, "t": D0 + timedelta(d),
                         "price": price * (prints or {}).get((name, d), 1.0),
                         "volume": volume, "ask": None, "bid": None, "category": "rifle"})
    return pl.DataFrame(rows, schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def go(b: pl.DataFrame, params: dict, capital: float, days: int, cost: CostModel | None = None):
    costs = (cost,) if cost else RunSpec.__dataclass_fields__["costs"].default
    return run(RunSpec("cheap_flip", params, capital, D0, D0 + timedelta(days - 1), costs=costs, fills=FILLS),
               FakeStore(b))


def filled(res, side: str) -> pl.DataFrame:
    return res.trades.filter((pl.col("qty_filled") > 0) & (pl.col("side") == side))


def test_registered_with_a_small_grid():
    entry = REGISTRY["cheap_flip"]
    assert entry.family == "flip"
    assert math.prod(len(v) for v in entry.grid.values()) <= 12
    assert set(entry.grid) <= set(flip.FLIP_DEFAULTS)


def test_margin_is_net_of_both_legs_minimum_fee_and_rounding():
    s = flip.CheapFlip({})
    # Sell 2.00 * 0.97 = 1.94, fee 5% = 0.097, buy limit 1.40 * 1.03 = 1.442.
    assert s.margin(1.442, 2.0) == pytest.approx(1.843 / 1.442 - 1)
    # Sell 0.12 * 0.97 = 0.1164 -> 0.116 (rounded down); 5% is 0.0058 but the minimum fee is 0.01.
    assert s.margin(0.103, 0.12) == pytest.approx(0.106 / 0.103 - 1)


def test_only_cheap_liquid_items_with_a_margin_above_the_hurdle_are_bought():
    res = go(bars(30), {"hurdle": 0.15}, 100.0, 30)
    assert set(res.trades["market_hash_name"]) == {"dip A", "dip B"}
    buys = filled(res, "buy")
    assert set(buys["market_hash_name"]) == {"dip A", "dip B"}
    assert buys["price"].max() <= 5.0
    # Sized inside 5% of the median volume (20 units) and inside the cash, so nothing is cut.
    assert set(res.trades["reason"]) == {"ok"}

    lower = go(bars(30), {"hurdle": 0.05}, 100.0, 30)
    assert set(filled(lower, "buy")["market_hash_name"]) == {"dip A", "dip B", "shallow dip"}


def test_minimum_fee_decides_the_penny_item(monkeypatch):
    assert "penny dip" not in set(go(bars(30), {"hurdle": 0.05}, 100.0, 30).trades["market_hash_name"])
    low = CostModel(valid_from=date(2013, 1, 1), min_fee=0.001)
    monkeypatch.setattr(flip, "load_settings", lambda: Settings(costs=low))
    res = go(bars(30), {"hurdle": 0.05}, 100.0, 30, cost=low)
    assert "penny dip" in set(filled(res, "buy")["market_hash_name"])


def test_nothing_is_sold_before_the_lock_ends():
    res = go(bars(40), {"max_hold": 7}, 100.0, 40)
    buys, sells = filled(res, "buy"), filled(res, "sell")
    assert sells.height > 0 and "locked" not in set(res.trades["reason"])
    first_buy = dict(buys.group_by("market_hash_name").agg(pl.col("t").min()).iter_rows())
    for r in sells.iter_rows(named=True):
        assert r["t"] >= first_buy[r["market_hash_name"]] + timedelta(7)
    # Bought on the first bar after the signal, sold on the first bar the units are unlocked.
    a = res.trades.filter(pl.col("market_hash_name") == "dip A").select("t", "side", "qty_filled").rows()
    assert a[0][:2] == (D0 + timedelta(1), "buy") and a[1][:2] == (D0 + timedelta(8), "sell")
    assert a[0][2] == a[1][2] > 0
    # The price was back at 2.00 by then: sold at 1.94 less 5%, bought at 1.442.
    assert res.equity["balance"][-1] > 100.0


def test_longer_hold_waits_for_the_usual_price_or_the_deadline():
    back_on_20 = go(bars(40, dip=(-2, 19)), {"max_hold": 60}, 100.0, 40)
    sells = filled(back_on_20, "sell").filter(pl.col("market_hash_name") == "dip A")
    # Two of the three newest bars are at 2.00 on day 21, so the order goes out then and fills on day 22.
    assert sells["t"].to_list() == [D0 + timedelta(22)]

    never_back = go(bars(40, dip=(-2, 39)), {"max_hold": 20}, 100.0, 40)
    sells = filled(never_back, "sell").filter(pl.col("market_hash_name") == "dip A")
    assert sells["t"].min() == D0 + timedelta(1 + 20 + 1)


def test_one_odd_print_is_neither_a_dip_nor_a_fill():
    # "no dip" prints at half its price on the decision day; "dip A" prints 30% up on its fill day.
    res = go(bars(30, prints={("no dip", 0): 0.5, ("dip A", 1): 1.3}), {}, 100.0, 30)
    assert "no dip" not in set(res.trades["market_hash_name"])
    first = res.trades.filter((pl.col("market_hash_name") == "dip A") & (pl.col("t") == D0 + timedelta(1)))
    assert first.select("qty_filled", "reason").rows() == [(0, "limit")]
    assert filled(res, "buy").filter(pl.col("market_hash_name") == "dip A")["t"].min() == D0 + timedelta(2)


def test_a_bid_below_the_recent_price_fills_only_when_the_price_comes_down():
    # Limit 1.40 * 0.9 * 1.03 = 1.298: the 1.442 the dip trades at never fills, a 1.20 print does.
    assert filled(go(bars(30), {"discount": 0.10}, 100.0, 30), "buy").filter(
        pl.col("market_hash_name") == "dip A").height == 0
    res = go(bars(30, prints={("dip A", 2): 1.2 / 1.4}), {"discount": 0.10}, 100.0, 30)
    assert filled(res, "buy").filter(pl.col("market_hash_name") == "dip A")["t"].to_list() == [D0 + timedelta(2)]


def test_truncated_data_gives_the_same_trades():
    params = {"hurdle": 0.0, "max_hold": 14, "min_volume": 5, "max_price": 100.0, "cap_share": 1.0}
    fills = FillModel(participation_cap=1.0, spread_penalty=0.03)
    total = 0
    for seed in range(4):
        b = synthetic_bars(seed, 120)
        cut = D0 + timedelta(45 + 15 * seed)
        spec = RunSpec("cheap_flip", params, 1000.0, D0, D0 + timedelta(119), fills=fills)
        full = run(spec, FakeStore(b))
        part = run(spec, FakeStore(b.filter(pl.col("t") <= cut)))
        assert part.trades.equals(full.trades.filter(pl.col("t") <= cut))
        assert part.equity.equals(full.equity.filter(pl.col("t") <= cut))
        total += filled(part, "buy").height + filled(part, "sell").height
    assert total > 20


def test_runs_unchanged_on_hourly_quotes():
    # Hourly bars carry market.csgo asks and bids for 12 days; the dip starts 2 days before the run.
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for name, (usual, low, _) in ITEMS.items():
        for h in range(-5 * 24, 10 * 24):
            ask = low if -48 <= h < 4 * 24 else usual
            rows.append({"market_hash_name": name, "t": start + timedelta(hours=h), "price": ask, "volume": 0,
                         "ask": ask, "bid": ask * 0.97, "category": "rifle"})
    hourly = pl.DataFrame(rows, schema_overrides={"t": pl.Datetime("us", "UTC")})
    daily = bars(10)
    data = HourlyData(hourly, MarketData(daily, FakeStore(daily).items_frame(), UniverseSettings(),
                                         RegimesSettings()), "")
    spec = RunSpec("cheap_flip", {}, 100.0, D0, D0 + timedelta(9), freq="1h",
                   fills=FillModel(participation_cap=1.0, spread_penalty=0.0, position_cap=0.05))
    res = run(spec, FakeStore(daily), data=data)
    buys, sells = filled(res, "buy"), filled(res, "sell")
    assert set(buys["market_hash_name"]) == {"dip A", "dip B"}
    assert sells.height > 0
    first_buy = dict(buys.group_by("market_hash_name").agg(pl.col("t").min()).iter_rows())
    for r in sells.iter_rows(named=True):
        assert r["t"] >= first_buy[r["market_hash_name"]] + timedelta(hours=168)
