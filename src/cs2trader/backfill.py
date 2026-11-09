from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cs2trader.errors import ConfigError, SourceUnavailable
from cs2trader.models import FxRate, SteamPoint
from cs2trader.sources.steam import EmptyHistory
from cs2trader.store import Store


class HistorySource(Protocol):
    def price_history(self, market_hash_name: str) -> list[SteamPoint]: ...


@dataclass(frozen=True)
class BackfillReport:
    ok: int
    empty: int
    errors: int
    skipped: int


def feed_universe(store: Store) -> list[str]:
    rows = store.db.execute(
        "SELECT DISTINCT market_hash_name FROM asks WHERE feed_time = (SELECT max(feed_time) FROM asks) "
        "ORDER BY 1"
    ).fetchall()
    if not rows:
        raise ConfigError("no asks snapshot stored: run `cs2 collect run --kinds asks` or pass --names")
    return [r[0] for r in rows]


def read_names(path: Path) -> list[str]:
    return [n.strip() for n in path.read_text().splitlines() if n.strip()]


def backfill(store: Store, source: HistorySource, names: Sequence[str], fx: FxRate, *,
             refresh: bool = False, limit: int | None = None, stop_after_errors: int = 10,
             echo: Callable[[str], None] = print) -> BackfillReport:
    done = set() if refresh else store.fetched_names()
    todo = [n for n in names if n not in done]
    if limit is not None:
        todo = todo[:limit]
    ok = empty = errors = in_a_row = 0
    for name in todo:
        try:
            points = source.price_history(name)
        except EmptyHistory:
            store.mark_fetched(name, "empty", 0)
            empty += 1
            echo(f"empty {name}")
            continue
        except SourceUnavailable as e:
            store.mark_fetched(name, "error", 0)
            errors += 1
            in_a_row += 1
            echo(f"error {name}: {e}")
            if in_a_row >= stop_after_errors:
                raise SourceUnavailable("steam", f"{in_a_row} items failed in a row, stopping") from e
            continue
        in_a_row = 0
        store.upsert_steam(points, fx)
        store.mark_fetched(name, "ok", len(points))
        ok += 1
        echo(f"ok    {name}: {len(points)} points, {points[0].ts:%Y-%m-%d} → {points[-1].ts:%Y-%m-%d}")
    return BackfillReport(ok, empty, errors, sum(n in done for n in names))


def request_logger(path: Path) -> Callable[[datetime, int], None]:
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(ts: datetime, status: int) -> None:
        with path.open("a") as f:
            f.write(f"{ts.isoformat()}\t{status}\n")

    return log


def merge_from(store: Store, other_data_dir: Path) -> dict[str, int]:
    """Copy Steam history and fetch log from another data dir (a backfill run kept apart so its
    long write lock doesn't block other commands). Rows already here are replaced."""
    src = other_data_dir / "cs2.duckdb"
    store.db.execute(f"ATTACH '{src}' AS other (READ_ONLY)")
    try:
        counts = {}
        for table in ("steam_points", "steam_fetch_log"):
            store.db.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM other.{table}")
            counts[table] = store.db.execute(f"SELECT count(*) FROM other.{table}").fetchall()[0][0]
        log = other_data_dir / "logs" / "steam_requests.tsv"
        if log.exists():
            mine = store.data_dir / "logs" / "steam_requests.tsv"
            mine.parent.mkdir(parents=True, exist_ok=True)
            with mine.open("a") as f:
                f.write(log.read_text())
        return counts
    finally:
        store.db.execute("DETACH other")
