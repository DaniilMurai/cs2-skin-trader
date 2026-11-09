import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

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
