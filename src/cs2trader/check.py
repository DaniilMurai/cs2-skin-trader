from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from cs2trader.errors import DataCheckFailed
from cs2trader.models import Category
from cs2trader.sources.items_db import classify, fetch_items_db
from cs2trader.store import Store

MIN_COVERAGE = 0.95
MAX_UNKNOWN_SHARE = 0.01
# 24 was the one pace tried in S-01, not a Steam limit; 1.0 s/request (60/min, +2 for window edges)
# ran 260 requests without a 429 (2026-11-09 interval probes)
MAX_REQUESTS_PER_MIN = 62
FIRST_DATE_WINDOW_DAYS = 7
FIRST_DATE_EARLY_DAYS = 1
MAX_EARLY_SHARE = 0.10

# The fixed AC-4 list: (name, category, source)
AC4: list[tuple[str, Category, str]] = [
    ("★ Karambit | Doppler (Factory New)", Category.KNIFE, "db"),
    ("★ StatTrak™ Bayonet | Autotronic (Field-Tested)", Category.KNIFE, "db"),
    ("★ Sport Gloves | Amphibious (Field-Tested)", Category.GLOVE, "db"),
    ("★ Hand Wraps | Arboreal (Factory New)", Category.GLOVE, "db"),
    ("AK-47 | Redline (Field-Tested)", Category.WEAPON_SKIN, "db"),
    ("StatTrak™ AWP | Asiimov (Field-Tested)", Category.WEAPON_SKIN, "db"),
    ("Souvenir AWP | Acheron (Factory New)", Category.WEAPON_SKIN, "db"),
    ("Sticker | 00 Nation (Holo) | Rio 2022", Category.STICKER, "db"),
    ("Recoil Case", Category.CASE, "db"),
    ("10 Year Birthday Sticker Capsule", Category.CAPSULE, "db"),
    ("Antwerp 2022 Dust II Souvenir Package", Category.SOUVENIR_PACKAGE, "db"),
    ("Sealed Graffiti | 100 Thieves | Boston 2018", Category.GRAFFITI, "db"),
    ("Charm | 8 Ball IGL", Category.CHARM, "db"),
    ("Souvenir Charm | Austin 2025 Highlight | 180 One Tap", Category.CHARM, "rule"),
    ("Patch | Abandon Hope", Category.PATCH, "db"),
    ("Music Kit | Adam Beyer, Red Room", Category.MUSIC_KIT, "db"),
    ("Deluge Music Kit Box", Category.MUSIC_KIT_BOX, "db"),
    ("Bloody Darryl The Strapped | The Professionals", Category.AGENT, "db"),
    ("Austin 2025 Viewer Pass", Category.OTHER, "db"),
    ("Totally New Gadget", Category.UNKNOWN, "none"),
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _universe_table(store: Store, universe: Sequence[str]) -> None:
    store.db.register("_check_universe", pl.DataFrame({"name": list(universe)}, schema={"name": pl.String}))


def _sample(rows: Sequence[Any], n: int = 3) -> str:
    return "; ".join(str(r[0]) for r in rows[:n])


def check_coverage(store: Store, universe: Sequence[str]) -> CheckResult:
    if not universe:
        return CheckResult("coverage", False, "empty universe")
    n = len(set(universe))
    with_points, empty, error = store.db.execute("""
        SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM steam_points p WHERE p.name = u.name)),
               count(*) FILTER (WHERE l.status = 'empty'),
               count(*) FILTER (WHERE l.status = 'error')
        FROM (SELECT DISTINCT name FROM _check_universe) u
        LEFT JOIN steam_fetch_log l USING (name)
    """).fetchall()[0]
    unfetched = store.db.execute("""
        SELECT count(*) FROM (SELECT DISTINCT name FROM _check_universe) u
        WHERE NOT EXISTS (SELECT 1 FROM steam_fetch_log l WHERE l.name = u.name)
    """).fetchall()[0][0]
    share = with_points / n
    return CheckResult(
        "coverage", share >= MIN_COVERAGE,
        f"{with_points}/{n} = {share:.1%} with history (need {MIN_COVERAGE:.0%}); "
        f"empty {empty}, error {error}, unfetched {unfetched}")


def check_first_date(store: Store, universe: Sequence[str]) -> CheckResult:
    # No independent first-sale source exists, so items.release_date is the proxy, and it is noisy
    # (full backfill 2026-11-10: median first point = release +1 d, 5% before release, 25% later than
    # +8 d). Hence a rule on the whole set, not on every item. Items without release_date are skipped,
    # and so are cases: their DB date is a first_sale_date that misses real releases by -159...+329 days (S-09).
    rows = store.db.execute("""
        WITH f AS (
            SELECT p.name, CAST(min(p.ts) AS DATE) AS first_day
            FROM steam_points p JOIN (SELECT DISTINCT name FROM _check_universe) u USING (name)
            GROUP BY p.name)
        SELECT f.name, f.first_day, i.release_date, date_diff('day', i.release_date, f.first_day)
        FROM f JOIN items i USING (name) WHERE i.release_date IS NOT NULL AND i.category <> 'case'
    """).fetchall()
    if not rows:
        return CheckResult("first_date", True, "no items with a release date")
    early = [r for r in rows if r[3] < -FIRST_DATE_EARLY_DAYS]
    offsets = sorted(r[3] for r in rows)
    median = (offsets[(len(offsets) - 1) // 2] + offsets[len(offsets) // 2]) / 2
    share = len(early) / len(rows)
    ok = share <= MAX_EARLY_SHARE and 0 <= median <= FIRST_DATE_WINDOW_DAYS
    detail = (f"{len(rows)} items with a release date: first point > {FIRST_DATE_EARLY_DAYS} d before release "
              f"for {share:.1%} (max {MAX_EARLY_SHARE:.0%}), median first point = release {median:+.1f} d "
              f"(allowed 0..{FIRST_DATE_WINDOW_DAYS})")
    if early:
        detail += "; e.g. " + "; ".join(f"{r[0]} first {r[1]} release {r[2]}" for r in early[:3])
    return CheckResult("first_date", ok, detail)


def check_duplicates(store: Store) -> CheckResult:
    dup_ts = store.db.execute(
        "SELECT name, ts FROM steam_points GROUP BY name, ts HAVING count(*) > 1 LIMIT 100").fetchall()
    dup_day = store.db.execute("""
        SELECT name, CAST(ts AS DATE) FROM steam_points WHERE granularity = 'day'
        GROUP BY name, CAST(ts AS DATE) HAVING count(*) > 1 LIMIT 100""").fetchall()
    ok = not dup_ts and not dup_day
    return CheckResult("duplicates", ok, "no duplicate (name, ts) or daily dates" if ok else
                       f"{len(dup_ts)}+ duplicate (name, ts), {len(dup_day)}+ duplicate daily dates; "
                       f"e.g. {_sample(dup_ts or dup_day)}")


def check_fx(store: Store) -> CheckResult:
    n, no_fx, bad_usd, bad_day, bad_rate = store.db.execute("""
        WITH l AS (SELECT name, CAST(fetched_at AS DATE) AS fetch_day FROM steam_fetch_log)
        SELECT count(*),
               count(*) FILTER (WHERE p.fx_per_usd IS NULL OR p.fx_per_usd <= 0 OR p.price_usd IS NULL),
               count(*) FILTER (WHERE p.fx_per_usd > 0
                   AND abs(p.price_usd - p.price_raw / p.fx_per_usd) > 1e-9 * abs(p.price_raw / p.fx_per_usd)),
               count(*) FILTER (WHERE p.fx_day IS DISTINCT FROM l.fetch_day),
               count(*) FILTER (WHERE r.day IS NULL)
        FROM steam_points p
        LEFT JOIN l USING (name)
        LEFT JOIN fx_rates r ON r.day = p.fx_day AND r.base = p.currency AND r.per_usd = p.fx_per_usd
    """).fetchall()[0]
    ok = n > 0 and not (no_fx or bad_usd or bad_day or bad_rate)
    detail = (f"{n} rows; missing fx {no_fx}, price_usd mismatch {bad_usd}, fx_day != fetch day {bad_day}, "
              f"rate not in fx_rates {bad_rate}")
    return CheckResult("fx", ok, detail if n else "no steam_points rows")


def check_request_rate(request_log: Path | None) -> CheckResult:
    if request_log is None or not request_log.exists():
        return CheckResult("request_rate", True, "no log")
    times = sorted(
        datetime.fromisoformat(line.split("\t")[0]).timestamp()
        for line in request_log.read_text().splitlines() if line.strip())
    peak, lo = 0, 0
    for hi, t in enumerate(times):
        while t - times[lo] >= 60:
            lo += 1
        peak = max(peak, hi - lo + 1)
    return CheckResult("request_rate", peak <= MAX_REQUESTS_PER_MIN,
                       f"{len(times)} requests, peak {peak} in 60 s (max {MAX_REQUESTS_PER_MIN})")


def check_unknown_share(store: Store) -> CheckResult:
    n, unknown, missing = store.db.execute("""
        SELECT count(i.name), count(*) FILTER (WHERE i.category = 'unknown'), count(*) FILTER (WHERE i.name IS NULL)
        FROM (SELECT DISTINCT name FROM _check_universe) u LEFT JOIN items i USING (name)
    """).fetchall()[0]
    if n == 0:
        return CheckResult("unknown_share", False, f"no universe names in items ({missing} missing)")
    share = unknown / n
    return CheckResult("unknown_share", share <= MAX_UNKNOWN_SHARE,
                       f"{unknown}/{n} = {share:.2%} unknown (max {MAX_UNKNOWN_SHARE:.0%}); "
                       f"{missing} universe names not in items")


def check_ac4_list(db: Sequence[Mapping[str, Any]]) -> CheckResult:
    got = classify([n for n, _, _ in AC4], db)
    bad = [(i.market_hash_name, i.category, i.source, c, s)
           for i, (_, c, s) in zip(got, AC4, strict=True) if (i.category, i.source) != (c, s)]
    return CheckResult("ac4_list", not bad, f"{len(AC4) - len(bad)}/{len(AC4)} map as expected" + (
        "; e.g. " + "; ".join(f"{b[0]} got {b[1]}/{b[2]} want {b[3]}/{b[4]}" for b in bad[:3]) if bad else ""))


def run_checks(store: Store, universe: Sequence[str], request_log: Path | None, categories: bool,
               items_db: Sequence[Mapping[str, Any]] | None = None) -> list[CheckResult]:
    """`items_db` (raw ByMykel records) is fetched from data/cache when omitted; only used with categories."""
    _universe_table(store, universe)
    try:
        results = [check_coverage(store, universe), check_first_date(store, universe),
                   check_duplicates(store), check_fx(store), check_request_rate(request_log)]
        if categories:
            db = items_db if items_db is not None else fetch_items_db(store.data_dir / "cache")
            results += [check_unknown_share(store), check_ac4_list(db)]
    finally:
        store.db.unregister("_check_universe")
    return results


def format_results(results: Sequence[CheckResult]) -> str:
    return "\n".join(f"{'PASS' if r.ok else 'FAIL'}  {r.name}: {r.detail}" for r in results)


def raise_if_failed(results: Sequence[CheckResult]) -> None:
    failed = [r.name for r in results if not r.ok]
    if failed:
        raise DataCheckFailed("data check failed: " + ", ".join(failed))
