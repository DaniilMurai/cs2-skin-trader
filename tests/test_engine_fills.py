from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.backtest.engine import Book, apply_fills, cost_at, run
from cs2trader.errors import InvariantViolation
from cs2trader.models import CostModel, FillModel, Order, RunSpec
from cs2trader.strategies import registry

D0 = date(2024, 1, 1)
COST = CostModel(valid_from=date(2013, 1, 1))
NO_SPREAD = FillModel(participation_cap=1.0, spread_penalty=0.0)
ITEM = "★ Karambit | Doppler (Factory New)"


def bar(t: date, price: float = 40.0, volume: int = 100, name: str = ITEM, **extra) -> pl.DataFrame:
    return pl.DataFrame([{"market_hash_name": name, "t": t, "price": price, "volume": volume,
                          "ask": None, "bid": None, **extra}])


def close(a: float, b: float) -> bool:
    return abs(a - b) < 0.005


def test_ac7_lock_fee_and_same_day_reuse():
    book = Book(100.0)
    [buy] = apply_fills([Order(ITEM, "buy", 1)], bar(D0), book, COST, NO_SPREAD)
    assert buy.qty_filled == 1 and close(book.balance, 60.0)

    for day in range(1, 7):
        [f] = apply_fills([Order(ITEM, "sell", 1)], bar(D0 + timedelta(day)), book, COST, NO_SPREAD)
        assert f.qty_filled == 0 and f.reason == "locked"
    assert close(book.balance, 60.0)

    day7 = bar(D0 + timedelta(7))
    sell, rebuy = apply_fills([Order(ITEM, "buy", 1), Order(ITEM, "sell", 1)], day7, book, COST, NO_SPREAD)
    assert sell.side == "sell" and sell.qty_filled == 1
    assert close(sell.fee, 2.0) and close(sell.price, 40.0)
    assert rebuy.side == "buy" and rebuy.qty_filled == 1
    assert close(book.balance, 60.0 + 38.0 - 40.0)
    assert close(book.fees_paid, 2.0)


def test_ac7_cashout_after_hold(monkeypatch):
    class OneRoundTrip:
        name = "round_trip"
        params: dict = {}

        def __init__(self, params):
            self.bought = False

        def decide(self, market, book):
            if not self.bought:
                self.bought = True
                return [Order(ITEM, "buy", 1)]
            return [Order(ITEM, "sell", 1)] if book.sellable(ITEM) else []

    class FakeStore:
        def daily_bars(self, start, end):
            return pl.concat([bar(start + timedelta(d)) for d in range((end - start).days + 1)])

        def data_version(self):
            return "fake"

        def items_frame(self):
            return pl.DataFrame(schema={"name": pl.String, "category": pl.String})

    monkeypatch.setitem(registry.REGISTRY, "round_trip",
                        registry.StrategyEntry("round_trip", "benchmark", OneRoundTrip, {}, ""))
    spec = RunSpec("round_trip", {}, 100.0, D0, D0 + timedelta(20), fills=NO_SPREAD)
    res = run(spec, FakeStore())
    trades = res.trades.filter(pl.col("qty_filled") > 0)
    buy_t, sell_t = trades["t"].to_list()
    assert buy_t == D0 + timedelta(1)          # decided at day 0 close, filled next bar
    assert sell_t == buy_t + timedelta(7)
    assert res.cashout_available == sell_t + timedelta(8)
    last = res.equity.row(-1, named=True)
    assert close(last["balance"], 98.0)
    assert close(last["equity_cash"], 98.0 * 0.95)
    assert close(last["equity_steam"], 98.0 * 0.98)


def test_ac8_volume_cap_and_spread():
    fills = FillModel(participation_cap=0.20, spread_penalty=0.03)
    book = Book(10_000.0)
    [f] = apply_fills([Order(ITEM, "buy", 5)], bar(D0, volume=10), book, COST, fills)
    assert f.qty_filled == 2 and f.reason == "volume_cap"
    assert close(f.price, 40.0 * 1.03) and close(book.balance, 10_000.0 - 2 * 41.2)
    assert sum(l.qty for l in book.lots) == 2

    later = D0 + timedelta(7)
    [s] = apply_fills([Order(ITEM, "sell", 2)], bar(later, volume=10), book, COST, fills)
    assert s.qty_filled == 2 and s.price <= 40.0 * (1 - 0.03) + 1e-12
    assert close(s.fee, 2 * 38.8 * 0.05)


def test_volume_cap_is_per_item_and_day_across_orders():
    fills = FillModel(participation_cap=0.20, spread_penalty=0.0)
    book = Book(10_000.0)
    fs = apply_fills([Order(ITEM, "buy", 2)] * 3, bar(D0, volume=10), book, COST, fills)
    assert [f.qty_filled for f in fs] == [2, 0, 0]
    assert sum(l.qty for l in book.lots) == 2


def test_cap_is_not_lost_to_float_error():
    book = Book(1e6)
    [f] = apply_fills([Order(ITEM, "buy", 100)], bar(D0, price=1.0, volume=100),
                      book, COST, FillModel(participation_cap=0.29, spread_penalty=0.0))
    assert f.qty_filled == 29


def test_no_cash_and_no_position_and_no_data():
    book = Book(50.0)
    sell, buy, missing = apply_fills(
        [Order(ITEM, "buy", 2), Order(ITEM, "sell", 1), Order("Recoil Case", "buy", 1)],
        bar(D0), book, COST, NO_SPREAD)
    assert sell.reason == "no_position" and sell.qty_filled == 0
    assert buy.reason == "no_cash" and buy.qty_filled == 1
    assert missing.reason == "no_data"


def test_limit_orders():
    book = Book(1000.0)
    no, yes = apply_fills([Order(ITEM, "buy", 1, limit=39.0), Order(ITEM, "buy", 1, limit=40.0)],
                          bar(D0), book, COST, NO_SPREAD)
    assert no.reason == "limit" and no.qty_filled == 0
    assert yes.qty_filled == 1
    [s] = apply_fills([Order(ITEM, "sell", 1, limit=41.0)], bar(D0 + timedelta(7)), book, COST, NO_SPREAD)
    assert s.reason == "limit" and s.qty_filled == 0


def test_ratio_by_category_with_fallback():
    fills = FillModel(participation_cap=1.0, spread_penalty=0.0, ratio={"knife": 0.9}, ratio_fallback=1.1)
    book = Book(1000.0)
    [k] = apply_fills([Order(ITEM, "buy", 1)], bar(D0, category="knife"), book, COST, fills)
    [c] = apply_fills([Order("Recoil Case", "buy", 1)], bar(D0, name="Recoil Case", price=1.0),
                      book, COST, fills)
    assert close(k.price, 36.0) and close(c.price, 1.1)


def test_cost_schedule_by_date():
    old = CostModel(valid_from=date(2013, 1, 1), lock_days=0)
    new = CostModel(valid_from=date(2025, 7, 1))
    assert cost_at((new, old), date(2024, 5, 1)) is old
    assert cost_at((old, new), date(2025, 7, 1)) is new
    with pytest.raises(InvariantViolation):
        cost_at((new,), date(2020, 1, 1))


def test_non_positive_order_is_a_bug():
    with pytest.raises(InvariantViolation):
        apply_fills([Order(ITEM, "buy", 0)], bar(D0), Book(100.0), COST, NO_SPREAD)
