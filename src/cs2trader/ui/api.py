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
from cs2trader.ui import plain

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
    hash_ok = None if meta is None else meta["results_hash"] == digest
    boards = plain.scoreboard(shown, meta["verdicts"] if meta else None, ctx.settings)
    return {
        "state": "ok", "day": day, "reports": days, "results_hash": digest,
        "is_latest": day == days[0],
        "hash_matches_report": hash_ok,
        "headline": plain.verdict_headline(boards, meta is not None),
        "scoreboard": boards,
        "freshness": plain.report_freshness(day, meta is not None, hash_ok, last_day),
        "holdout_start": ctx.settings.wf.holdout_start.isoformat(),
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
            out.append({"run_id": meta["run_id"], "strategy": s["strategy"],
                        "label": plain.strategy_label(s["strategy"]), "capital": s["capital"],
                        "start": s["start"], "end": s["end"], "params": s["params"],
                        "mtime": spec.stat().st_mtime})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    out = out[:200]
    for r in out:
        r["end_cash"] = _run_end_cash(root / r["run_id"])
    return {"state": "ok" if out else "empty", "runs": out,
            "message": None if out else "No runs yet: start one on this page or with `cs2 backtest`."}


def _run_end_cash(folder: Path) -> float | None:
    """Final cash-out value of a saved run, for the list of past runs."""
    f = folder / "equity.parquet"
    if not f.exists():
        return None
    key = ("end_cash", str(f), f.stat().st_mtime_ns)
    if key not in _cache:
        col = pl.read_parquet(f, columns=["equity_cash"])["equity_cash"]
        _cache[key] = col[-1] if col.len() else None
    return _cache[key]


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
    costs = (meta["spec"].get("costs") or [None])[-1]
    # The summary reads the full curve: downsampling could drop the worst day.
    summary = plain.run_summary(meta["spec"], _json_rows(eq), filled.height, trades.height, costs)
    return 200, {"state": "ok", "meta": meta, "equity": _json_rows(_downsample(eq)),
                 "label": plain.strategy_label(meta["spec"]["strategy"]), "summary": summary,
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
    r = ctx.settings.regimes
    return {"state": "ok", "points": _json_rows(_downsample(df)), "n_days": df.height,
            "now": plain.market_now(_json_rows(df.tail(400)), r.lookback_days, r.threshold)}


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
    out = {"state": "ok", "window_h": hours, "snapshots": status.snapshots,
           "min_item_share": status.min_item_share, "target": 44 * hours // 48,
           "gaps": [[g[0].isoformat(), g[1].isoformat()] for g in status.gaps],
           "low_share_hours": [h.isoformat() for h in status.low_share],
           "latest_feed_time": latest, "age_hours": age_h, "per_hour": _json_rows(per_hour)}
    out["summary"] = plain.collector_summary(out)
    return out


def strategies() -> dict:
    from cs2trader.strategies import event, mean_reversion, momentum, trend, video
    from cs2trader.strategies.registry import REGISTRY

    built_in = {"v1_dip_knives": video.V1_DEFAULTS, "v2_category_momentum": video.V2_DEFAULTS,
                "xs_momentum": momentum.XS_DEFAULTS, "trend_ma": trend.TREND_DEFAULTS,
                "mean_reversion": mean_reversion.MR_DEFAULTS, "event": event.EVENT_DEFAULTS}
    out = []
    for n, e in sorted(REGISTRY.items()):
        text = plain.STRATEGIES.get(n, {})
        params = []
        for key, options in e.grid.items():
            label, unit, help_text = plain.PARAMS.get((n, key), (key, "", ""))
            params.append({"key": key, "label": label, "unit": unit, "help": help_text,
                           "options": list(options), "default": built_in.get(n, {}).get(key)})
        out.append({"name": n, "family": e.family, "grid": {k: list(v) for k, v in e.grid.items()},
                    "spec": e.spec_md, "label": text.get("label", n), "summary": text.get("summary", ""),
                    "family_label": plain.FAMILIES.get(e.family, e.family),
                    "is_benchmark": e.family == "benchmark", "params": params})
    return {"strategies": out}


def home(ctx: Ctx, job_running: bool = False) -> dict:
    """The landing page: four plain answers and what to do next, each from the same data its page shows."""
    ov = overview(ctx)
    v = verdict(ctx)
    if v["state"] == "ok":
        vblock = {**v["headline"], "state": "ok", "day": v["day"], "freshness": v["freshness"],
                  "lines": [b["headline"] for b in v["scoreboard"]] if v["verdicts"] is not None else []}
    else:
        vblock = {"state": "empty", "status": "unknown", "headline": "No verdict yet",
                  "detail": "No report has been built, so no strategy has been judged.", "lines": []}
    data = plain.data_block(ov)
    coll = plain.collector_summary(collector(ctx))
    idx = market_index_series(ctx)
    if idx.get("state") == "ok" and idx["now"]:
        market = {"state": "ok", **idx["now"]}
    else:
        market = {"state": idx.get("state", "empty"), "regime": None, "headline": "No market picture yet",
                  "detail": idx.get("message") or "Run `cs2 regimes` to label the market."}
    return {"state": "ok", "verdict": vblock, "data": data, "collector": coll, "market": market,
            "next": plain.next_steps(vblock, data, coll, job_running),
            "data_version": ov.get("data_version"), "last_day": ov.get("last_day")}


def settings_dump(settings: Settings) -> dict:
    return json.loads(json.dumps(asdict(settings), default=str))
