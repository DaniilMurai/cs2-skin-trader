import math
import random
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.config import RegimesSettings, UniverseSettings
from cs2trader.market.index import market_index
from cs2trader.market.regimes import label_regimes

D0 = date(2024, 1, 1)
DAYS = 200
S = RegimesSettings(lookback_days=30, threshold=0.05)


def synthetic(seed: int = 0, n_items: int = 15, crash_day: int = 120) -> pl.DataFrame:
    rng = random.Random(seed)
    rows = []
    for i in range(n_items):
        price = 10.0 + i
        start = rng.randint(0, 20)
        for d in range(start, DAYS):
            if rng.random() < 0.1:
                continue  # thin items miss days
            drift = -0.02 if crash_day <= d < crash_day + 10 else 0.002
            price *= math.exp(drift + rng.gauss(0, 0.005))
            rows.append({"market_hash_name": f"item {i}", "t": D0 + timedelta(d), "price": price,
                         "volume": rng.randint(5, 50), "category": "knife" if i < 6 else "case"})
    return pl.DataFrame(rows)


def items_of(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.select(pl.col("market_hash_name").alias("name"), "category").unique()


def labels(bars: pl.DataFrame) -> pl.DataFrame:
    return label_regimes(market_index(bars, items_of(bars), "all", UniverseSettings()), S)


def test_every_day_one_label_and_crash_is_falling():
    lab = labels(synthetic())
    days = lab["t"].to_list()
    assert days == [days[0] + timedelta(i) for i in range(len(days))]
    assert lab["t"].n_unique() == lab.height
    assert lab["regime"].is_in(["rising", "falling", "flat"]).all()
    crash_week = lab.filter(pl.col("t").is_between(D0 + timedelta(125), D0 + timedelta(131)))
    assert (crash_week["regime"] == "falling").all()
    assert lab.filter(pl.col("t") == D0 + timedelta(100))["regime"].item() == "rising"


@pytest.mark.parametrize("seed", range(5))
def test_labels_are_point_in_time(seed):
    bars = synthetic(seed)
    full = labels(bars)
    cut = D0 + timedelta(random.Random(seed).randint(40, DAYS - 2))
    part = labels(bars.filter(pl.col("t") <= cut))
    assert part.equals(full.filter(pl.col("t") <= cut))


def test_thin_market_has_no_index():
    bars = synthetic(n_items=5)
    assert market_index(bars, items_of(bars), "all", UniverseSettings()).is_empty()
    assert labels(bars).is_empty()


def test_category_index_has_one_series_per_category():
    bars = synthetic()
    idx = market_index(bars, items_of(bars), "category", UniverseSettings())
    assert set(idx["group"]) == {"knife", "case"}
    first = idx.group_by("group").agg(pl.col("level").first())
    assert (first["level"] == 100.0).all()


def test_low_volume_items_do_not_count():
    bars = synthetic().with_columns(pl.lit(1).alias("volume"))
    assert market_index(bars, items_of(bars), "all", UniverseSettings(min_median_volume=5)).is_empty()
