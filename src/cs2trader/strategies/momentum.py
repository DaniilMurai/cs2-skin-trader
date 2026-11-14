import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.market.universe import liquid_band
from cs2trader.models import Order
from cs2trader.strategies.video import prices_now_and_lag

XS_SPEC = """**xs_momentum — cross-sectional momentum.**
Every `rebalance` days, universe items are ranked by their `lookback`-day return (price now vs the
last price at or before now − `lookback` days; items without such a price are not ranked). The
target is the top `k` items by return (ties by name), each getting an equal dollar share of current
equity; an item whose one unit costs more than a share is still targeted as a single unit, bought
if the cash covers it. Held items outside the target are sold in full as soon as their units are
unlocked (checked every day until sold; a unit is never sold before its 7-day lock ends). Missing
target quantity is bought every day until the next rebalance (volume caps cancel the unfilled
part, so it is re-ordered the next day). Unit cost for sizing = price × (1 + `spread`).
"""

XS_GRID = {
    "lookback": [14, 30, 90],
    "k": [3, 10],
    "rebalance": [7, 14, 28],
}

XS_DEFAULTS = {"lookback": 30, "k": 5, "rebalance": 14, "spread": 0.03}


class XsMomentum:
    name = "xs_momentum"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**XS_DEFAULTS, **params}
        self.next_rebalance = None
        self.target: list[str] = []

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(p["lookback"] + 15)
        today = dict(hist.filter(pl.col("t") == now).select("market_hash_name", "price").iter_rows())
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty

        rebalanced = self.next_rebalance is None or now >= self.next_rebalance
        if rebalanced:
            self.next_rebalance = now + timedelta(days=p["rebalance"])
            moves = (prices_now_and_lag(hist.filter(pl.col("market_hash_name").is_in(market.universe())),
                                        now, p["lookback"])
                     .with_columns((pl.col("price") / pl.col("lag_price") - 1).alias("r"))
                     .sort(["r", "market_hash_name"], descending=[True, False]).head(p["k"]))
            self.target = moves["market_hash_name"].to_list()

        orders: list[Order] = []
        for name in held:
            if name not in self.target and name in today and (qty := book.sellable(name)):
                orders.append(Order(name, "sell", qty))
        if not self.target:
            return orders

        equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for l in book.lots())
        share = equity / len(self.target)
        cash = book.balance
        for name in self.target:
            price = today.get(name)
            if price is None:
                continue
            unit = price * (1 + p["spread"])
            want = max(1, math.floor(share / unit)) - held.get(name, 0)
            qty = min(want, math.floor(cash / unit))
            if qty >= 1:
                orders.append(Order(name, "buy", qty))
                cash -= qty * unit
        return orders


XSL_SPEC = """**xs_momentum_liquid — cross-sectional momentum on the liquid band, with a drawdown stop.**
Every `rebalance` days the candidates are the universe items inside the liquid band: last price from
`min_price` up to one equal share of equity (equity / `k`, before the `spread`), and a 30-day median
volume of at least `min_volume` a day. They are ranked by their `lookback`-day return, measured
between two 7-day median prices (the week ending now vs the week ending `lookback` days ago), so a
single odd print neither ranks an item nor lifts a penny item into the band: the recent median must
itself be at least `min_price`. Only returns above `min_return` (the cost of a round trip) qualify.
A buy carries a limit of today's price × (1 + `spread`) × (1 + `max_slip`), so it does not fill into
a next-day spike. The target is the top `k` (ties by name), each sized at one share of equity but
never more than `cap_share` of its median daily volume; with fewer than `k` qualifiers the rest stays
in cash. Held items outside the target are sold as soon as their units are unlocked; missing target
quantity is re-ordered every day until the next rebalance.

Stop: equity (cash + holdings at the latest price) is compared with its peak since the last entry.
When it falls `stop` below that peak, the target is emptied: nothing is bought and every unit is sold
on the first bar it is unlocked. Re-entry: at least `cooldown` days after the trigger, on the first
day the market regime is not falling; the peak then restarts from the equity of that day.
"""

XSL_GRID = {
    "lookback": [30, 90],
    "rebalance": [14, 28],
    "stop": [0.15, 0.30],
}

# min_price 1.0 keeps clear of the prices where the minimum fee binds (under $0.20) and of penny
# items; min_return 0.12 ≈ 3% spread paid twice + 5% seller fee.
XSL_DEFAULTS = {"lookback": 30, "rebalance": 14, "stop": 0.15, "k": 5, "min_price": 1.0,
                "min_volume": 20, "cap_share": 0.05, "min_return": 0.12, "cooldown": 28, "spread": 0.03,
                "max_slip": 0.05}


class XsMomentumLiquid:
    name = "xs_momentum_liquid"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**XSL_DEFAULTS, **params}
        self.next_rebalance = None
        self.target: dict[str, int] = {}    # name -> most units the volume cap allows
        self.marks: dict[str, float] = {}   # latest price seen for a held item
        self.peak = 0.0
        self.stopped_on = None

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        today = dict(market.history(1).filter(pl.col("t") == now).select("market_hash_name", "price").iter_rows())
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
            self.marks.setdefault(lot.market_hash_name, lot.unit_cost)
        self.marks = {n: today.get(n, self.marks[n]) for n in held}
        equity = book.balance + sum(q * self.marks[n] for n, q in held.items())

        if self.stopped_on is not None:
            if now >= self.stopped_on + timedelta(days=p["cooldown"]) and market.regime().value != "falling":
                self.stopped_on, self.peak, self.next_rebalance = None, equity, None
        else:
            self.peak = max(self.peak, equity)
            if equity <= self.peak * (1 - p["stop"]):
                self.stopped_on, self.target = now, {}

        if self.stopped_on is None and (self.next_rebalance is None or now >= self.next_rebalance):
            self.next_rebalance = now + timedelta(days=p["rebalance"])
            self.target = self._pick(market, equity / p["k"])

        orders = [Order(n, "sell", q) for n in held if n not in self.target and (q := book.sellable(n))]
        share = equity / p["k"]
        cash = book.balance
        for name, room in self.target.items():
            price = today.get(name)
            if price is None:
                continue
            unit = price * (1 + p["spread"])
            qty = min(math.floor(share / unit), room) - held.get(name, 0)
            qty = min(qty, math.floor(cash / unit))
            if qty >= 1:
                orders.append(Order(name, "buy", qty, unit * (1 + p["max_slip"])))
                cash -= qty * unit
        return orders

    def _pick(self, market, share: float) -> dict[str, int]:
        p = self.p
        now = market.now
        liq = market.liquidity()
        band = liquid_band(liq.filter(pl.col("market_hash_name").is_in(market.universe())),
                           p["min_price"], share / (1 + p["spread"]), p["min_volume"])
        hist = market.history(p["lookback"] + 15).filter(
            pl.col("market_hash_name").is_in(band["market_hash_name"].to_list()))
        lag = now - timedelta(days=p["lookback"])
        week = timedelta(days=7)
        # Weekly medians, not single bars: the old strategy's top-k was whatever printed one odd
        # price on the ranking day, bought the day after and sold once the print was gone.
        recent = (hist.filter(pl.col("t") > now - week).group_by("market_hash_name")
                  .agg(pl.col("price").median().alias("p1")))
        before = (hist.filter((pl.col("t") > lag - week) & (pl.col("t") <= lag)).group_by("market_hash_name")
                  .agg(pl.col("price").median().alias("p0")))
        top = (recent.filter(pl.col("p1") >= p["min_price"]).join(before, on="market_hash_name")
               .with_columns((pl.col("p1") / pl.col("p0") - 1).alias("r"))
               .filter(pl.col("r") > p["min_return"])
               .sort(["r", "market_hash_name"], descending=[True, False]).head(p["k"])
               .join(band.select("market_hash_name", "median_volume"), on="market_hash_name"))
        # The epsilon keeps whole caps whole, as the engine's own cap does (0.05 * 20 in floats).
        return {n: math.floor(v * p["cap_share"] + 1e-9)
                for n, v in zip(top["market_hash_name"].to_list(), top["median_volume"].to_list())}
