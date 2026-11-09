from datetime import date, timedelta

import polars as pl

from cs2trader.config import UniverseSettings

WINDOW_DAYS = 30


def universe(bars: pl.DataFrame, on: date, s: UniverseSettings) -> list[str]:
    b = bars.select("market_hash_name", "t", "price", "volume").filter(pl.col("t") <= on)
    name = "market_hash_name"

    head = (
        b.group_by(name)
        .agg(
            pl.col("t").min().alias("first_t"),
            pl.col("price").sort_by("t").last().alias("last_price"),
        )
        .filter(
            (pl.col("first_t") <= on - timedelta(days=s.min_history_days))
            & (pl.col("last_price") >= s.min_price_usd)
        )
    )

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

    out = head.join(med, on=name, how="left").filter(
        pl.col("median_volume").fill_null(0.0) >= s.min_median_volume
    )
    return out.get_column(name).sort().to_list()
