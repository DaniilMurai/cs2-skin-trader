import polars as pl

from cs2trader.config import RatioSettings
from cs2trader.errors import DataCheckFailed
from cs2trader.store import Store

SIDES = ("ask", "bid")
_SCHEMA = {"category": pl.String, "median": pl.Float64, "q25": pl.Float64, "q75": pl.Float64,
           "n_items": pl.Int64, "fallback": pl.Boolean}


def _summarize(per_item: pl.DataFrame, value: str, min_items: int) -> pl.DataFrame:
    """Median/q25/q75 over items per category; thin categories borrow the all-items numbers."""
    def stats(df: pl.DataFrame) -> dict:
        v = df[value]
        return {"median": v.median(), "q25": v.quantile(0.25, "linear"), "q75": v.quantile(0.75, "linear")}

    overall = stats(per_item)
    rows = []
    for (cat,), grp in per_item.group_by("category", maintain_order=True):
        thin = grp.height < min_items
        rows.append({"category": cat, **(overall if thin else stats(grp)),
                     "n_items": grp.height, "fallback": thin})
    return pl.DataFrame(rows, schema=_SCHEMA)


def _require_days(store: Store, s: RatioSettings) -> None:
    n = store.db.execute(
        "SELECT count(DISTINCT d) FROM (SELECT CAST(feed_time AS DATE) AS d FROM asks "
        "UNION ALL SELECT CAST(feed_time AS DATE) FROM bids)").fetchall()[0][0]
    if n < s.min_days:
        raise DataCheckFailed(f"ratio needs at least {s.min_days} days of market.csgo snapshots, "
                              f"found {n}; keep the collector running")


def steam_to_market_ratio(store: Store, s: RatioSettings) -> pl.DataFrame:
    """market.csgo price / Steam daily price, same day, per category and side (AC-6)."""
    _require_days(store, s)
    steam = store.db.sql("""
        SELECT b.name, b.day, b.price AS steam, COALESCE(i.category, 'unknown') AS category
        FROM (SELECT p.name, CAST(p.ts AS DATE) AS day,
                     COALESCE(SUM(p.price_usd * p.volume) / NULLIF(SUM(p.volume), 0), AVG(p.price_usd)) AS price
              FROM steam_points p GROUP BY ALL) b
        LEFT JOIN items i ON i.name = b.name WHERE b.price > 0
    """).pl()
    parts = []
    for side in SIDES:
        view = "asks" if side == "ask" else "bids"
        # Several snapshots per day: the daily mean smooths feed noise without picking one hour
        mkt = store.db.sql(f"""
            SELECT market_hash_name AS name, CAST(feed_time AS DATE) AS day, avg({side}) AS market
            FROM {view} WHERE {side} > 0 GROUP BY ALL
        """).pl()
        # One ratio per item (median over days) so a heavily sampled item doesn't outvote the rest
        per_item = (mkt.join(steam, on=["name", "day"])
                    .with_columns((pl.col("market") / pl.col("steam")).alias("ratio"))
                    .group_by("name", "category").agg(pl.col("ratio").median()))
        if not per_item.is_empty():
            parts.append(_summarize(per_item, "ratio", s.min_items).with_columns(pl.lit(side).alias("side")))
    if not parts:
        raise DataCheckFailed("no item has both a Steam price and a market.csgo snapshot on the same day; "
                              "run `cs2 backfill steam` and `cs2 items update` first")
    cols = ["category", "side", "median", "q25", "q75", "n_items", "fallback"]
    return pl.concat(parts).select(cols).sort("category", "side")


def measured_spread(store: Store, s: RatioSettings) -> pl.DataFrame:
    """Median (ask - bid) / mid per category, from asks and bids of the same feed_time."""
    pairs = store.db.sql("""
        SELECT a.market_hash_name AS name, COALESCE(i.category, 'unknown') AS category,
               (a.ask - b.bid) / ((a.ask + b.bid) / 2) AS spread
        FROM asks a JOIN bids b USING (feed_time, market_hash_name)
        LEFT JOIN items i ON i.name = a.market_hash_name
        WHERE b.bid > 0 AND a.ask >= b.bid
    """).pl()
    if pairs.is_empty():
        return pl.DataFrame(schema=_SCHEMA)
    per_item = pairs.group_by("name", "category").agg(pl.col("spread").median())
    return _summarize(per_item, "spread", s.min_items).sort("category")


def format_ratio(ratio: pl.DataFrame, spread: pl.DataFrame) -> str:
    lines = [f"{'category':<18}{'side':<5}{'median':>8}{'q25':>8}{'q75':>8}{'items':>7}  note"]
    for r in ratio.iter_rows(named=True):
        note = "fallback: all-items numbers" if r["fallback"] else ""
        lines.append(f"{r['category']:<18}{r['side']:<5}{r['median']:>8.3f}{r['q25']:>8.3f}"
                     f"{r['q75']:>8.3f}{r['n_items']:>7}  {note}")
    lines += ["", "measured spread (ask - bid) / mid"]
    for r in spread.iter_rows(named=True):
        note = "  fallback: all-items number" if r["fallback"] else ""
        lines.append(f"{r['category']:<18}{r['median']:>8.1%}{r['n_items']:>7} items{note}")
    return "\n".join(lines)
