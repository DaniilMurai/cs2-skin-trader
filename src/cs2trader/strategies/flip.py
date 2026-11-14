import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.backtest.costs import round_price, sale_proceeds, unit_fee
from cs2trader.config import load_settings
from cs2trader.market.universe import liquid_band
from cs2trader.models import Order

FLIP_SPEC = """**cheap_flip — buy cheap liquid items below their usual price, sell after the 7-day lock.**
Candidates are universe items with a 30-day median volume of at least `min_volume` a day whose last
price and `entry_days`-day median price are both between `min_price` and `max_price` (5 USD). For each,
the usual price is the median over the last `ref_days` days and the entry price is the `entry_days`-day
median (at least two bars, so one odd print moves neither). A buy is a limit order at entry price ×
(1 − `discount`) × (1 + `spread`), rounded to the price step. The expected net margin is what one unit
brings when sold at the usual price × (1 − `spread`) after the seller fee, its minimum and price
rounding (the configured cost model), divided by the limit price plus the buyer fee, minus 1. Only
items with a margin above `hurdle` are ordered, best margin first, while fewer than `max_positions`
items are held; one position is equity / `max_positions` dollars (a single unit if one unit costs
more), never more than `cap_share` of the item's median daily volume. An unfilled order is cancelled
and placed again the next day if the item still qualifies. A held item is not bought again.

Exit: unlocked units are sold once the item's `entry_days`-day median price reaches the usual price
remembered at the buy, or `max_hold` days after the buy, whichever comes first. With `max_hold` 7 that
is the first bar after the lock.
"""

FLIP_GRID = {
    "hurdle": [0.05, 0.15, 0.30],
    "discount": [0.0, 0.10],
    "max_hold": [7, 60],
}

FLIP_DEFAULTS = {"hurdle": 0.15, "discount": 0.0, "max_hold": 7, "ref_days": 30, "entry_days": 3,
                 "min_price": 0.10, "max_price": 5.0, "min_volume": 20, "cap_share": 0.05,
                 "max_positions": 10, "spread": 0.03}

NAME = "market_hash_name"


class CheapFlip:
    name = "cheap_flip"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**FLIP_DEFAULTS, **params}
        # A strategy is not handed the run's cost model; the engine builds its own from the same
        # file, so the margin here is net of the fees, minimum fee and rounding the fills will pay.
        self.cost = load_settings().costs
        self.target: dict[str, float] = {}

    def margin(self, limit: float, usual: float) -> float:
        paid = limit + unit_fee(limit, self.cost.buyer_fee, self.cost)
        return sale_proceeds(usual * (1 - self.p["spread"]), self.cost) / paid - 1

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(p["ref_days"])
        fresh = pl.col("t") > now - timedelta(days=p["entry_days"])

        held: dict[str, list] = {}
        for lot in book.lots():
            held.setdefault(lot.market_hash_name, []).append(lot)
        marks = dict(hist.filter(pl.col(NAME).is_in(list(held)) & fresh).group_by(NAME)
                     .agg(pl.col("price").median()).iter_rows())
        self.target = {n: t for n, t in self.target.items() if n in held}

        orders: list[Order] = []
        for name in sorted(held):
            qty = book.sellable(name)
            if not qty:
                continue
            age = (now - min(l.bought for l in held[name])).days
            if age >= p["max_hold"] or marks.get(name, 0.0) >= self.target.get(name, math.inf):
                orders.append(Order(name, "sell", qty))

        free = p["max_positions"] - len(held)
        if free <= 0:
            return orders
        band = liquid_band(market.liquidity().filter(pl.col(NAME).is_in(market.universe())),
                           p["min_price"], p["max_price"], p["min_volume"])
        band = band.filter(~pl.col(NAME).is_in(list(held)))
        scale = (1 - p["discount"]) * (1 + p["spread"])
        stats = (hist.filter(pl.col(NAME).is_in(band[NAME].to_list()))
                 .group_by(NAME)
                 .agg(pl.col("price").median().alias("usual"),
                      pl.col("price").filter(fresh).median().alias("entry"),
                      fresh.sum().alias("n_entry"), pl.len().alias("n"))
                 # Costs only lower the margin, so anything under the gross hurdle is dropped
                 # before the exact per-item cost calculation.
                 .filter((pl.col("n_entry") >= 2) & (pl.col("n") >= p["ref_days"] / 2)
                         & pl.col("entry").is_between(p["min_price"], p["max_price"])
                         & (pl.col("usual") > pl.col("entry") * scale * (1 + p["hurdle"])))
                 .join(band.select(NAME, "median_volume"), on=NAME))

        picks = []
        for name, usual, entry, volume in stats.select(NAME, "usual", "entry", "median_volume").iter_rows():
            limit = round_price(entry * scale, self.cost, "buy")
            m = self.margin(limit, usual)
            if m > p["hurdle"]:
                picks.append((-m, name, limit, usual, volume))

        equity = book.balance + sum(l.qty * marks.get(n, l.unit_cost) for n, lots in held.items() for l in lots)
        slot = equity / p["max_positions"]
        cash = book.balance
        for _, name, limit, usual, volume in sorted(picks):
            if free == 0:
                break
            unit = limit + unit_fee(limit, self.cost.buyer_fee, self.cost)
            # The epsilon keeps whole caps whole, as the engine's own cap does (0.05 * 20 in floats).
            room = math.floor(volume * p["cap_share"] + 1e-9)
            qty = min(max(math.floor(slot / unit), 1), room, math.floor(cash / unit + 1e-9))
            if qty < 1:
                continue
            orders.append(Order(name, "buy", qty, limit))
            self.target[name] = usual
            cash -= qty * unit
            free -= 1
        return orders
