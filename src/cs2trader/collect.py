import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import polars as pl

from cs2trader.config import McSettings, Settings, load_settings, require_secret
from cs2trader.errors import ConfigError, Cs2Error
from cs2trader.models import Sale
from cs2trader.sources.marketcsgo import MarketCsgoKeyed, MarketCsgoPublic
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
    # No sales stored: never polled, or polled with zero sales longer than the largest tier ago
    fresh = store.db.execute("""
        SELECT DISTINCT market_hash_name FROM asks
        WHERE feed_time = (SELECT max(feed_time) FROM asks)
          AND market_hash_name NOT IN (SELECT market_hash_name FROM sales)
          AND market_hash_name NOT IN (
              SELECT market_hash_name FROM sales_polls WHERE epoch(polled_at) > ?)
        ORDER BY market_hash_name
    """, [now.timestamp() - tiers[-1] * 3600]).fetchall()
    return overdue + [r[0] for r in fresh]


@dataclass(frozen=True)
class CollectReport:
    kinds_ok: tuple[str, ...]
    kinds_failed: tuple[str, ...]
    files: tuple[Path, ...]


# Which error decides the exit code when several kinds fail: corrupt data first, then auth,
# then config, and the transient SourceUnavailable last.
_EXIT_PRIORITY = (5, 3, 2, 4)


def worst_error(errors: Sequence[Cs2Error]) -> Cs2Error | None:
    rank = {code: i for i, code in enumerate(_EXIT_PRIORITY)}
    return min(errors, key=lambda e: rank.get(e.exit_code, len(rank)), default=None)


def run_collect(
    store: Store,
    kinds: Sequence[Literal["asks", "bids", "sales"]],
    host: Literal["gha", "mac"],
    now: datetime,
    settings: Settings | None = None,
    public: MarketCsgoPublic | None = None,
    keyed: MarketCsgoKeyed | None = None,
) -> tuple[CollectReport, dict[str, Cs2Error], dict[str, int]]:
    """Returns the report, the error per failed kind and rows written per ok kind (a failed
    sales kind may still have saved the batches fetched before the error)."""
    settings = settings or load_settings()
    ok: list[str] = []
    failed: dict[str, Cs2Error] = {}
    rows: dict[str, int] = {}
    files: list[Path] = []
    quarantine = settings.data_dir / "quarantine"
    for kind in kinds:
        try:
            if kind in ("asks", "bids"):
                src = public or MarketCsgoPublic(host, quarantine / "mc_public")
                snap = src.asks() if kind == "asks" else src.bids()
                files.append(store.write_snapshot(snap))
                rows[kind] = len(snap.rows)
            else:
                rows[kind] = _collect_sales(store, kinds_host=host, now=now, settings=settings,
                                            keyed=keyed, files=files, quarantine=quarantine / "mc_keyed")
            ok.append(kind)
        except Cs2Error as e:
            failed[kind] = e
    report = CollectReport(tuple(ok), tuple(failed), tuple(files))
    return report, failed, rows


def _collect_sales(store: Store, kinds_host: Literal["gha", "mac"], now: datetime, settings: Settings,
                   keyed: MarketCsgoKeyed | None, files: list[Path], quarantine: Path) -> int:
    mc = settings.mc
    if keyed is None:
        keyed = MarketCsgoKeyed(require_secret("MARKETCSGO_API_KEY"), mc.interval_s, mc.sales_batch, quarantine)
    names = sales_due(store, now, mc)[: mc.sales_max_names_per_run]
    got: dict[str, list[Sale]] = {}
    try:
        for i in range(0, len(names), mc.sales_batch):
            got.update(keyed.sales(names[i : i + mc.sales_batch]))
    except Cs2Error as e:
        # Batches fetched before the error are kept; the kind is still reported failed
        if got:
            files.append(store.write_sales(got, kinds_host))
        e.rows_saved = sum(len(v) for v in got.values())  # type: ignore[attr-defined]
        raise
    if got:
        files.append(store.write_sales(got, kinds_host))
    return sum(len(v) for v in got.values())


def collect_once(
    store: Store,
    kinds: Sequence[Literal["asks", "bids", "sales"]],
    host: Literal["gha", "mac"],
    now: datetime,
    settings: Settings | None = None,
    public: MarketCsgoPublic | None = None,
    keyed: MarketCsgoKeyed | None = None,
) -> CollectReport:
    return run_collect(store, kinds, host, now, settings, public, keyed)[0]


def pull_remote(store: Store, repo_url: str) -> int:
    if not repo_url:
        raise ConfigError("mc.data_repo is not set in cs2.toml")
    tree = store.data_dir / "collector"
    before = sum(1 for _ in tree.rglob("*.parquet")) if tree.exists() else 0
    if (tree / ".git").exists():
        cmd = ["git", "-C", str(tree), "pull", "-q", "--ff-only"]
    elif tree.exists() and any(tree.iterdir()):
        # Local mac files predate the clone: clone beside them, then move the git dir in.
        tmp = store.data_dir / "collector.clone"
        subprocess.run(["git", "clone", "-q", "--no-checkout", repo_url, str(tmp)], check=True)
        (tmp / ".git").rename(tree / ".git")
        tmp.rmdir()
        cmd = ["git", "-C", str(tree), "reset", "-q", "--hard", "HEAD"]
    else:
        cmd = ["git", "clone", "-q", repo_url, str(tree)]
    subprocess.run(cmd, check=True)
    return sum(1 for _ in tree.rglob("*.parquet")) - before
