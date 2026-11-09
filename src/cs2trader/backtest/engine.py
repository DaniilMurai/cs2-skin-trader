import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from cs2trader.errors import InvariantViolation, LookAheadError
from cs2trader.models import CostModel, EquityPoint, Fill, FillModel, Lot, Order, RunResult, RunSpec
from cs2trader.store import Store


@dataclass
class Book:
    balance: float
    lots: list[Lot] = field(default_factory=list)
    fees_paid: float = 0.0

    def sellable(self, name: str, on: date | datetime) -> int:
        return sum(l.qty for l in self.lots if l.market_hash_name == name and l.unlocks <= on)


class _Market:
    def __init__(self, bars: pl.DataFrame, now: date) -> None:
        self._bars = bars
        self.now = now

    def history(self, lookback: int) -> pl.DataFrame:
        lo = self.now - timedelta(days=lookback)
        return self._bars.filter((pl.col("t") > lo) & (pl.col("t") <= self.now))

    def universe(self) -> list[str]:
        return self._bars.filter(pl.col("t") == self.now)["market_hash_name"].to_list()

    def at(self, t: date) -> pl.DataFrame:
        if t > self.now:
            raise LookAheadError(f"asked for {t} at {self.now}")
        return self._bars.filter(pl.col("t") == t)


class _Portfolio:
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
    t = bar["t"][0]
    rows = {r["market_hash_name"]: r for r in bar.iter_rows(named=True)}
    out: list[Fill] = []
    for o in sorted(orders, key=lambda o: o.side != "sell"):
        if o.qty <= 0:
            raise InvariantViolation(f"order quantity must be positive: {o}")
        r = rows.get(o.market_hash_name)
        if r is None:
            out.append(Fill(t, o.market_hash_name, o.side, o.qty, 0, 0.0, 0.0, "no_data"))
            continue
        cap = _cap(r["volume"], fills.participation_cap)
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
                # The affordability epsilon may overshoot by float dust; clamp it, not real debt.
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


def run(spec: RunSpec, store: Store) -> RunResult:
    from cs2trader.strategies.registry import REGISTRY

    strategy = REGISTRY[spec.strategy].factory(spec.params)
    bars = store.daily_bars(spec.start, spec.end)
    days = sorted(set(bars["t"].to_list()))
    book = Book(spec.capital)
    last: dict[str, Mapping[str, Any]] = {}
    trades: list[Fill] = []
    equity: list[EquityPoint] = []
    pending: list[Order] = []
    last_sale: date | None = None
    for i, t in enumerate(days):
        today = bars.filter(pl.col("t") == t)
        cost = cost_at(spec.costs, t)
        trades += (done := apply_fills(pending, today, book, cost, spec.fills))
        if sold := [f.t for f in done if f.side == "sell" and f.qty_filled]:
            last_sale = max(sold)
        last.update((r["market_hash_name"], r) for r in today.iter_rows(named=True))
        equity.append(_equity(t, book, last, cost, spec.fills))
        next_t = days[i + 1] if i + 1 < len(days) else t + timedelta(days=1)
        pending = strategy.decide(_Market(bars, t), _Portfolio(book, next_t))
    data_version = store.data_version()
    spec_json = json.dumps(asdict(spec), default=str, sort_keys=True)
    run_id = hashlib.sha256((spec_json + data_version).encode()).hexdigest()[:16]
    return RunResult(
        run_id, spec, data_version,
        pl.DataFrame([asdict(f) for f in trades]) if trades else pl.DataFrame(),
        pl.DataFrame([asdict(e) for e in equity]),
        last_sale + timedelta(days=cost_at(spec.costs, last_sale).cashout_hold_days) if last_sale else None,
    )
