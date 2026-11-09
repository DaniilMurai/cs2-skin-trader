import polars as pl

from cs2trader.config import RegimesSettings
from cs2trader.models import Regime


def label_regimes(index: pl.DataFrame, s: RegimesSettings) -> pl.DataFrame:
    """Columns t, index_level, ret, regime for the `all` index, one row per calendar day.

    `ret` is the index return over `lookback_days` calendar days, or since the first index day
    while fewer days exist, so the first days are labelled too.
    """
    idx = index.filter(pl.col("group") == "all").sort("t")
    ret = pl.col("level") / pl.col("level").shift(s.lookback_days).fill_null(pl.col("level").first()) - 1
    return idx.select(
        "t", pl.col("level").alias("index_level"), ret.alias("ret"),
    ).with_columns(
        pl.when(pl.col("ret") > s.threshold).then(pl.lit(Regime.RISING.value))
        .when(pl.col("ret") < -s.threshold).then(pl.lit(Regime.FALLING.value))
        .otherwise(pl.lit(Regime.FLAT.value)).alias("regime")
    )
