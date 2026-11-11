import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl

from cs2trader.errors import FxMissing
from cs2trader.models import FxRate, ItemInfo, Sale, Snapshot, SteamPoint

SCHEMA = """
CREATE TABLE IF NOT EXISTS steam_points (
    name VARCHAR, ts TIMESTAMPTZ, granularity VARCHAR, price_raw DOUBLE, currency VARCHAR,
    volume INTEGER, fx_per_usd DOUBLE, fx_day DATE, price_usd DOUBLE,
    PRIMARY KEY (name, ts));
CREATE TABLE IF NOT EXISTS steam_fetch_log (
    name VARCHAR PRIMARY KEY, fetched_at TIMESTAMPTZ, status VARCHAR, n_points INTEGER,
    http_status INTEGER);
CREATE TABLE IF NOT EXISTS fx_rates (
    day DATE, base VARCHAR, per_usd DOUBLE, source VARCHAR, filled BOOLEAN,
    PRIMARY KEY (day, base));
CREATE TABLE IF NOT EXISTS items (
    name VARCHAR PRIMARY KEY, category VARCHAR, source VARCHAR, weapon VARCHAR, rarity VARCHAR,
    stattrak BOOLEAN, souvenir BOOLEAN, release_date DATE, first_seen DATE);
CREATE TABLE IF NOT EXISTS variants_tried (
    strategy VARCHAR, params_json VARCHAR, fold INTEGER, capital DOUBLE, run_id VARCHAR,
    PRIMARY KEY (strategy, params_json, fold, capital));
CREATE TABLE IF NOT EXISTS regimes (
    t DATE PRIMARY KEY, index_level DOUBLE, ret DOUBLE, regime VARCHAR);
"""


_VIEW_COLUMNS = {
    "asks": "ask DOUBLE, listings BIGINT",
    "bids": "bid DOUBLE, orders BIGINT",
}
_SALES_COLUMNS = "ts TIMESTAMPTZ, price_raw DOUBLE, currency VARCHAR"


SPIKE_FACTOR = 5.0
SPIKE_WINDOW = 7


def cap_price_jumps(bars: pl.DataFrame) -> pl.DataFrame:
    """Clip each bar's price to within SPIKE_FACTOR of the median of the item's previous bars.

    Steam history has one-day prints at 5-30x the surrounding price (a $0.14 item at $4.48 for one
    day); left in, they dominate drawdowns and mean-reversion signals. The reference uses only
    earlier bars, so a bar never depends on later data; a genuine move of more than 5x is followed
    over a few bars instead of being dropped.
    """
    if bars.is_empty():
        return bars
    ref = pl.col("price").shift(1).rolling_median(SPIKE_WINDOW, min_samples=3) \
        .over("market_hash_name")
    return bars.sort("market_hash_name", "t").with_columns(ref.alias("_ref")).with_columns(
        pl.when(pl.col("_ref").is_null()).then(pl.col("price"))
        .otherwise(pl.col("price").clip(pl.col("_ref") / SPIKE_FACTOR, pl.col("_ref") * SPIKE_FACTOR))
        .alias("price")
    ).drop("_ref").sort("t", "market_hash_name")


class Store:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.db = duckdb.connect(str(data_dir / "cs2.duckdb"))
        self.db.execute("SET TimeZone = 'UTC'")
        self.db.execute(SCHEMA)
        for kind in _VIEW_COLUMNS:
            self._create_view(kind)
        self._create_sales_view()

    def _create_sales_view(self) -> None:
        tree = self.data_dir / "collector" / "sales"
        if any(tree.glob("day=*/*.parquet")):
            # Identical (name, ts, price_raw) = the same sale seen by overlapping polls (S-07)
            src = f"""SELECT * EXCLUDE (day, rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY market_hash_name, ts, price_raw ORDER BY fetched_at, host) AS rn
                FROM read_parquet('{tree}/day=*/*.parquet', hive_partitioning = true)
            ) WHERE rn = 1"""
        else:
            cols = ", ".join(f"CAST(NULL AS {c.split()[1]}) AS {c.split()[0]}" for c in _SALES_COLUMNS.split(", "))
            src = (f"SELECT CAST(NULL AS TIMESTAMPTZ) AS fetched_at, CAST(NULL AS VARCHAR) AS host, "
                   f"CAST(NULL AS VARCHAR) AS market_hash_name, {cols} WHERE false")
        self.db.execute(f"CREATE OR REPLACE VIEW sales AS {src}")
        polls = self.data_dir / "collector" / "sales_polls"
        if any(polls.glob("day=*/*.parquet")):
            psrc = f"SELECT * EXCLUDE (day) FROM read_parquet('{polls}/day=*/*.parquet', hive_partitioning = true)"
        else:
            psrc = ("SELECT CAST(NULL AS TIMESTAMPTZ) AS polled_at, CAST(NULL AS VARCHAR) AS host, "
                    "CAST(NULL AS VARCHAR) AS market_hash_name WHERE false")
        self.db.execute(f"CREATE OR REPLACE VIEW sales_polls AS {psrc}")

    def _create_view(self, kind: str) -> None:
        tree = self.data_dir / "collector" / kind
        if any(tree.glob("day=*/*.parquet")):
            # GHA and the Mac may record the same feed_time; keep one row, 'gha' wins deterministically
            src = f"""SELECT * EXCLUDE (day, rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY feed_time, market_hash_name ORDER BY host) AS rn
                FROM read_parquet('{tree}/day=*/*.parquet', hive_partitioning = true)
            ) WHERE rn = 1"""
        else:
            # No files yet: an empty typed view, so queries return zero rows instead of failing
            src = (f"SELECT CAST(NULL AS TIMESTAMPTZ) AS feed_time, CAST(NULL AS TIMESTAMPTZ) AS fetched_at, "
                   f"CAST(NULL AS VARCHAR) AS host, CAST(NULL AS VARCHAR) AS market_hash_name, "
                   f"{', '.join('CAST(NULL AS ' + c.split()[1] + ') AS ' + c.split()[0] for c in _VIEW_COLUMNS[kind].split(', '))} "
                   f"WHERE false")
        self.db.execute(f"CREATE OR REPLACE VIEW {kind} AS {src}")

    def write_snapshot(self, snap: Snapshot) -> Path:
        feed = snap.feed_time.astimezone(UTC)
        path = (self.data_dir / "collector" / snap.kind / f"day={feed:%Y-%m-%d}"
                / f"{feed:%Y%m%dT%H%M%SZ}-{snap.host}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        if snap.kind == "asks":
            vals = {"ask": [r.ask for r in snap.rows], "listings": [r.listings for r in snap.rows]}
        else:
            vals = {"bid": [r.bid for r in snap.rows], "orders": [r.orders for r in snap.rows]}
        n = len(snap.rows)
        df = pl.DataFrame({
            "feed_time": [feed] * n,
            "fetched_at": [snap.fetched_at.astimezone(UTC)] * n,
            "host": [snap.host] * n,
            "market_hash_name": [r.market_hash_name for r in snap.rows],
            **vals,
        }, schema_overrides={"feed_time": pl.Datetime("us", "UTC"), "fetched_at": pl.Datetime("us", "UTC"),
                             "host": pl.String})
        tmp = path.with_suffix(".tmp")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        self._create_view(snap.kind)
        return path

    def write_sales(self, sales: Mapping[str, Sequence[Sale]], host: str) -> Path:
        fetched_at = datetime.now(UTC)
        rows = [s for group in sales.values() for s in group]
        path = (self.data_dir / "collector" / "sales" / f"day={fetched_at:%Y-%m-%d}"
                / f"{fetched_at:%Y%m%dT%H%M%S%fZ}-{host}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        n = len(rows)
        df = pl.DataFrame({
            "fetched_at": [fetched_at] * n,
            "host": [host] * n,
            "market_hash_name": [r.market_hash_name for r in rows],
            "ts": [r.ts.astimezone(UTC) for r in rows],
            "price_raw": [r.price_raw for r in rows],
            "currency": [r.currency for r in rows],
        }, schema={"fetched_at": pl.Datetime("us", "UTC"), "host": pl.String,
                   "market_hash_name": pl.String, "ts": pl.Datetime("us", "UTC"),
                   "price_raw": pl.Float64, "currency": pl.String})
        tmp = path.with_suffix(".tmp")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        # Items with no sales leave no rows above; the poll log keeps them from being re-polled every run.
        polls = self.data_dir / "collector" / "sales_polls" / path.parent.name / path.name
        polls.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"polled_at": [fetched_at] * len(sales), "host": [host] * len(sales),
                      "market_hash_name": list(sales)},
                     schema={"polled_at": pl.Datetime("us", "UTC"), "host": pl.String,
                             "market_hash_name": pl.String}).write_parquet(polls, compression="zstd")
        self._create_sales_view()
        return path

    def upsert_fx(self, rates: Sequence[FxRate]) -> int:
        self.db.executemany(
            "INSERT OR REPLACE INTO fx_rates VALUES (?, ?, ?, ?, ?)",
            [(r.day, r.base, r.per_usd, r.source, r.filled) for r in rates],
        )
        return len(rates)

    def upsert_items(self, items: Sequence[ItemInfo]) -> int:
        today = datetime.now(UTC).date()
        # first_seen survives re-classification: only the first insert sets it
        self.db.executemany(
            """INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (name) DO UPDATE SET
                 category = excluded.category, source = excluded.source, weapon = excluded.weapon,
                 rarity = excluded.rarity, stattrak = excluded.stattrak, souvenir = excluded.souvenir,
                 release_date = excluded.release_date""",
            [(i.market_hash_name, str(i.category), i.source, i.weapon, i.rarity, i.stattrak, i.souvenir,
              i.release_date, today) for i in items],
        )
        return len(items)

    def write_regimes(self, labels: pl.DataFrame) -> int:
        self.db.execute("DELETE FROM regimes")
        self.db.execute("INSERT INTO regimes SELECT t, index_level, ret, regime FROM labels")
        return labels.height

    def record_variants(self, rows: Sequence[tuple[str, str, int, float, str]]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO variants_tried VALUES (?, ?, ?, ?, ?)", rows)

    def last_day(self) -> date | None:
        return self.db.execute("SELECT max(CAST(ts AS DATE)) FROM steam_points").fetchall()[0][0]

    def items_frame(self) -> pl.DataFrame:
        return self.db.sql("SELECT name, category FROM items").pl()

    def fx(self, day: date, base: str) -> FxRate:
        row = self.db.execute(
            "SELECT day, base, per_usd, source, filled FROM fx_rates WHERE day = ? AND base = ?",
            [day, base],
        ).fetchone()
        if row is None:
            raise FxMissing(f"no {base} rate for {day}; run `cs2 fx update`")
        return FxRate(*row)

    def upsert_steam(self, points: Sequence[SteamPoint], fx: FxRate) -> int:
        for p in points:
            if p.currency != fx.base:
                raise FxMissing(f"{p.market_hash_name}: price in {p.currency}, rate is {fx.base}")
        if not points:
            return 0
        # executemany into a primary-keyed table costs ~0.65 ms/row (2.6 s per 4,000-point item,
        # 85% of the full backfill time); one Arrow-backed statement is ~25x faster
        frame = pl.DataFrame(
            [(p.market_hash_name, p.ts, p.granularity, p.price_raw, p.currency, p.volume,
              fx.per_usd, fx.day, p.price_raw / fx.per_usd) for p in points],
            schema=["name", "ts", "granularity", "price_raw", "currency", "volume",
                    "fx_per_usd", "fx_day", "price_usd"],
            orient="row",
        )
        self.db.register("_steam_batch", frame.to_arrow())
        try:
            self.db.execute("INSERT OR REPLACE INTO steam_points SELECT * FROM _steam_batch")
        finally:
            self.db.unregister("_steam_batch")
        return len(points)

    def mark_fetched(self, name: str, status: Literal["ok", "empty", "error"], n_points: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO steam_fetch_log VALUES (?, ?, ?, ?, NULL)",
            [name, datetime.now(UTC), status, n_points],
        )

    def fetched_names(self) -> set[str]:
        rows = self.db.execute("SELECT name FROM steam_fetch_log WHERE status = 'ok'").fetchall()
        return {r[0] for r in rows}

    def daily_bars(self, start: date, end: date) -> pl.DataFrame:
        # Days with hourly points are collapsed to one bar: volume-weighted price, summed volume.
        # duckdb 2.0: .pl() exists on relations (sql), no longer on execute() results
        bars = self.db.sql("""
            SELECT p.name AS market_hash_name, CAST(p.ts AS DATE) AS t,
                   COALESCE(SUM(p.price_usd * p.volume) / NULLIF(SUM(p.volume), 0),
                            AVG(p.price_usd)) AS price,
                   CAST(SUM(p.volume) AS BIGINT) AS volume,
                   CAST(NULL AS DOUBLE) AS ask, CAST(NULL AS DOUBLE) AS bid,
                   i.category AS category
            FROM steam_points p LEFT JOIN items i ON i.name = p.name
            WHERE CAST(p.ts AS DATE) BETWEEN ? AND ?
            GROUP BY ALL ORDER BY t, market_hash_name
        """, params=[start, end]).pl()
        return cap_price_jumps(bars)

    def data_version(self) -> str:
        parts = [str(self.db.execute("SELECT CAST(max(fetched_at) AS VARCHAR) FROM steam_fetch_log").fetchone()[0])]
        for table in ("steam_points", "steam_fetch_log", "fx_rates", "items"):
            parts.append(str(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]))
        for kind in _VIEW_COLUMNS:
            latest = self.db.execute(f"SELECT CAST(max(feed_time) AS VARCHAR) FROM {kind}").fetchone()[0]
            if latest is not None:  # absent when no snapshots, so the version of snapshot-free data is unchanged
                parts.append(f"{kind}={latest}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
