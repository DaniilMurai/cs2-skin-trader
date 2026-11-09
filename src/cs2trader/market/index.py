from typing import Literal

import polars as pl

from cs2trader.config import UniverseSettings

MAX_GAP_DAYS = 7
MIN_ITEMS = {"all": 10, "category": 5}


def market_index(bars: pl.DataFrame, items: pl.DataFrame,
                 by: Literal["all", "category"], s: UniverseSettings | None = None) -> pl.DataFrame:
    """Chain-linked index of the median daily log return, columns t, group, level, n_items.

    An item contributes on day t if it has a bar on t, its previous bar is at most MAX_GAP_DAYS
    old, its trailing 30-day mean daily volume (missing days = 0) is at least
    `min_median_volume` and its price is at least `min_price_usd`. The median, not a
    volume-weighted mean, keeps thousands of cheap stickers and a few huge knife moves from
    dominating, and every input is known at t, so the level on t never changes when later data
    arrives. Days with fewer than MIN_ITEMS contributors keep the previous level. The level
    starts at 100 on the group's first day with enough contributors and is forward-filled over
    calendar days without bars.
    """
    s = s or UniverseSettings()
    df = bars.select("market_hash_name", "t", "price", "volume").sort("market_hash_name", "t")
    if by == "category":
        df = df.join(items.select(pl.col("name").alias("market_hash_name"), "category"),
                     on="market_hash_name", how="left").with_columns(
            pl.col("category").fill_null("unknown").alias("group"))
    else:
        df = df.with_columns(pl.lit("all").alias("group"))

    df = df.with_columns(
        (pl.col("volume").rolling_sum_by("t", window_size="30d") / 30).over("market_hash_name").alias("vol30"),
        pl.col("price").shift(1).over("market_hash_name").alias("prev_price"),
        pl.col("t").shift(1).over("market_hash_name").alias("prev_t"),
    )
    contrib = df.filter(
        pl.col("prev_t").is_not_null()
        & ((pl.col("t") - pl.col("prev_t")).dt.total_days() <= MAX_GAP_DAYS)
        & (pl.col("vol30") >= s.min_median_volume)
        & (pl.col("price") >= s.min_price_usd)
        & (pl.col("prev_price") > 0)
    ).with_columns((pl.col("price") / pl.col("prev_price")).log().alias("r"))

    daily = contrib.group_by("group", "t").agg(pl.col("r").median(), pl.len().alias("n_items"))
    min_items = MIN_ITEMS[by]
    out = []
    for group, g in daily.sort("t").group_by("group", maintain_order=True):
        enough = g.filter(pl.col("n_items") >= min_items)
        if enough.is_empty():
            continue
        start = enough["t"].min()
        days = pl.DataFrame({"t": pl.date_range(start, g["t"].max(), "1d", eager=True)})
        g = days.join(g, on="t", how="left").with_columns(
            pl.lit(group[0]).alias("group"),
            pl.col("n_items").fill_null(0).cast(pl.Int64),
        ).with_columns(
            # The start day is the base; its own return is not applied.
            pl.when((pl.col("n_items") >= min_items) & (pl.col("t") > start))
            .then(pl.col("r")).otherwise(0.0).alias("r"),
        ).with_columns((100 * pl.col("r").cum_sum().exp()).alias("level"))
        out.append(g.select("t", "group", "level", "n_items"))
    if not out:
        return pl.DataFrame(schema={"t": pl.Date, "group": pl.String, "level": pl.Float64, "n_items": pl.Int64})
    return pl.concat(out).sort("group", "t")
