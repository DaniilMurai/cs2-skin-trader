import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl

from cs2trader.errors import FxMissing
from cs2trader.models import FxRate, SteamPoint

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
"""


class Store:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.db = duckdb.connect(str(data_dir / "cs2.duckdb"))
        self.db.execute("SET TimeZone = 'UTC'")
        self.db.execute(SCHEMA)

    def upsert_fx(self, rates: Sequence[FxRate]) -> int:
        self.db.executemany(
            "INSERT OR REPLACE INTO fx_rates VALUES (?, ?, ?, ?, ?)",
            [(r.day, r.base, r.per_usd, r.source, r.filled) for r in rates],
        )
        return len(rates)

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
        self.db.executemany(
            "INSERT OR REPLACE INTO steam_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(p.market_hash_name, p.ts, p.granularity, p.price_raw, p.currency, p.volume,
              fx.per_usd, fx.day, p.price_raw / fx.per_usd) for p in points],
        )
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
        return self.db.sql("""
            SELECT name AS market_hash_name, CAST(ts AS DATE) AS t,
                   COALESCE(SUM(price_usd * volume) / NULLIF(SUM(volume), 0),
                            AVG(price_usd)) AS price,
                   CAST(SUM(volume) AS BIGINT) AS volume,
                   CAST(NULL AS DOUBLE) AS ask, CAST(NULL AS DOUBLE) AS bid
            FROM steam_points
            WHERE CAST(ts AS DATE) BETWEEN ? AND ?
            GROUP BY ALL ORDER BY t, market_hash_name
        """, params=[start, end]).pl()

    def data_version(self) -> str:
        parts = [str(self.db.execute("SELECT CAST(max(fetched_at) AS VARCHAR) FROM steam_fetch_log").fetchone()[0])]
        for table in ("steam_points", "steam_fetch_log", "fx_rates"):
            parts.append(str(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
