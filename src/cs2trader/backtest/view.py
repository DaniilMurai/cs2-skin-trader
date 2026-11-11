import bisect
import pickle
from collections.abc import Sequence
from datetime import date, timedelta
from itertools import accumulate
from pathlib import Path
from typing import Literal, Protocol

import polars as pl

from cs2trader.config import RegimesSettings, UniverseSettings
from cs2trader.errors import LookAheadError
from cs2trader.market.index import market_index
from cs2trader.market.regimes import label_regimes
from cs2trader.market.universe import WINDOW_DAYS, universe
from cs2trader.models import Lot, Regime


class MarketView(Protocol):
    now: date

    def history(self, lookback: int) -> pl.DataFrame: ...
    def universe(self) -> list[str]: ...
    def items(self) -> pl.DataFrame: ...
    def index(self, by: Literal["all", "category"], lookback: int) -> pl.DataFrame: ...
    def regime(self) -> Regime: ...


class PortfolioView(Protocol):
    balance: float

    def lots(self) -> list[Lot]: ...
    def sellable(self, name: str) -> int: ...


class MarketData:
    """Everything strategies may read, prepared once per run. Every derived series (index,
    regimes, first bar per item) is point-in-time by construction, so slicing it at `now`
    is safe; the views below only ever slice at or before `now`."""

    def __init__(self, bars: pl.DataFrame, items: pl.DataFrame,
                 us: UniverseSettings, rs: RegimesSettings) -> None:
        self.days: list[date] = sorted(bars["t"].unique().to_list())
        self.by_day: dict[date, pl.DataFrame] = {
            k[0]: v for k, v in bars.partition_by("t", as_dict=True, maintain_order=False).items()}
        self.items = items
        self.us = us
        self.first_t = bars.group_by("market_hash_name").agg(pl.col("t").min().alias("first_t"))
        self.index = {by: market_index(bars, items, by, us) for by in ("all", "category")}
        labels = label_regimes(self.index["all"], rs)
        self.regimes: dict[date, str] = dict(zip(labels["t"].to_list(), labels["regime"].to_list()))
        self._universe: dict[date, list[str]] = {}
        self._empty = bars.clear()

    def export(self, directory: Path) -> None:
        """Write the market so worker processes can attach to it without each holding a copy:
        the per-day bars as one Arrow file that every worker memory-maps (the OS shares those
        pages), everything small as a pickle."""
        sizes = [self.by_day[d].height for d in self.days]
        pl.concat([self.by_day[d] for d in self.days]).write_ipc(directory / "bars.arrow", compression="uncompressed")
        small = {k: v for k, v in self.__dict__.items() if k not in ("by_day", "_universe")}
        (directory / "market.pkl").write_bytes(pickle.dumps((small, sizes)))

    @classmethod
    def attach(cls, directory: Path) -> MarketData:
        small, sizes = pickle.loads((directory / "market.pkl").read_bytes())
        self = cls.__new__(cls)
        self.__dict__.update(small)
        bars = pl.read_ipc(directory / "bars.arrow")
        starts = [0, *accumulate(sizes)]
        self.by_day = {d: bars.slice(starts[i], sizes[i]) for i, d in enumerate(self.days)}
        self._universe = {}
        return self

    def window(self, now: date, lookback: int) -> pl.DataFrame:
        frames = [self.by_day[d] for d in self._days_in(now - timedelta(days=lookback), now)]
        return pl.concat(frames) if frames else self._empty

    def universe_on(self, now: date) -> list[str]:
        if now not in self._universe:
            first = self.first_t.filter(pl.col("first_t") <= now)
            self._universe[now] = universe(self.window(now, WINDOW_DAYS), now, self.us, first)
        return self._universe[now]

    def _days_in(self, lo: date, hi: date) -> Sequence[date]:
        return self.days[bisect.bisect_right(self.days, lo):bisect.bisect_right(self.days, hi)]


class BarMarket:
    def __init__(self, data: MarketData, now: date) -> None:
        self._data = data
        self.now = now

    def history(self, lookback: int) -> pl.DataFrame:
        if lookback < 1:
            raise ValueError("lookback must be ≥ 1 bar-day")
        return self._data.window(self.now, lookback)

    def at(self, t: date) -> pl.DataFrame:
        if t > self.now:
            raise LookAheadError(f"asked for {t} at {self.now}")
        return self._data.by_day.get(t, self._data._empty)

    def universe(self) -> list[str]:
        return self._data.universe_on(self.now)

    def items(self) -> pl.DataFrame:
        return self._data.items

    def index(self, by: Literal["all", "category"], lookback: int) -> pl.DataFrame:
        idx = self._data.index[by]
        return idx.filter((pl.col("t") > self.now - timedelta(days=lookback)) & (pl.col("t") <= self.now))

    def regime(self) -> Regime:
        return Regime(self._data.regimes.get(self.now, Regime.FLAT.value))
