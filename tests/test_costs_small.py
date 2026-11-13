import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from cs2trader.backtest.costs import round_price, sale_proceeds, unit_fee
from cs2trader.backtest.engine import Book, _equity, apply_fills
from cs2trader.backtest.runs import load_spec
from cs2trader.config import Settings
from cs2trader.errors import InvariantViolation
from cs2trader.evaluate import make_spec
from cs2trader.models import (
    Category,
    CostModel,
    FillModel,
    FxRate,
    ItemInfo,
    Lot,
    Order,
    SteamPoint,
)
from cs2trader.store import Store

D0 = date(2024, 1, 1)
# Defaults: 5% seller fee, no buyer fee, price step 0.001, smallest price 0.005, minimum fee 0.01
COST = CostModel(valid_from=date(2013, 1, 1))
NO_SPREAD = FillModel(participation_cap=1.0, spread_penalty=0.0)
ITEM = "Recoil Case"


def bar(price: float, volume: int = 1000, **extra) -> pl.DataFrame:
    return pl.DataFrame([{"market_hash_name": ITEM, "t": D0, "price": price, "volume": volume,
                          "ask": None, "bid": None, **extra}],
                        schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def holding(qty: int, balance: float = 0.0) -> Book:
    return Book(balance, [Lot(ITEM, qty, 0.0, D0 - timedelta(30), D0 - timedelta(23))])


def test_minimum_fee_binds_on_a_cheap_sale():
    # 5% of 0.12 is 0.006, under the 0.01 minimum: 3 items pay 3 x 0.01, not 3 x 0.006
    book = holding(3)
    [f] = apply_fills([Order(ITEM, "sell", 3)], bar(0.12), book, COST, NO_SPREAD)
    assert f.qty_filled == 3 and f.price == pytest.approx(0.12)
    assert f.fee == pytest.approx(0.03)
    assert book.balance == pytest.approx(3 * 0.12 - 0.03)
    assert book.fees_paid == pytest.approx(0.03)


def test_minimum_fee_does_not_bind_on_a_dear_sale():
    book = holding(1)
    [f] = apply_fills([Order(ITEM, "sell", 1)], bar(40.0), book, COST, NO_SPREAD)
    assert f.fee == pytest.approx(2.0) and book.balance == pytest.approx(38.0)


def test_price_rounds_against_the_trader_on_both_sides():
    # 0.2504 is not a listable price: a buy pays 0.251, a sale gets 0.250
    book = Book(50.0)
    [buy] = apply_fills([Order(ITEM, "buy", 10)], bar(0.2504), book, COST, NO_SPREAD)
    assert buy.price == pytest.approx(0.251) and buy.fee == 0.0
    assert book.balance == pytest.approx(50.0 - 2.51)

    seller = holding(10)
    [sell] = apply_fills([Order(ITEM, "sell", 10)], bar(0.2504), seller, COST, NO_SPREAD)
    assert sell.price == pytest.approx(0.250)
    # 5% of 0.250 is 0.0125, charged as 0.013 per item
    assert sell.fee == pytest.approx(0.13)
    assert seller.balance == pytest.approx(2.50 - 0.13)


def test_rounding_keeps_grid_prices_and_follows_the_spread():
    assert round_price(0.29, COST, "buy") == pytest.approx(0.29) == round_price(0.29, COST, "sell")
    # 0.333 with a 3% spread: 0.34299 up to 0.343, 0.32301 down to 0.323
    spread = FillModel(participation_cap=1.0, spread_penalty=0.03)
    [buy] = apply_fills([Order(ITEM, "buy", 1)], bar(0.333), Book(50.0), COST, spread)
    [sell] = apply_fills([Order(ITEM, "sell", 1)], bar(0.333), holding(1), COST, spread)
    assert buy.price == pytest.approx(0.343) and sell.price == pytest.approx(0.323)


def test_item_below_the_smallest_price_is_not_traded():
    book = Book(50.0)
    [buy] = apply_fills([Order(ITEM, "buy", 100)], bar(0.004), book, COST, NO_SPREAD)
    assert (buy.qty_filled, buy.reason, buy.fee) == (0, "min_price", 0.0)
    assert book.balance == 50.0 and not book.lots

    seller = holding(5)
    [sell] = apply_fills([Order(ITEM, "sell", 5)], bar(0.004), seller, COST, NO_SPREAD)
    assert (sell.qty_filled, sell.reason) == (0, "min_price")
    assert seller.balance == 0.0 and seller.lots[0].qty == 5
    # A holding that cannot be sold is worth nothing in the equity curve
    point = _equity(D0, seller, {ITEM: bar(0.004).row(0, named=True)}, COST, NO_SPREAD)
    assert point.holdings_value == 0.0

    # 0.005 is the smallest listed price and can be bought
    [ok] = apply_fills([Order(ITEM, "buy", 100)], bar(0.005), book, COST, NO_SPREAD)
    assert ok.qty_filled == 100 and book.balance == pytest.approx(49.5)


def test_sale_that_the_fee_would_eat_is_not_made():
    # At 0.008 the 0.01 minimum fee exceeds the price
    seller = holding(5)
    [sell] = apply_fills([Order(ITEM, "sell", 5)], bar(0.008), seller, COST, NO_SPREAD)
    assert (sell.qty_filled, sell.reason) == (0, "min_price") and seller.balance == 0.0
    assert sale_proceeds(0.008, COST) == 0.0
    assert sale_proceeds(0.12, COST) == pytest.approx(0.11)


def test_zero_bid_is_not_a_sale_on_hourly_quotes():
    seller = holding(1)
    [sell] = apply_fills([Order(ITEM, "sell", 1)], bar(0.5, ask=0.5, bid=0.0), seller, COST, NO_SPREAD,
                         quotes=True)
    assert (sell.qty_filled, sell.reason) == (0, "min_price") and seller.lots[0].qty == 1


def test_buyer_fee_uses_the_same_rounding_and_minimum():
    cost = replace(COST, buyer_fee=0.02)
    assert unit_fee(0.12, 0.02, cost) == pytest.approx(0.01)       # 0.0024, under the minimum
    assert unit_fee(1.234, 0.02, cost) == pytest.approx(0.025)     # 0.02468, up to the step
    book = Book(50.0)
    [buy] = apply_fills([Order(ITEM, "buy", 10)], bar(0.12), book, cost, NO_SPREAD)
    assert buy.fee == pytest.approx(0.10) and book.balance == pytest.approx(50.0 - 1.20 - 0.10)
    assert book.lots[0].unit_cost == pytest.approx(0.13)


def test_fifty_dollars_buys_whole_items_only():
    # 50 / 17 = 2.94: two items, never 2.94 of one
    book = Book(50.0)
    [buy] = apply_fills([Order(ITEM, "buy", 5)], bar(17.0), book, COST, NO_SPREAD)
    assert (buy.qty_filled, buy.reason) == (2, "no_cash")
    assert isinstance(buy.qty_filled, int) and book.lots[0].qty == 2
    assert book.balance == pytest.approx(16.0)

    [none] = apply_fills([Order(ITEM, "buy", 1)], bar(60.0), Book(50.0), COST, NO_SPREAD)
    assert (none.qty_filled, none.reason) == (0, "no_cash")


def test_fractional_order_is_an_engine_error():
    with pytest.raises(InvariantViolation, match="whole number"):
        apply_fills([Order(ITEM, "buy", 2.5)], bar(17.0), Book(50.0), COST, NO_SPREAD)


def test_cost_stress_scales_the_minimum_fee():
    args = ("bh_universe", {}, 50.0, D0, D0 + timedelta(30), Settings())
    assert make_spec(*args, stress=True).costs[0].min_fee == pytest.approx(0.015)
    assert make_spec(*args).costs[0].min_fee == pytest.approx(0.01)


def _seed_store(data_dir) -> None:
    """Six items from 0.03 to 30 USD with 100 days of history, so a 50 USD basket has to round down."""
    store = Store(data_dir)
    fx = FxRate(date(2024, 6, 1), "UAH", 40.0, "nbu", False)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    names = {}
    for i, usd in enumerate([0.03, 0.137, 0.74, 3.3, 11.7, 30.0]):
        name = f"Test Case {i}"
        names[name] = usd
        store.upsert_steam([SteamPoint(name, start + timedelta(days=d), usd * 40.0 * (1 + 0.002 * (d % 7)),
                                       "UAH", 50, "day") for d in range(100)], fx)
    store.upsert_items([ItemInfo(n, Category.CASE, "rule", None, None, False, False, None) for n in names])
    store.close()


def test_cli_backtest_with_capital_50_buys_whole_items(tmp_path):
    data_dir = tmp_path / "data"
    _seed_store(data_dir)
    (tmp_path / "cs2.toml").write_text('data_dir = "data"\n[universe]\nmin_price_usd = 0.01\n')
    r = subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", "backtest",
                        "--strategy", "bh_universe", "--capital", "50",
                        "--from", "2024-02-15", "--to", "2024-04-05"],
                       capture_output=True, text=True, cwd=tmp_path, check=False)
    assert r.returncode == 0, r.stderr
    [run_dir] = (data_dir / "runs").iterdir()
    trades = pl.read_parquet(run_dir / "trades.parquet")
    equity = pl.read_parquet(run_dir / "equity.parquet")
    spec = load_spec(run_dir / "spec.json")
    assert spec.capital == 50.0 and spec.costs[0].min_fee == 0.01

    buys = trades.filter((pl.col("side") == "buy") & (pl.col("qty_filled") > 0))
    assert buys.height >= 3
    assert trades.schema["qty_filled"] == pl.Int64
    # Every fill price is a listable price
    ticks = trades.filter(pl.col("qty_filled") > 0)["price"] * 1000
    assert ((ticks - ticks.round()).abs() < 1e-6).all()
    # Whole items times their price account for every cent that left the balance
    spent = (buys["qty_filled"] * buys["price"] + buys["fee"]).sum()
    assert 0 < spent <= 50.0
    assert equity["balance"][-1] == pytest.approx(50.0 - spent, abs=1e-6)


def test_spec_saved_before_the_new_fields_still_loads(tmp_path):
    old = {"strategy": "bh_universe", "params": {}, "capital": 100.0, "start": "2024-01-01", "end": "2024-02-01",
           "freq": "1d", "costs": [{"valid_from": "2013-01-01", "seller_fee": 0.05, "buyer_fee": 0.0,
                                    "withdraw_fee_cash": 0.05, "withdraw_fee_steam": 0.02, "lock_days": 7,
                                    "cashout_hold_days": 8}],
           "fills": {"participation_cap": 0.2, "spread_penalty": 0.03, "ratio": {}, "ratio_fallback": 1.0}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"spec": old}))
    assert load_spec(path).costs[0] == COST
