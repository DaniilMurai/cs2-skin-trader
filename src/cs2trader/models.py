from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

import polars as pl

Currency = Literal["USD", "UAH", "RUB"]


class Regime(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    FLAT = "flat"


class Category(StrEnum):
    # values from spike S-04; `unknown` must stay <= 1% (AC-4)
    KNIFE = "knife"
    GLOVE = "glove"
    WEAPON_SKIN = "weapon_skin"
    STICKER = "sticker"
    CASE = "case"
    CAPSULE = "capsule"
    SOUVENIR_PACKAGE = "souvenir_package"
    GRAFFITI = "graffiti"
    CHARM = "charm"
    PATCH = "patch"
    MUSIC_KIT = "music_kit"
    MUSIC_KIT_BOX = "music_kit_box"
    AGENT = "agent"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ItemInfo:
    market_hash_name: str
    category: Category
    source: Literal["db", "rule", "none"]
    weapon: str | None
    rarity: str | None
    stattrak: bool
    souvenir: bool
    release_date: date | None


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
    host: Literal["gha", "mac", "cf"]
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
    reason: Literal["ok", "volume_cap", "locked", "no_cash", "no_position", "limit", "no_data", "min_price"]


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
    # Measured on the USD asks feed of 2026-11-13: all 27,157 prices are whole thousandths, lowest 0.005
    price_step: float = 0.001
    min_price: float = 0.005
    # Not published by market.csgo (spike S-03); a pessimistic placeholder, not a fact
    min_fee: float = 0.01


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


@dataclass(frozen=True)
class Metrics:
    net_return_cash: float
    net_return_steam: float
    max_drawdown_cash: float
    max_drawdown_steam: float
    sharpe_cash: float
    n_trades: int
    fees_paid: float


@dataclass(frozen=True)
class Verdict:
    strategy: str
    capital: float
    status: Literal["holds_up", "fails"]
    first_broken_rule: Literal["beats_cash", "beats_bh", "drawdown", "cost_stress", "deflated_sharpe"] | None
    holdout: Metrics
    deflated_sharpe: float
    n_variants: int
