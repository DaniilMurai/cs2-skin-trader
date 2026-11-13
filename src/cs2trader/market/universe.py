from datetime import date, timedelta

import polars as pl

from cs2trader.config import UniverseSettings

WINDOW_DAYS = 30


def liquidity(bars: pl.DataFrame, on: date, first_t: pl.DataFrame | None = None) -> pl.DataFrame:
    """One row per item seen up to `on`: first_t, last_price, median_volume (30 days, missing days = 0).
    `first_t` (market_hash_name, first_t) lets the caller pass only recent bars: with it,
    history length comes from that frame and `bars` needs to cover the last 30 days only."""
    b = bars.select("market_hash_name", "t", "price", "volume").filter(pl.col("t") <= on)
    name = "market_hash_name"

    head = b.group_by(name).agg(
        pl.col("t").min().alias("first_t"),
        pl.col("price").sort_by("t").last().alias("last_price"),
    )
    if first_t is not None:
        head = head.drop("first_t").join(first_t, on=name, how="inner")

    # Days without a bar count as volume 0 (thin items skip days). The median of the
    # 30-day vector is taken from the k observed volumes padded with 30-k leading zeros:
    # the two middle ranks (0-based 14 and 15) are looked up in the sorted observed values.
    win = (
        b.filter(pl.col("t") > on - timedelta(days=WINDOW_DAYS))
        .sort(name, "volume")
        .with_columns(
            (
                pl.int_range(pl.len()).over(name)
                + (WINDOW_DAYS - pl.len().over(name))
            ).alias("rank")
        )
        .filter(pl.col("rank").is_in([WINDOW_DAYS // 2 - 1, WINDOW_DAYS // 2]))
        .group_by(name)
        .agg(pl.col("volume").cast(pl.Float64).sum().alias("mid_sum"), pl.len().alias("n"))
    )
    # With fewer than two ranks present, the missing middle ranks are padded zeros.
    med = win.with_columns((pl.col("mid_sum") / 2).alias("median_volume"))

    return (head.join(med.select(name, "median_volume"), on=name, how="left")
            .with_columns(pl.col("median_volume").fill_null(0.0)).sort(name))


def liquid_band(table: pl.DataFrame, min_price: float = 0.0, max_price: float = 0.0,
                min_median_volume: float = 0.0) -> pl.DataFrame:
    """Rows of a `liquidity` table inside the price band and at or above the volume floor.
    `max_price` 0 means no upper bound, so the defaults keep every row."""
    keep = (pl.col("last_price") >= min_price) & (pl.col("median_volume") >= min_median_volume)
    if max_price > 0:
        keep &= pl.col("last_price") <= max_price
    return table.filter(keep)


def universe(bars: pl.DataFrame, on: date, s: UniverseSettings,
             first_t: pl.DataFrame | None = None) -> list[str]:
    return members(liquidity(bars, on, first_t), on, s)


def members(table: pl.DataFrame, on: date, s: UniverseSettings) -> list[str]:
    old_enough = table.filter(pl.col("first_t") <= on - timedelta(days=s.min_history_days))
    band = liquid_band(old_enough, s.min_price_usd, s.max_price_usd, s.min_median_volume)
    return band.get_column("market_hash_name").sort().to_list()
