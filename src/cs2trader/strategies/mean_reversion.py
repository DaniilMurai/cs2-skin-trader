import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.models import Order

MR_SPEC = """**mean_reversion — buy statistical overshoots below the recent mean.**
For each item in the point-in-time universe the mean and standard deviation of its daily prices over
the last `lookback` days (bars in (now − lookback, now], at least lookback/2 of them) give
z = (price − mean) / std. An item with z ≤ −`z` is a buy candidate; candidates are ranked by the
deepest z and bought while free position slots (`max_positions`) and cash allow, one slot = current
equity / `max_positions` (a single unit if one unit costs more than a slot but the cash covers it).
The mean at the moment of entry is remembered per item. Exit (unlocked units only, because of the
7-day trade lock): price ≥ that entry-time mean, or held ≥ `max_hold` days. An item sold in the last
`cooldown` days is not bought again.
"""

MR_GRID = {
    "z": [1.5, 2.0, 3.0],
    "lookback": [14, 30],
}

MR_DEFAULTS = {"z": 2.0, "lookback": 30, "max_positions": 5, "max_hold": 30, "cooldown": 7, "spread": 0.03}


class MeanReversion:
    name = "mean_reversion"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**MR_DEFAULTS, **params}
        self.exited: dict[str, object] = {}
        self.entry_mean: dict[str, float] = {}

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(p["lookback"])
        today = dict(hist.filter(pl.col("t") == now).select("market_hash_name", "price").iter_rows())
        orders: list[Order] = []

        held: dict[str, list] = {}
        for lot in book.lots():
            held.setdefault(lot.market_hash_name, []).append(lot)
        for name, lots in held.items():
            qty = book.sellable(name)
            price = today.get(name)
            if not qty or price is None:
                continue
            target = self.entry_mean.get(name)
            if target is None:
                target = sum(l.unit_cost * l.qty for l in lots) / sum(l.qty for l in lots)
            oldest = min(l.bought for l in lots)
            if price >= target or (now - oldest).days >= p["max_hold"]:
                orders.append(Order(name, "sell", qty))
                self.exited[name] = now

        free = p["max_positions"] - len(held)
        if free <= 0:
            return orders
        cooling = {n for n, t in self.exited.items() if (now - t).days < p["cooldown"]}
        eligible = set(market.universe()) - cooling - set(held)
        stats = (hist.filter(pl.col("market_hash_name").is_in(eligible))
                 .group_by("market_hash_name")
                 .agg(pl.col("price").mean().alias("mean"), pl.col("price").std(ddof=0).alias("std"),
                      pl.len().alias("n"))
                 .filter((pl.col("n") >= p["lookback"] / 2) & (pl.col("std") > 0))
                 .join(pl.DataFrame({"market_hash_name": list(today), "price": list(today.values())},
                                    schema={"market_hash_name": pl.String, "price": pl.Float64}),
                       on="market_hash_name")
                 .with_columns(((pl.col("price") - pl.col("mean")) / pl.col("std")).alias("zscore"))
                 .filter(pl.col("zscore") <= -p["z"])
                 .sort("zscore", "market_hash_name"))

        equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for ls in held.values()
                                    for l in ls)
        slot = equity / p["max_positions"]
        cash = book.balance
        for name, price, mean in stats.select("market_hash_name", "price", "mean").iter_rows():
            if free == 0:
                break
            unit = price * (1 + p["spread"])
            qty = math.floor(min(slot, cash) / unit)
            if qty < 1 and unit <= cash:
                qty = 1
            if qty < 1:
                continue
            orders.append(Order(name, "buy", qty))
            self.entry_mean[name] = mean
            cash -= qty * unit
            free -= 1
        return orders
