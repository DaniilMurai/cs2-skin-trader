"""Forward paper test: the registry strategies, hour by hour, on our own market.csgo snapshots."""
import bisect
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl

from cs2trader.backtest.engine import load_market, run
from cs2trader.backtest.view import MarketData
from cs2trader.config import Settings, load_settings
from cs2trader.errors import DataCheckFailed, LookAheadError
from cs2trader.models import FillModel, Regime, RunResult, RunSpec
from cs2trader.store import Store
from cs2trader.strategies.registry import StrategyEntry

MIN_DAYS = 28
# Hourly bars loaded before the first decision, so day-based lookbacks have history from the start.
WARMUP_DAYS = 45
HOUR = pl.Datetime("us", "UTC")

# Where the contract left a choice open; printed by `cs2 forward` and listed in the report.
CHOICES = (
    ("A bar stamped h holds what was known at h: each item's newest ask and bid snapshot with "
     "feed_time in (h - 1h, h]. A decision at h sees bars <= h; its orders fill on the next hour "
     "that has a snapshot."),
    "An hour without a snapshot is not a step: stale quotes are not carried, pending orders wait.",
    ("Price = the ask (the bid when there is no ask). Sales are polled for few items and arrive "
     "late, so they only give volume: sales in the hour already fetched at h, 0 when none seen."),
    ("A buy fills at the ask and a sell at the bid, no ratio and no spread penalty, one unit per "
     "item and side per hour (only the best quote is known); a missing quote fills nothing. "
     "Observed sales do not cap fills."),
    "Holdings are valued at their newest bar's bid (its ask when nobody bids) less the seller fee.",
    ("The 7-day lock is 168 hours from the fill hour. Strategy parameters in days stay calendar "
     "days; parameters that count bars count hours (mean-reversion's minimum sample, the "
     "buy-and-hold retry window)."),
    ("The universe, the category index and the regime come from Steam daily bars of UTC days "
     "that ended before the decision hour: four weeks of snapshots are too short for them."),
)

ROW_SCHEMA = {"strategy": pl.String, "capital": pl.Float64, "start": pl.String, "end": pl.String,
              "params_json": pl.String, "run_id": pl.String, "net_return_cash": pl.Float64,
              "net_return_steam": pl.Float64, "max_drawdown_cash": pl.Float64, "n_trades": pl.Int64,
              "fees_paid": pl.Float64, "bt_run_id": pl.String, "bt_net_return_cash": pl.Float64,
              "bt_net_return_steam": pl.Float64, "bt_max_drawdown_cash": pl.Float64, "bt_n_trades": pl.Int64}


class HourlyData:
    """The market of an hourly run: market.csgo hourly bars for prices, a daily Steam market for
    everything that needs long history."""

    def __init__(self, bars: pl.DataFrame, daily: MarketData, version: str) -> None:
        self.days: list[datetime] = sorted(bars["t"].unique().to_list())
        self.by_day: dict[datetime, pl.DataFrame] = {
            k[0]: v for k, v in bars.partition_by("t", as_dict=True, maintain_order=False).items()}
        self.daily = daily
        self.version = version
        self._empty = bars.clear()

    def view(self, now: datetime) -> HourMarket:
        return HourMarket(self, now)

    def window(self, now: datetime, lookback: int) -> pl.DataFrame:
        lo = bisect.bisect_right(self.days, now - timedelta(days=lookback))
        frames = [self.by_day[h] for h in self.days[lo:bisect.bisect_right(self.days, now)]]
        return pl.concat(frames) if frames else self._empty

    def median_volumes(self, now: datetime, names: set[str]) -> dict[str, float]:
        # Same day rule as HourMarket: today's Steam bar is still forming.
        return self.daily.median_volumes(now.date() - timedelta(days=1), names)


class HourMarket:
    def __init__(self, data: HourlyData, now: datetime) -> None:
        self._data = data
        self.now = now
        # The Steam bar of the current UTC day is still forming, so the newest usable day is yesterday.
        self._day = now.date() - timedelta(days=1)

    def history(self, lookback: int) -> pl.DataFrame:
        if lookback < 1:
            raise ValueError("lookback must be ≥ 1 bar-day")
        return self._data.window(self.now, lookback)

    def at(self, t: datetime) -> pl.DataFrame:
        if t > self.now:
            raise LookAheadError(f"asked for {t} at {self.now}")
        return self._data.by_day.get(t, self._data._empty)

    def universe(self) -> list[str]:
        return self._data.daily.universe_on(self._day)

    def liquidity(self) -> pl.DataFrame:
        return self._data.daily.liquidity_on(self._day)

    def items(self) -> pl.DataFrame:
        return self._data.daily.items

    def index(self, by: Literal["all", "category"], lookback: int) -> pl.DataFrame:
        idx = self._data.daily.index[by].filter(
            (pl.col("t") > self._day - timedelta(days=lookback)) & (pl.col("t") <= self._day))
        # A day's level is known when the day ends, so it is stamped with the next midnight; the
        # newest level is repeated at `now` because strategies look the current level up by t == now.
        stamped = idx.with_columns((pl.col("t") + timedelta(days=1)).cast(pl.Datetime("us"))
                                   .dt.replace_time_zone("UTC").alias("t"))
        latest = stamped.filter(pl.col("t") == pl.col("t").max().over("group")).filter(pl.col("t") < self.now)
        return pl.concat([stamped, latest.with_columns(pl.lit(self.now).cast(HOUR).alias("t"))]).sort("group", "t")

    def regime(self) -> Regime:
        return Regime(self._data.daily.regimes.get(self._day, Regime.FLAT.value))


def snapshot_span(store: Store) -> tuple[int, datetime | None, datetime | None]:
    """Distinct UTC days with an ask snapshot, and the first and last feed time."""
    n, first, last = store.db.execute(
        "SELECT count(DISTINCT CAST(feed_time AS DATE)), CAST(epoch(min(feed_time)) AS DOUBLE), "
        "CAST(epoch(max(feed_time)) AS DOUBLE) FROM asks").fetchall()[0]
    if not n:
        return 0, None, None
    return n, datetime.fromtimestamp(first, UTC), datetime.fromtimestamp(last, UTC)


def forward(entry: StrategyEntry, params: Mapping[str, Any], capital: float, store: Store,
            start: datetime, end: datetime, settings: Settings | None = None,
            min_days: int = MIN_DAYS) -> RunResult:
    settings = settings or load_settings()
    bars = store.hourly_bars(start - timedelta(days=WARMUP_DAYS), end)
    days = bars.filter(pl.col("t") >= start)["t"].dt.date().n_unique()
    if days < max(min_days, 1):
        raise DataCheckFailed(f"forward needs at least {min_days} days of hourly market.csgo snapshots between "
                              f"{start:%Y-%m-%d %H:%M} and {end:%Y-%m-%d %H:%M} UTC, found {days}; "
                              "keep the collector running")
    daily = load_market(store, start.date(), end.date(), settings)
    c = settings.costs
    spec = RunSpec(entry.name, dict(params), capital, start.date(), end.date(), freq="1h", costs=(c,),
                   fills=FillModel(participation_cap=settings.fills.participation_cap, spread_penalty=0.0,
                                   position_cap=settings.fills.position_cap))
    # The exact hours and the newest snapshot are part of the run identity; daily data stays in data_version.
    version = f"+{start.isoformat()}..{end.isoformat()}@{store.snapshot_version()}"
    return run(spec, store, settings, HourlyData(bars.filter(pl.col("t") <= end), daily, version))


def summary_row(fwd: RunResult, backtest: RunResult | None, start: datetime, end: datetime) -> dict:
    from cs2trader.evaluate import metrics

    m = metrics(fwd)
    row = {"strategy": fwd.spec.strategy, "capital": fwd.spec.capital, "start": start.isoformat(),
           "end": end.isoformat(), "params_json": json.dumps(dict(fwd.spec.params), sort_keys=True),
           "run_id": fwd.run_id, "net_return_cash": m.net_return_cash, "net_return_steam": m.net_return_steam,
           "max_drawdown_cash": m.max_drawdown_cash, "n_trades": m.n_trades, "fees_paid": m.fees_paid}
    if backtest is not None:
        b = metrics(backtest)
        row |= {"bt_run_id": backtest.run_id, "bt_net_return_cash": b.net_return_cash,
                "bt_net_return_steam": b.net_return_steam, "bt_max_drawdown_cash": b.max_drawdown_cash,
                "bt_n_trades": b.n_trades}
    return row


def save_rows(path: Path, rows: list[dict]) -> pl.DataFrame:
    """One row per (strategy, capital): a new forward run replaces the earlier one."""
    new = pl.DataFrame(rows, schema=ROW_SCHEMA)
    if path.exists():
        old = pl.read_parquet(path).join(new, on=["strategy", "capital"], how="anti")
        new = pl.concat([old, new], how="diagonal_relaxed")
    path.parent.mkdir(parents=True, exist_ok=True)
    new = new.sort("strategy", "capital")
    new.write_parquet(path)
    return new

