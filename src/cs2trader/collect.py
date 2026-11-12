import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import polars as pl

from cs2trader.config import McSettings, Settings, load_settings, require_secret
from cs2trader.errors import ConfigError, Cs2Error
from cs2trader.models import Sale
from cs2trader.sources.marketcsgo import MarketCsgoKeyed, MarketCsgoPublic
from cs2trader.sources.skinport import SkinportPublic
from cs2trader.sources.waxpeer import WaxpeerPublic
from cs2trader.store import Store

# The keyed endpoint returns the last 100 sales per item; an item is re-polled once
# `sales_refill_share` of that window has elapsed, rounded down to a poll tier (S-07).
_WINDOW = 100


def sales_due(store: Store, now: datetime, s: McSettings) -> list[str]:
    # `sales` is deduplicated and keeps each sale's first fetch time, so a poll that found nothing new
    # only shows up in sales_polls; without it the item would count as overdue on every run.
    tiers = sorted(s.sales_tiers_h)
    stats = store.db.sql(f"""
        SELECT market_hash_name AS name,
               epoch(max(ts)) - epoch(min(ts)) AS span_s,
               greatest(
                   (SELECT epoch(max(fetched_at)) FROM sales x WHERE x.market_hash_name = r.market_hash_name),
                   coalesce((SELECT epoch(max(polled_at)) FROM sales_polls q
                             WHERE q.market_hash_name = r.market_hash_name), 0)) AS fetched_s
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
    kinds: Sequence[Literal["asks", "bids", "sales", "skinport", "waxpeer"]],
    host: Literal["gha", "mac"],
    now: datetime,
    settings: Settings | None = None,
    public: MarketCsgoPublic | None = None,
    keyed: MarketCsgoKeyed | None = None,
    skinport: SkinportPublic | None = None,
    waxpeer: WaxpeerPublic | None = None,
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
            elif kind == "skinport":
                src = skinport or SkinportPublic(quarantine / "skinport")
                rows[kind] = _collect_skinport(store, src, host, files)
            elif kind == "waxpeer":
                at, part = (waxpeer or WaxpeerPublic(quarantine / "waxpeer")).prices()
                files.append(store.write_waxpeer(at, host, part))
                rows[kind] = len(part)
            else:
                rows[kind] = _collect_sales(store, kinds_host=host, now=now, settings=settings,
                                            keyed=keyed, files=files, quarantine=quarantine / "mc_keyed")
            ok.append(kind)
        except Cs2Error as e:
            failed[kind] = e
    report = CollectReport(tuple(ok), tuple(failed), tuple(files))
    return report, failed, rows


def _collect_skinport(store: Store, src: SkinportPublic, host: str, files: list[Path]) -> int:
    # Both views in one snapshot: tradable-only is what can be bought and resold now, the full list
    # includes trade-locked listings that sell cheaper (S-12).
    rows: list[dict] = []
    fetched_at = None
    for tradable in (True, False):
        at, part = src.items(tradable)
        fetched_at = fetched_at or at
        rows += part
    files.append(store.write_skinport(fetched_at, host, rows))
    return len(rows)


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


@dataclass(frozen=True)
class CollectStatus:
    snapshots: int
    min_item_share: float
    gaps: tuple[tuple[datetime, datetime], ...]
    low_share: tuple[datetime, ...] = ()
    window_h: int = 48


def collect_status(store: Store, window_h: int = 48, now: datetime | None = None,
                   min_share: float = 0.9, max_gap_h: int = 2) -> CollectStatus:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    # A snapshot is identified by its UTC hour (floor of feed_time) and counts when that hour's
    # start lies in (now - window_h, now]; the window therefore spans exactly window_h hour slots.
    lo = now - timedelta(hours=window_h)
    hours = store.db.execute("""
        WITH a AS (
            SELECT date_trunc('hour', feed_time AT TIME ZONE 'UTC') AS h, feed_time,
                   count(DISTINCT market_hash_name) AS n
            FROM asks GROUP BY ALL),
        b AS (SELECT DISTINCT date_trunc('hour', feed_time AT TIME ZONE 'UTC') AS h FROM bids)
        SELECT a.h, max(a.n) AS n FROM a JOIN b USING (h)
        WHERE a.h > CAST(? AS TIMESTAMP) AND a.h <= CAST(? AS TIMESTAMP)
        GROUP BY a.h ORDER BY a.h
    """, [lo.replace(tzinfo=None), now.replace(tzinfo=None)]).fetchall()
    hours = pl.DataFrame(hours, schema={"h": pl.Datetime("us"), "n": pl.Int64}, orient="row")
    hours = hours.with_columns(pl.col("h").dt.replace_time_zone("UTC"))
    if hours.is_empty():
        first = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=window_h - 1)
        return CollectStatus(0, 0.0, ((first, first + timedelta(hours=window_h)),) if window_h > max_gap_h else (),
                             (), window_h)
    share = pl.col("n") / pl.col("n").max()
    hours = hours.with_columns(share.alias("share"))

    # Gap = run of consecutive hour slots without a complete snapshot, longer than max_gap_h
    # hours; reported as (start of its first slot, end of its last slot), i.e. end is exclusive.
    first = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=window_h - 1)
    grid = pl.DataFrame({"h": pl.datetime_range(first, now.replace(minute=0, second=0, microsecond=0),
                                                "1h", time_zone="UTC", eager=True)})
    miss = (grid.join(hours.select("h"), on="h", how="anti")
            .with_columns((pl.col("h").diff() != timedelta(hours=1)).fill_null(True).cum_sum().alias("run"))
            .group_by("run").agg(pl.col("h").min().alias("start"), pl.col("h").max().alias("last"))
            .with_columns((pl.col("last") + timedelta(hours=1)).alias("end"))
            .filter((pl.col("end") - pl.col("start")) > timedelta(hours=max_gap_h))
            .sort("start"))
    gaps = tuple(zip(miss["start"].to_list(), miss["end"].to_list(), strict=True))
    low = hours.filter(pl.col("share") < min_share)["h"].to_list()
    return CollectStatus(hours.height, float(hours["share"].min()), gaps, tuple(low), window_h)


def format_status(status: CollectStatus) -> str:
    enough = status.snapshots >= 44 * status.window_h // 48
    ok = "below target" if not enough else ("ok" if not status.low_share else "ok, but some hours have low item share")
    lines = [
        f"snapshots: {status.snapshots} in last {status.window_h} h ({ok}; AC-3 needs >= 44 per 48 h)",
        f"min item share: {status.min_item_share:.1%} (flag below 90%)",
    ]
    lines += [f"gap: {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M} UTC ({(b - a).total_seconds() / 3600:.0f} h)"
              for a, b in status.gaps] or ["gaps > 2 h: none"]
    lines += [f"low share: {h:%Y-%m-%d %H:%M} UTC" for h in status.low_share]
    return "\n".join(lines)
