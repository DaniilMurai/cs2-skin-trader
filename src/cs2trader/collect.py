from datetime import datetime

import polars as pl

from cs2trader.config import McSettings
from cs2trader.store import Store

# The keyed endpoint returns the last 100 sales per item; an item is re-polled once
# `sales_refill_share` of that window has elapsed, rounded down to a poll tier (S-07).
_WINDOW = 100


def sales_due(store: Store, now: datetime, s: McSettings) -> list[str]:
    tiers = sorted(s.sales_tiers_h)
    stats = store.db.sql(f"""
        SELECT market_hash_name AS name,
               epoch(max(ts)) - epoch(min(ts)) AS span_s,
               (SELECT epoch(max(fetched_at)) FROM sales x WHERE x.market_hash_name = r.market_hash_name) AS fetched_s
        FROM (SELECT *, row_number() OVER (PARTITION BY market_hash_name ORDER BY ts DESC) AS rn FROM sales) r
        WHERE rn <= {_WINDOW}
        GROUP BY market_hash_name
    """).pl()
    interval_h = pl.col("span_s") / 3600 * s.sales_refill_share
    tier_h = pl.max_horizontal(
        *[pl.when(interval_h >= t).then(pl.lit(float(t))).otherwise(pl.lit(float(tiers[0]))) for t in tiers]
    )
    overdue = (
        stats.with_columns(tier_h.alias("tier_h"))
        .with_columns((now.timestamp() - pl.col("fetched_s") - pl.col("tier_h") * 3600).alias("overdue_s"))
        .filter(pl.col("overdue_s") >= 0)
        .sort("overdue_s", descending=True)["name"]
        .to_list()
    )
    # Never fetched: in the latest asks snapshot but no sales stored; fetched ones with zero sales stay here too
    fresh = store.db.sql("""
        SELECT DISTINCT market_hash_name FROM asks
        WHERE feed_time = (SELECT max(feed_time) FROM asks)
          AND market_hash_name NOT IN (SELECT market_hash_name FROM sales)
        ORDER BY market_hash_name
    """).fetchall()
    return overdue + [r[0] for r in fresh]
