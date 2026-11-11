"""Read-only data for the UI. Every function opens DuckDB read-only for the call and closes it, so a
`cs2` subprocess (backtest, report, collector) can take the write lock between requests."""
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from cs2trader.collect import collect_status
from cs2trader.config import Settings
from cs2trader.report import results_hash
from cs2trader.store import Store

RUN_ID = re.compile(r"^[0-9a-f]{16}$")
REPORT_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_POINTS = 1500
MAX_TRADES = 500
_cache: dict[tuple, Any] = {}


class Ctx:
    def __init__(self, settings: Settings, config_path: Path = Path("cs2.toml")) -> None:
        self.settings = settings
        self.data_dir = Path(settings.data_dir)
        self.reports_dir = Path(settings.reports_dir)
        self.config_path = config_path


def busy_or_missing(e: Exception) -> dict:
    text = str(e)
    if "lock" in text.lower() or "conflicting" in text.lower():
        return {"state": "busy", "message": "The database is in use by another process (a run, report or "
                                            "backfill). Try again when it finishes."}
    return {"state": "empty", "message": f"No database yet: {text.splitlines()[0]}"}


def open_store(ctx: Ctx) -> Store | dict:
    if not (ctx.data_dir / "cs2.duckdb").exists():
        return {"state": "empty", "message": "No data/cs2.duckdb yet: run the backfill first."}
    try:
        return Store(ctx.data_dir, read_only=True)
    except duckdb.Error as e:
        return busy_or_missing(e)


def _downsample(df: pl.DataFrame, n: int = MAX_POINTS) -> pl.DataFrame:
    if df.height <= n:
        return df
    step = -(-df.height // n)
    return df.gather_every(step)


def _json_rows(df: pl.DataFrame) -> list[dict]:
    return json.loads(df.write_json())


def overview(ctx: Ctx) -> dict:
    store = open_store(ctx)
    if isinstance(store, dict):
        return store
    try:
        n_items = store.db.execute("SELECT count(DISTINCT name) FROM steam_points").fetchone()[0]
        return {"state": "ok", "data_version": store.data_version(), "last_day": str(store.last_day()),
                "items_with_history": n_items}
    finally:
        store.close()


def _reports(ctx: Ctx) -> list[str]:
    if not ctx.reports_dir.exists():
        return []
    days = [p.name for p in ctx.reports_dir.iterdir() if REPORT_DAY.match(p.name)
            and (p / "results.parquet").exists()]
    return sorted(days, reverse=True)


def verdict(ctx: Ctx, day: str | None = None) -> dict:
    days = _reports(ctx)
    if not days:
        return {"state": "empty", "message": "No report yet: run `cs2 report` first.", "reports": []}
    day = day if day in days else days[0]
    folder = ctx.reports_dir / day
    results = pl.read_parquet(folder / "results.parquet")
    digest = results_hash(results)
    meta_path = folder / "verdicts.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    shown = results.filter(~pl.col("period").str.starts_with("wf_fold_"))
    store = open_store(ctx)
    version = None
    if not isinstance(store, dict):
        version, last_day = store.data_version(), str(store.last_day())
        store.close()
    else:
        last_day = None
    return {
        "state": "ok", "day": day, "reports": days, "results_hash": digest,
        "hash_matches_report": None if meta is None else meta["results_hash"] == digest,
        "summary": meta["summary"] if meta else None,
        "verdicts": meta["verdicts"] if meta else None,
        "verdicts_note": None if meta else "This report predates verdicts.json; re-run `cs2 report` for the table.",
        "results": _json_rows(shown), "data_version": version, "last_day": last_day,
        "report_html": f"/reports/{day}/report.html",
    }


def runs(ctx: Ctx) -> dict:
    root = ctx.data_dir / "runs"
    out = []
    if root.exists():
        for spec in root.glob("*/spec.json"):
            if not RUN_ID.match(spec.parent.name):
                continue
            meta = json.loads(spec.read_text())
            s = meta["spec"]
            out.append({"run_id": meta["run_id"], "strategy": s["strategy"], "capital": s["capital"],
                        "start": s["start"], "end": s["end"], "params": s["params"],
                        "mtime": spec.stat().st_mtime})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return {"state": "ok" if out else "empty", "runs": out[:200],
            "message": None if out else "No runs yet: start one on this page or with `cs2 backtest`."}


def run_detail(ctx: Ctx, run_id: str) -> tuple[int, dict]:
    if not RUN_ID.match(run_id):
        return 400, {"error": "bad run id"}
    folder = ctx.data_dir / "runs" / run_id
    if not (folder / "spec.json").exists():
        return 404, {"error": "no such run"}
    meta = json.loads((folder / "spec.json").read_text())
    eq = pl.read_parquet(folder / "equity.parquet").with_columns(pl.col("t").cast(pl.String))
    for unit in ("cash", "steam"):
        col = f"equity_{unit}"
        eq = eq.with_columns((pl.col(col) / pl.col(col).cum_max() - 1).alias(f"drawdown_{unit}"))
    trades = pl.read_parquet(folder / "trades.parquet").with_columns(pl.col("t").cast(pl.String))
    filled = trades.filter(pl.col("qty_filled") > 0)
    return 200, {"state": "ok", "meta": meta, "equity": _json_rows(_downsample(eq)),
                 "n_trades": trades.height, "n_filled": filled.height,
                 "trades": _json_rows(trades.head(MAX_TRADES))}


def market_index_series(ctx: Ctx) -> dict:
    store = open_store(ctx)
    if isinstance(store, dict):
        return store
    try:
        df = store.db.sql("SELECT CAST(t AS VARCHAR) AS t, index_level, ret, regime FROM regimes ORDER BY t").pl()
    finally:
        store.close()
    if df.is_empty():
        return {"state": "empty", "message": "No regime labels yet: run `cs2 regimes`."}
    return {"state": "ok", "points": _json_rows(_downsample(df)), "n_days": df.height}


def market_categories(ctx: Ctx, days: int = 730) -> dict:
    from cs2trader.market.index import market_index

    db_file = ctx.data_dir / "cs2.duckdb"
    if not db_file.exists():
        return {"state": "empty", "message": "No data/cs2.duckdb yet."}
    key = ("categories", days, db_file.stat().st_mtime_ns, db_file.stat().st_size)
    if key in _cache:
        return _cache[key]
    store = open_store(ctx)
    if isinstance(store, dict):
        return store
    try:
        end = store.last_day()
        bars = store.daily_bars(end - timedelta(days=days + 45), end)
        idx = market_index(bars, store.items_frame(), "category", ctx.settings.universe)
    finally:
        store.close()
    idx = idx.filter(pl.col("t") >= end - timedelta(days=days))
    # Rebase every category to 100 at the window start so the lines are comparable.
    idx = idx.with_columns((pl.col("level") / pl.col("level").first().over("group") * 100).alias("level"),
                           pl.col("t").cast(pl.String))
    series = {g: _json_rows(_downsample(d.select("t", "level"), 400)) for (g,), d in idx.group_by("group")}
    out = {"state": "ok", "days": days, "series": series}
    _cache[key] = out
    return out


def market_item_search(ctx: Ctx, q: str) -> dict:
    store = open_store(ctx)
    if isinstance(store, dict):
        return store
    try:
        like = "%" + q.lower().replace("%", "").replace("_", "") + "%"
        rows = store.db.execute(
            "SELECT name FROM items WHERE lower(name) LIKE ? ORDER BY name LIMIT 20", [like]).fetchall()
    finally:
        store.close()
    return {"state": "ok", "names": [r[0] for r in rows]}


def market_item(ctx: Ctx, name: str) -> dict:
    store = open_store(ctx)
    if isinstance(store, dict):
        return store
    try:
        df = store.db.sql("""
            SELECT CAST(CAST(ts AS DATE) AS VARCHAR) AS t,
                   sum(price_usd * volume) / nullif(sum(volume), 0) AS price, sum(volume) AS volume
            FROM steam_points WHERE name = $n GROUP BY 1 ORDER BY 1""", params={"n": name}).pl()
    finally:
        store.close()
    if df.is_empty():
        return {"state": "empty", "message": f"No Steam history for {name!r}."}
    return {"state": "ok", "name": name, "points": _json_rows(_downsample(df))}


def collector(ctx: Ctx, hours: int = 48) -> dict:
    # In-memory store: only the Parquet files are read, the database file stays free for writers.
    store = Store(ctx.data_dir, in_memory=True)
    try:
        status = collect_status(store, hours)
        per_hour = store.db.sql("""
            SELECT CAST(date_trunc('hour', feed_time AT TIME ZONE 'UTC') AS VARCHAR) AS hour,
                   count(DISTINCT market_hash_name) AS items, any_value(host) AS host
            FROM asks GROUP BY ALL ORDER BY 1 DESC LIMIT 96""").pl()
        latest = store.db.execute("SELECT CAST(max(feed_time) AS VARCHAR) FROM asks").fetchone()[0]
    finally:
        store.close()
    if per_hour.is_empty():
        return {"state": "empty", "message": "No collector snapshots under data/collector yet."}
    age_h = None
    if latest:
        age_h = round((datetime.now(UTC) - datetime.fromisoformat(latest.replace(" ", "T")
                       .replace("+00", "+00:00"))).total_seconds() / 3600, 1)
    return {"state": "ok", "window_h": hours, "snapshots": status.snapshots,
            "min_item_share": status.min_item_share, "target": 44 * hours // 48,
            "gaps": [[g[0].isoformat(), g[1].isoformat()] for g in status.gaps],
            "low_share_hours": [h.isoformat() for h in status.low_share],
            "latest_feed_time": latest, "age_hours": age_h, "per_hour": _json_rows(per_hour)}


def strategies() -> dict:
    from cs2trader.strategies.registry import REGISTRY

    return {"strategies": [{"name": n, "family": e.family, "grid": {k: list(v) for k, v in e.grid.items()},
                            "spec": e.spec_md} for n, e in sorted(REGISTRY.items())]}


def settings_dump(settings: Settings) -> dict:
    return json.loads(json.dumps(asdict(settings), default=str))
