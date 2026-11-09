from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import polars as pl

Currency = Literal["USD", "UAH", "RUB"]


@dataclass(frozen=True)
class SteamPoint:
    market_hash_name: str
    ts: datetime
    price_raw: float
    currency: Currency
    volume: int
    granularity: Literal["day", "hour"]


@dataclass(frozen=True)
class FxRate:
    day: date
    base: Currency
    per_usd: float
    source: Literal["nbu", "cbr"]
    filled: bool


@dataclass(frozen=True)
class AskRow:
    market_hash_name: str
    ask: float
    # active listing count, not sales
    listings: int


@dataclass(frozen=True)
class BidRow:
    market_hash_name: str
    bid: float
    orders: int


@dataclass(frozen=True)
class Snapshot:
    kind: Literal["asks", "bids"]
    feed_time: datetime
    fetched_at: datetime
    host: Literal["gha", "mac"]
    rows: list[AskRow] | list[BidRow]


@dataclass(frozen=True)
class Sale:
    market_hash_name: str
    ts: datetime
    price_raw: float
    currency: Currency


@dataclass(frozen=True)
class Order:
    market_hash_name: str
    side: Literal["buy", "sell"]
    qty: int
    limit: float | None = None


@dataclass(frozen=True)
class Fill:
    t: date | datetime
    market_hash_name: str
    side: Literal["buy", "sell"]
    qty_requested: int
    qty_filled: int
    price: float
    fee: float
    reason: Literal["ok", "volume_cap", "locked", "no_cash", "no_position", "limit", "no_data"]


@dataclass
class Lot:
    market_hash_name: str
    qty: int
    unit_cost: float
    bought: date | datetime
    unlocks: date | datetime


@dataclass(frozen=True)
class CostModel:
    valid_from: date
    seller_fee: float = 0.05
    buyer_fee: float = 0.0
    withdraw_fee_cash: float = 0.05
    withdraw_fee_steam: float = 0.02
    lock_days: int = 7
    cashout_hold_days: int = 8


@dataclass(frozen=True)
class FillModel:
    participation_cap: float = 0.20
    spread_penalty: float = 0.03
    ratio: Mapping[str, float] = field(default_factory=dict)
    ratio_fallback: float = 1.0


@dataclass(frozen=True)
class EquityPoint:
    t: date | datetime
    balance: float
    holdings_value: float
    equity_cash: float
    equity_steam: float
    fees_paid: float


@dataclass(frozen=True)
class RunSpec:
    strategy: str
    params: Mapping[str, Any]
    capital: float
    start: date
    end: date
    freq: Literal["1d", "1h"] = "1d"
    costs: tuple[CostModel, ...] = (CostModel(valid_from=date(2013, 1, 1)),)
    fills: FillModel = FillModel()


@dataclass(frozen=True)
class RunResult:
    run_id: str
    spec: RunSpec
    data_version: str
    trades: pl.DataFrame
    equity: pl.DataFrame
    cashout_available: date | datetime | None
