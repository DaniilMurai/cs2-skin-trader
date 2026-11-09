import random
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.config import UniverseSettings
from cs2trader.market.universe import universe

ON = date(2024, 6, 30)
S = UniverseSettings(min_history_days=30, min_median_volume=5, min_price_usd=0.10)


def item(name, start, end, price=1.0, volume=10, every=1):
    days = (end - start).days
    ts = [start + timedelta(days=i) for i in range(0, days + 1, every)]
    return pl.DataFrame(
        {"market_hash_name": name, "t": ts, "price": [price] * len(ts), "volume": [volume] * len(ts)}
    ).with_columns(pl.col("volume").cast(pl.Int64))


def frame(*items):
    return pl.concat(items)


def test_healthy_item_included_sorted():
    bars = frame(item("b", ON - timedelta(days=100), ON), item("a", ON - timedelta(days=100), ON))
    assert universe(bars, ON, S) == ["a", "b"]


def test_item_first_seen_after_on_never_appears():
    bars = frame(item("old", ON - timedelta(days=100), ON), item("new", ON + timedelta(days=1), ON + timedelta(days=200)))
    assert universe(bars, ON, S) == ["old"]


def test_short_history_excluded():
    bars = frame(
        item("short", ON - timedelta(days=29), ON),
        item("exact", ON - timedelta(days=30), ON),
    )
    assert universe(bars, ON, S) == ["exact"]


def test_volume_threshold():
    start = ON - timedelta(days=100)
    bars = frame(
        item("v5", start, ON, volume=5),
        item("v4", start, ON, volume=4),
    )
    assert universe(bars, ON, S) == ["v5"]


def test_missing_days_count_as_zero():
    start = ON - timedelta(days=100)
    # bar every 3rd day, 10 bars in the window of 30: median over 30 days is 0
    bars = frame(item("thin", start, ON, volume=100, every=3), item("daily", start, ON, volume=6))
    assert universe(bars, ON, S) == ["daily"]


def test_missing_days_median_boundary():
    start = ON - timedelta(days=100)
    # 16 of 30 days with volume 5 -> ranks 14 and 15 both 5; 15 of 30 -> rank 14 is 0, 15 is 5 -> 2.5
    full = item("x16", ON - timedelta(days=15), ON, volume=5)  # 16 days
    full = pl.concat([item("x16", start, ON - timedelta(days=30), volume=5), full])
    half = pl.concat([item("x15", start, ON - timedelta(days=30), volume=5), item("x15", ON - timedelta(days=14), ON, volume=5)])
    assert universe(frame(full, half), ON, S) == ["x16"]


def test_price_uses_last_known_bar():
    start = ON - timedelta(days=100)
    cheap_last = pl.concat([item("c", start, ON - timedelta(days=1), price=5.0), item("c", ON, ON, price=0.05)])
    ok_last = pl.concat([item("r", start, ON - timedelta(days=1), price=0.01), item("r", ON, ON, price=0.10)])
    assert universe(frame(cheap_last, ok_last), ON, S) == ["r"]


@pytest.mark.parametrize("seed", range(5))
def test_unchanged_after_deleting_future_bars(seed):
    rng = random.Random(seed)
    base = date(2024, 1, 1)
    rows = []
    for i in range(60):
        first = base + timedelta(days=rng.randint(0, 250))
        for d in range(rng.randint(5, 200)):
            if rng.random() < rng.choice([0.3, 0.7, 1.0]):
                rows.append((f"i{i:02d}", first + timedelta(days=d), rng.choice([0.05, 0.5, 3.0]), rng.randint(0, 12)))
    bars = pl.DataFrame(rows, schema=["market_hash_name", "t", "price", "volume"], orient="row")
    on = base + timedelta(days=rng.randint(40, 300))
    full = universe(bars, on, S)
    assert full == universe(bars.filter(pl.col("t") <= on), on, S)
