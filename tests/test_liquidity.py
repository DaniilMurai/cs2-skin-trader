from datetime import date, timedelta

import polars as pl
import pytest
from test_forward import PINNED
from test_lookahead import D0, FakeStore, synthetic_bars
from test_walk_forward import Toy

from cs2trader.backtest.engine import Book, apply_fills, run
from cs2trader.config import Settings, UniverseSettings, load_settings
from cs2trader.errors import InvariantViolation
from cs2trader.evaluate import make_spec
from cs2trader.market.universe import liquid_band, liquidity, universe
from cs2trader.models import CostModel, FillModel, Lot, Order, RunSpec
from cs2trader.strategies import registry

ON = date(2024, 6, 30)
COST = CostModel(valid_from=date(2013, 1, 1))
# 5% of a median of 100 sales a day = 5 units; the bar's own volume never binds in these cases
CAPPED = FillModel(participation_cap=1.0, spread_penalty=0.0, position_cap=0.05)
MEDIANS = {"AK": 100.0, "thin": 19.0}


def item(name, price=1.0, volume=10, start=ON - timedelta(days=100), end=ON):
    ts = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return pl.DataFrame({"market_hash_name": name, "t": ts, "price": [float(price)] * len(ts),
                         "volume": [volume] * len(ts)}).with_columns(pl.col("volume").cast(pl.Int64))


def bar(name="AK", price=1.0, volume=1000, t=ON):
    return pl.DataFrame([{"market_hash_name": name, "t": t, "price": price, "volume": volume,
                          "ask": None, "bid": None}], schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def holding(qty: int, balance: float = 100.0) -> Book:
    old = ON - timedelta(days=30)
    return Book(balance, [Lot("AK", qty, 1.0, old, old + timedelta(days=7))] if qty else [])


# --- universe: price band and volume floor ---

BARS = pl.concat([item("cheap", 0.40), item("low edge", 0.50), item("in", 2.0), item("high edge", 5.0),
                  item("dear", 30.0), item("thin", 2.0, volume=4)])
BAND = UniverseSettings(min_history_days=30, min_median_volume=5, min_price_usd=0.50, max_price_usd=5.0)


def test_item_outside_the_price_band_or_under_the_volume_floor_is_not_in_the_universe():
    assert universe(BARS, ON, BAND) == ["high edge", "in", "low edge"]


def test_without_an_upper_price_the_universe_is_the_old_one():
    old = UniverseSettings(min_history_days=30, min_median_volume=5, min_price_usd=0.50)
    assert old.max_price_usd == 0.0 == UniverseSettings().max_price_usd
    assert universe(BARS, ON, old) == ["dear", "high edge", "in", "low edge"]


def test_liquidity_table_and_band_by_hand():
    # Missing days count as 0, so the median of 30 days is the mean of sorted ranks 14 and 15.
    gappy = pl.concat([item("gappy", 3.0, 8, start=ON - timedelta(days=100), end=ON - timedelta(days=30)),
                       item("gappy", 3.0, 8, start=ON - timedelta(days=15), end=ON)])
    gappy15 = gappy.filter(pl.col("t") != ON - timedelta(days=15))
    table = liquidity(pl.concat([BARS, gappy, gappy15.with_columns(pl.lit("gappy15").alias("market_hash_name"))]), ON)
    got = {r["market_hash_name"]: (r["last_price"], r["median_volume"]) for r in table.iter_rows(named=True)}
    assert got["in"] == (2.0, 10.0) and got["thin"] == (2.0, 4.0)
    assert got["gappy"] == (3.0, 8.0)      # 16 days of 8: both middle ranks are 8
    assert got["gappy15"] == (3.0, 4.0)    # 15 days of 8: middle ranks are 0 and 8
    assert liquid_band(table, 1.0, 4.0, 5.0)["market_hash_name"].to_list() == ["gappy", "in"]
    assert liquid_band(table).height == table.height


@pytest.mark.parametrize("on", [ON - timedelta(days=40), ON - timedelta(days=7)])
def test_band_floor_and_median_ignore_bars_after_the_decision_day(on):
    # After `on` every item leaves the band and its volume explodes; nothing of that may show at `on`.
    later = pl.concat([item(n, 500.0, 90_000, start=on + timedelta(days=1), end=ON + timedelta(days=60))
                       for n in ("cheap", "in", "thin", "dear")])
    past = BARS.filter(pl.col("t") <= on)
    full = pl.concat([past, later])
    assert universe(full, on, BAND) == universe(past, on, BAND) == ["high edge", "in", "low edge"]
    assert liquidity(full, on).equals(liquidity(past, on))


# --- engine: position cap ---

def test_over_cap_order_is_cut_not_filled():
    book = holding(0)
    [f] = apply_fills([Order("AK", "buy", 8)], bar(), book, COST, CAPPED, median_volume=MEDIANS)
    assert (f.qty_requested, f.qty_filled, f.reason) == (8, 5, "position_cap")
    assert book.balance == pytest.approx(95.0) and sum(l.qty for l in book.lots) == 5


@pytest.mark.parametrize("held,filled,reason", [(0, 5, "ok"), (3, 2, "position_cap"), (5, 0, "position_cap"),
                                                (9, 0, "position_cap")])
def test_the_cut_counts_units_already_held(held, filled, reason):
    book = holding(held)
    [f] = apply_fills([Order("AK", "buy", 5)], bar(), book, COST, CAPPED, median_volume=MEDIANS)
    assert (f.qty_filled, f.reason) == (filled, reason)
    assert sum(l.qty for l in book.lots) == held + filled
    assert book.balance == pytest.approx(100.0 - filled)


def test_two_orders_on_one_day_share_the_room():
    book = holding(0)
    a, b = apply_fills([Order("AK", "buy", 3), Order("AK", "buy", 4)], bar(), book, COST, CAPPED,
                       median_volume=MEDIANS)
    assert (a.qty_filled, a.reason, b.qty_filled, b.reason) == (3, "ok", 2, "position_cap")


def test_no_room_under_20_sales_a_day_or_without_a_known_median():
    # 5% of 19 is 0.95 of an item; an item missing from the table has no measured liquidity at all.
    for name in ("thin", "unknown"):
        book = Book(100.0)
        [f] = apply_fills([Order(name, "buy", 1)], bar(name), book, COST, CAPPED, median_volume=MEDIANS)
        assert (f.qty_filled, f.reason, book.balance) == (0, "position_cap", 100.0)


def test_sales_are_never_cut_and_other_limits_keep_their_reason():
    book = holding(9)
    [sell] = apply_fills([Order("AK", "sell", 9)], bar(), book, COST, CAPPED, median_volume=MEDIANS)
    assert (sell.qty_filled, sell.reason) == (9, "ok")
    # cash for 2 only: the smaller limit names the reason
    [poor] = apply_fills([Order("AK", "buy", 8)], bar(), Book(2.5), COST, CAPPED, median_volume=MEDIANS)
    assert (poor.qty_filled, poor.reason) == (2, "no_cash")
    # 20% of a 10-unit bar = 2, below the position room of 5
    both = FillModel(participation_cap=0.2, spread_penalty=0.0, position_cap=0.05)
    [vol] = apply_fills([Order("AK", "buy", 8)], bar(volume=10), Book(100.0), COST, both, median_volume=MEDIANS)
    assert (vol.qty_filled, vol.reason) == (2, "volume_cap")


def test_cap_off_by_default_and_refuses_to_run_blind():
    assert FillModel().position_cap == 0.0
    off = FillModel(participation_cap=1.0, spread_penalty=0.0)
    [f] = apply_fills([Order("AK", "buy", 8)], bar(), Book(100.0), COST, off)
    assert (f.qty_filled, f.reason) == (8, "ok")
    with pytest.raises(InvariantViolation):
        apply_fills([Order("AK", "buy", 8)], bar(), Book(100.0), COST, CAPPED)


# --- engine run: the cap uses the decision day's median ---

class BuyOnce:
    name = "buy_once"

    def __init__(self, params) -> None:
        self.params = params

    def decide(self, market, book):
        return [Order("AK", "buy", 20)] if market.now == self.params["on"] else []


def stepped_bars(later_volume: int) -> pl.DataFrame:
    # The 30 days ending at D0: 16 days of 40 then 14 days of 400 -> median 40 -> room for 2.
    # One more day of 400 makes it 15/15 -> median 220 -> room for 11.
    rows = [(D0 - timedelta(days=29 - i), 40 if i < 16 else 400) for i in range(30)]
    rows += [(D0 + timedelta(days=i), later_volume) for i in range(1, 6)]
    return pl.DataFrame([{"market_hash_name": "AK", "t": t, "price": 1.0, "volume": v, "ask": None,
                          "bid": None, "category": "rifle"} for t, v in rows],
                        schema_overrides={"ask": pl.Float64, "bid": pl.Float64})


def run_once(bars: pl.DataFrame, monkeypatch) -> pl.DataFrame:
    monkeypatch.setitem(registry.REGISTRY, "buy_once",
                        registry.StrategyEntry("buy_once", "benchmark", BuyOnce, {}, ""))
    spec = RunSpec("buy_once", {"on": D0}, 100.0, D0, D0 + timedelta(days=5), fills=CAPPED)
    return run(spec, FakeStore(bars), Settings()).trades


def test_run_cuts_by_the_median_known_on_the_decision_day(monkeypatch):
    trades = run_once(stepped_bars(400), monkeypatch)
    assert trades.select("t", "qty_requested", "qty_filled", "reason").rows() == [
        (D0 + timedelta(days=1), 20, 2, "position_cap")]


def test_run_is_unchanged_when_bars_after_the_decision_day_are_deleted(monkeypatch):
    full = stepped_bars(90_000)
    # The fill bar (D0 + 1) has to exist to fill at all; everything after it is deleted.
    cut = full.filter(pl.col("t") <= D0 + timedelta(days=1))
    assert run_once(full, monkeypatch).equals(run_once(cut, monkeypatch))
    assert run_once(full, monkeypatch)["qty_filled"].to_list() == [2]


# --- settings and run identity ---

def test_settings_reach_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert (load_settings().fills.position_cap, load_settings().universe.max_price_usd) == (0.0, 0.0)
    (tmp_path / "cs2.toml").write_text("[universe]\nmax_price_usd = 5\n[fills]\nposition_cap = 0.05\n")
    s = load_settings()
    assert s.universe.max_price_usd == 5.0
    assert make_spec("cash", {}, 50.0, D0, D0, s).fills.position_cap == 0.05


def test_default_run_id_is_the_one_pinned_before_the_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(registry.REGISTRY, "toy", registry.StrategyEntry("toy", "momentum", Toy, {}, ""))
    bars, s = synthetic_bars(7, 60), load_settings()

    def result(settings):
        spec = make_spec("toy", {"lookback": 3}, 100.0, D0, D0 + timedelta(40), settings)
        res = run(spec, FakeStore(bars), settings)
        return res.run_id, res.trades.height, round(res.equity["equity_cash"][-1], 6)

    # PINNED was written by T-55, before the price band's top and the position cap existed
    assert result(s) == PINNED
    capped = result(load_settings(**{"fills.position_cap": 0.05}))
    banded = result(load_settings(**{"universe.max_price_usd": 5.0}))
    assert len({PINNED[0], capped[0], banded[0]}) == 3


def test_medians_for_a_few_names_equal_the_full_table():
    from cs2trader.backtest.engine import load_market
    data = load_market(FakeStore(synthetic_bars(5, 120)), D0, D0 + timedelta(119), Settings())
    names = {"item 02", "item 07", "never traded"}
    for day in (D0 + timedelta(3), D0 + timedelta(77)):
        direct = data.median_volumes(day, names)
        table = data.liquidity_on(day).filter(pl.col("market_hash_name").is_in(names))
        assert direct == dict(zip(table["market_hash_name"], table["median_volume"]))
        assert set(direct) == {"item 02", "item 07"}
        assert data.median_volumes(day, names) == direct
