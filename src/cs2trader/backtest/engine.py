import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from cs2trader.backtest.view import BarMarket, MarketData
from cs2trader.config import Settings, load_settings
from cs2trader.errors import Cs2Error, InvariantViolation
from cs2trader.market.events import events_hash
from cs2trader.models import (
    CostModel,
    EquityPoint,
    Fill,
    FillModel,
    Lot,
    Order,
    RunResult,
    RunSpec,
)
from cs2trader.store import Store


@dataclass
class Book:
    balance: float
    lots: list[Lot] = field(default_factory=list)
    fees_paid: float = 0.0

    def sellable(self, name: str, on: date | datetime) -> int:
        return sum(l.qty for l in self.lots if l.market_hash_name == name and l.unlocks <= on)


class BookView:
    def __init__(self, book: Book, next_t: date) -> None:
        self._book = book
        self._next_t = next_t
        self.balance = book.balance

    def lots(self) -> list[Lot]:
        return list(self._book.lots)

    def sellable(self, name: str) -> int:
        return self._book.sellable(name, self._next_t)


def cost_at(schedule: Sequence[CostModel], t: date | datetime) -> CostModel:
    day = t.date() if isinstance(t, datetime) else t
    live = [c for c in schedule if c.valid_from <= day]
    if not live:
        raise InvariantViolation(f"no cost model valid on {day}")
    return max(live, key=lambda c: c.valid_from)


def _ratio(row: Mapping[str, Any], fills: FillModel) -> float:
    # The ratio is per category; bars without a category column fall back to one number.
    return fills.ratio.get(row.get("category"), fills.ratio_fallback)


def _cap(volume: int, share: float) -> int:
    # 0.29 * 100 == 28.999999999999996 in floats; the epsilon keeps whole caps whole.
    return math.floor(volume * share + 1e-9)


def apply_fills(orders: Sequence[Order], bar: pl.DataFrame, book: Book,
                cost: CostModel, fills: FillModel) -> list[Fill]:
    if not orders:
        return []
    t = bar["t"][0]
    wanted = {o.market_hash_name for o in orders}
    rows = {r["market_hash_name"]: r
            for r in bar.filter(pl.col("market_hash_name").is_in(wanted)).iter_rows(named=True)}
    out: list[Fill] = []
    traded: dict[str, int] = {}   # units already filled today per item: the volume cap is per item and day
    for o in sorted(orders, key=lambda o: o.side != "sell"):
        if o.qty <= 0:
            raise InvariantViolation(f"order quantity must be positive: {o}")
        r = rows.get(o.market_hash_name)
        if r is None:
            out.append(Fill(t, o.market_hash_name, o.side, o.qty, 0, 0.0, 0.0, "no_data"))
            continue
        cap = max(_cap(r["volume"], fills.participation_cap) - traded.get(o.market_hash_name, 0), 0)
        sign = 1 if o.side == "buy" else -1
        price = r["price"] * _ratio(r, fills) * (1 + sign * fills.spread_penalty)
        if o.limit is not None and sign * (price - o.limit) > 0:
            out.append(Fill(t, o.market_hash_name, o.side, o.qty, 0, price, 0.0, "limit"))
            continue
        if o.side == "buy":
            unit = price * (1 + cost.buyer_fee)
            affordable = math.floor(book.balance / unit + 1e-9)
            qty = min(o.qty, cap, affordable)
            reason = "ok" if qty == o.qty else ("volume_cap" if qty == cap else "no_cash")
            if qty > 0:
                if book.balance - qty * unit < -1e-6:
                    raise InvariantViolation(f"overspend: balance {book.balance} < cost {qty * unit} for {o}")
                # The affordability epsilon may overshoot by float dust; clamp only that.
                book.balance = max(book.balance - qty * unit, 0.0)
                book.lots.append(Lot(o.market_hash_name, qty, unit, t,
                                     t + timedelta(days=cost.lock_days)))
            fee = qty * price * cost.buyer_fee
        else:
            held = sum(l.qty for l in book.lots if l.market_hash_name == o.market_hash_name)
            free = book.sellable(o.market_hash_name, t)
            qty = min(o.qty, cap, free)
            if qty == o.qty:
                reason = "ok"
            elif held == 0:
                reason = "no_position"
            elif qty == free:
                reason = "locked"
            else:
                reason = "volume_cap"
            fee = qty * price * cost.seller_fee
            book.balance += qty * price - fee
            left = qty
            for lot in sorted(book.lots, key=lambda l: l.bought):
                if lot.market_hash_name == o.market_hash_name and lot.unlocks <= t and left:
                    take = min(lot.qty, left)
                    lot.qty -= take
                    left -= take
            book.lots = [l for l in book.lots if l.qty > 0]
        traded[o.market_hash_name] = traded.get(o.market_hash_name, 0) + qty
        book.fees_paid += fee
        if book.balance < -1e-9 or any(l.qty < 0 for l in book.lots):
            raise InvariantViolation(f"balance {book.balance} after {o}")
        out.append(Fill(t, o.market_hash_name, o.side, o.qty, qty, price, fee, reason))
    return out


def _equity(t: date, book: Book, last: dict[str, Mapping[str, Any]], cost: CostModel,
            fills: FillModel) -> EquityPoint:
    held = sum(
        l.qty * last[l.market_hash_name]["price"] * _ratio(last[l.market_hash_name], fills)
        * (1 - fills.spread_penalty) * (1 - cost.seller_fee)
        for l in book.lots
    )
    gross = book.balance + held
    return EquityPoint(t, book.balance, held, gross * (1 - cost.withdraw_fee_cash),
                       gross * (1 - cost.withdraw_fee_steam), book.fees_paid)


FILL_SCHEMA = {"t": pl.Date, "market_hash_name": pl.String, "side": pl.String, "qty_requested": pl.Int64,
               "qty_filled": pl.Int64, "price": pl.Float64, "fee": pl.Float64, "reason": pl.String}

# Bars loaded before `spec.start` so lookbacks, the universe and regimes work from day one.
WARMUP_DAYS = 400


def load_market(store: Store, start: date, end: date, settings: Settings) -> MarketData:
    bars = store.daily_bars(start - timedelta(days=WARMUP_DAYS), end)
    return MarketData(bars, store.items_frame(), settings.universe, settings.regimes)


def run(spec: RunSpec, store: Store, settings: Settings | None = None,
        data: MarketData | None = None) -> RunResult:
    """`data` lets many runs share one prepared market (walk-forward); it must cover
    `spec.start − WARMUP_DAYS … spec.end`. Views never read past the decision bar, so a wider
    `data` changes nothing but the first weeks of regime labels, which then have more history."""
    from cs2trader.strategies.registry import REGISTRY

    settings = settings or load_settings()
    strategy = REGISTRY[spec.strategy].factory(spec.params)
    if data is None:
        data = load_market(store, spec.start, spec.end, settings)
    days = [d for d in data.days if spec.start <= d <= spec.end]
    if not days:
        raise Cs2Error(f"no bars between {spec.start} and {spec.end}: nothing to run on")
    book = Book(spec.capital)
    last: dict[str, Mapping[str, Any]] = {}
    trades: list[Fill] = []
    equity: list[EquityPoint] = []
    pending: list[Order] = []
    last_sale: date | None = None
    for i, t in enumerate(days):
        today = data.by_day[t]
        cost = cost_at(spec.costs, t)
        trades += (done := apply_fills(pending, today, book, cost, spec.fills))
        if sold := [f.t for f in done if f.side == "sell" and f.qty_filled]:
            last_sale = max(sold)
        held = {l.market_hash_name for l in book.lots}
        if held:
            last.update((r["market_hash_name"], r) for r in
                        today.filter(pl.col("market_hash_name").is_in(held)).iter_rows(named=True))
        equity.append(_equity(t, book, last, cost, spec.fills))
        next_t = days[i + 1] if i + 1 < len(days) else t + timedelta(days=1)
        pending = strategy.decide(BarMarket(data, t), BookView(book, next_t))
        for o in pending:
            if o.qty <= 0:
                raise InvariantViolation(f"strategy {spec.strategy} ordered {o}")
    data_version = store.data_version()
    spec_json = json.dumps(asdict(spec), default=str, sort_keys=True)
    # Universe and regime settings change what strategies see, so they are part of the run identity.
    view_json = json.dumps([asdict(settings.universe), asdict(settings.regimes), events_hash()], sort_keys=True)
    run_id = hashlib.sha256((spec_json + view_json + data_version).encode()).hexdigest()[:16]
    return RunResult(
        run_id, spec, data_version,
        pl.DataFrame([asdict(f) for f in trades], schema=FILL_SCHEMA),
        pl.DataFrame([asdict(e) for e in equity]),
        last_sale + timedelta(days=cost_at(spec.costs, last_sale).cashout_hold_days) if last_sale else None,
    )
