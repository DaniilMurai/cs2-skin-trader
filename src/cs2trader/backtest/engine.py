import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

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


def apply_fills(orders: Sequence[Order], bar: pl.DataFrame, book: Book,
                cost: CostModel, fills: FillModel) -> list[Fill]:
    t = bar["t"][0]
    rows = {r["market_hash_name"]: r for r in bar.iter_rows(named=True)}
    out: list[Fill] = []
    for o in sorted(orders, key=lambda o: o.side != "sell"):
        r = rows.get(o.market_hash_name)
        if r is None:
            out.append(Fill(t, o.market_hash_name, o.side, o.qty, 0, 0.0, 0.0, "no_data"))
            continue
        ratio = fills.ratio.get(o.market_hash_name, fills.ratio_fallback)
        cap = math.floor(r["volume"] * fills.participation_cap)
        if o.side == "buy":
            price = r["price"] * ratio * (1 + fills.spread_penalty)
            unit = price * (1 + cost.buyer_fee)
            qty = min(o.qty, cap, math.floor(book.balance / unit))
            reason = "ok" if qty == o.qty else ("volume_cap" if qty == cap else "no_cash")
            if qty > 0:
                book.balance -= qty * unit
                book.lots.append(Lot(o.market_hash_name, qty, unit, t,
                                     t + timedelta(days=cost.lock_days)))
            fee = qty * price * cost.buyer_fee
        else:
            price = r["price"] * ratio * (1 - fills.spread_penalty)
            free = book.sellable(o.market_hash_name, t)
            qty = min(o.qty, cap, free)
            reason = "ok" if qty == o.qty else ("volume_cap" if qty == cap else "locked")
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
        if book.balance < -1e-9:
            raise InvariantViolation(f"balance {book.balance} after {o}")
        out.append(Fill(t, o.market_hash_name, o.side, o.qty, qty, price, fee, reason))
    return out


def _equity(t: date, book: Book, last_price: dict[str, float], cost: CostModel,
            fills: FillModel) -> EquityPoint:
    held = sum(
        l.qty * last_price[l.market_hash_name] * (1 - fills.spread_penalty) * (1 - cost.seller_fee)
        for l in book.lots
    )
    gross = book.balance + held
    return EquityPoint(t, book.balance, held, gross * (1 - cost.withdraw_fee_cash),
                       gross * (1 - cost.withdraw_fee_steam), book.fees_paid)


def run(spec: RunSpec, store: Store) -> RunResult:
    from cs2trader.strategies.registry import REGISTRY

    strategy = REGISTRY[spec.strategy].factory(spec.params)
    cost = spec.costs[-1]
    bars = store.daily_bars(spec.start, spec.end)
    days = sorted(set(bars["t"].to_list()))
    book = Book(spec.capital)
    last_price: dict[str, float] = {}
    trades: list[Fill] = []
    equity: list[EquityPoint] = []
    pending: list[Order] = []
    last_sale: date | None = None
    for i, t in enumerate(days):
        today = bars.filter(pl.col("t") == t)
        trades += (done := apply_fills(pending, today, book, cost, spec.fills))
        if sold := [f.t for f in done if f.side == "sell" and f.qty_filled]:
            last_sale = max(sold)
        last_price.update(zip(today["market_hash_name"], today["price"]))
        equity.append(_equity(t, book, last_price, cost, spec.fills))
        next_t = days[i + 1] if i + 1 < len(days) else t + timedelta(days=1)
        pending = strategy.decide(_Market(bars, t), _Portfolio(book, next_t))
    data_version = store.data_version()
    spec_json = json.dumps(asdict(spec), default=str, sort_keys=True)
    run_id = hashlib.sha256((spec_json + data_version).encode()).hexdigest()[:16]
    return RunResult(
        run_id, spec, data_version,
        pl.DataFrame([asdict(f) for f in trades]) if trades else pl.DataFrame(),
        pl.DataFrame([asdict(e) for e in equity]),
        last_sale + timedelta(days=cost.cashout_hold_days) if last_sale else None,
    )
