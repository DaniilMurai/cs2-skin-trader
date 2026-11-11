import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.models import Order

V1_SPEC = """**v1_dip_knives — "falling market" (our reading of the video's screenshot).**
Candidates: items in the point-in-time universe whose category is knife or glove.
Entry: at a bar's close, an item's 7-day return (price now vs the last price at least 7 days
earlier) is at or below −`drop_7d`; candidates are ranked by the deepest drop and bought while
free position slots (`max_positions`) and cash allow, one slot = current equity / `max_positions`;
an item whose one unit costs more than a slot is still bought as a single unit if the cash covers
it (so $100 can hold one knife). An item already held, or sold in the last `cooldown` days, is not
bought — without the cooldown a stop-loss exit is re-bought at once, since the 7-day drop persists.
Exit (checked on unlocked units only — the 7-day trade lock makes earlier exits impossible):
price ≥ entry × (1 + `take_profit`), or held ≥ `max_hold` days, or — the video's "protection" —
price ≤ entry × (1 − `stop_loss`) when `stop_loss` is set. Entry = the lot's unit cost.
"""

V1_GRID = {
    "drop_7d": [0.05, 0.10, 0.15],
    "max_positions": [3, 5],
    "take_profit": [0.05, 0.10],
    "max_hold": [14, 30],
    "stop_loss": [None, 0.10, 0.20],
}

V1_DEFAULTS = {"drop_7d": 0.10, "max_positions": 3, "take_profit": 0.10, "max_hold": 30, "stop_loss": None,
               "spread": 0.03, "cooldown": 7}

KNIVES_GLOVES = ("knife", "glove")


def prices_now_and_lag(hist: pl.DataFrame, now, lag_days: int) -> pl.DataFrame:
    """market_hash_name, price (bar on `now`), lag_price (last bar ≤ now − lag_days)."""
    today = hist.filter(pl.col("t") == now).select("market_hash_name", "price")
    lag = (hist.filter(pl.col("t") <= now - timedelta(days=lag_days)).sort("t")
           .group_by("market_hash_name").agg(pl.col("price").last().alias("lag_price")))
    return today.join(lag, on="market_hash_name", how="inner")


class DipKnives:
    name = "v1_dip_knives"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**V1_DEFAULTS, **params}
        self.exited: dict[str, object] = {}

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(p["max_hold"] + 15)
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
            entry = sum(l.unit_cost * l.qty for l in lots) / sum(l.qty for l in lots)
            oldest = min(l.bought for l in lots)
            exit_ = (price >= entry * (1 + p["take_profit"])
                     or (now - oldest).days >= p["max_hold"]
                     or (p["stop_loss"] is not None and price <= entry * (1 - p["stop_loss"])))
            if exit_:
                orders.append(Order(name, "sell", qty))
                self.exited[name] = now

        free = p["max_positions"] - len(held)
        if free <= 0:
            return orders
        cats = market.items().filter(pl.col("category").is_in(KNIVES_GLOVES))["name"]
        cooling = {n for n, t in self.exited.items() if (now - t).days < p["cooldown"]}
        eligible = (set(market.universe()) & set(cats.to_list())) - cooling
        moves = (prices_now_and_lag(hist.filter(pl.col("market_hash_name").is_in(eligible)), now, 7)
                 .with_columns((pl.col("price") / pl.col("lag_price") - 1).alias("r7"))
                 .filter((pl.col("r7") <= -p["drop_7d"]) & ~pl.col("market_hash_name").is_in(list(held)))
                 .sort("r7", "market_hash_name"))
        equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for ls in held.values()
                                    for l in ls)
        slot = equity / p["max_positions"]
        cash = book.balance
        for name, price in moves.select("market_hash_name", "price").iter_rows():
            if free == 0:
                break
            unit = price * (1 + p["spread"])
            qty = math.floor(min(slot, cash) / unit)
            if qty < 1 and unit <= cash:
                qty = 1
            if qty < 1:
                continue
            orders.append(Order(name, "buy", qty))
            cash -= qty * unit
            free -= 1
        return orders


V2_SPEC = """**v2_category_momentum — "rising market" (our reading of the video's screenshot).**
Every `rebalance_days` bars: each category's index growth over `window` days is measured (the
point-in-time category index: chain-linked median daily return); the `top_k` categories with
growth > 0 are selected ("unknown" and "other" never are). Exposure grows with the strength of the
rise — "buy more actively while they rise": invested share = min(1, growth / `full_growth`) for
each selected category, applied to its 1/`top_k` share of equity. Inside a category, the
`items_per_cat` universe items with the highest own `window`-day return are held in equal
dollar amounts. The target is fixed until the next rebalance; every bar the book is moved toward it
(unfilled buys are retried, held items outside the target are sold as soon as their lock allows).
"""

V2_GRID = {
    "window": [14, 30],
    "top_k": [1, 2],
    "full_growth": [0.10, 0.20],
    "rebalance_days": [7, 14],
}

V2_DEFAULTS = {"window": 30, "top_k": 2, "full_growth": 0.10, "rebalance_days": 7, "items_per_cat": 3,
               "spread": 0.03}

NEVER_SELECTED = ("unknown", "other")


class CategoryMomentum:
    name = "v2_category_momentum"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**V2_DEFAULTS, **params}
        self.next_rebalance = None
        self.target: dict[str, float] = {}

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(p["window"] + 15)
        today = dict(hist.filter(pl.col("t") == now).select("market_hash_name", "price").iter_rows())
        if self.next_rebalance is None or now >= self.next_rebalance:
            self.next_rebalance = now + timedelta(days=p["rebalance_days"])
            self.target = self._target(market, hist, book, today)

        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
        orders: list[Order] = []
        for name in held:
            if name not in self.target and (qty := book.sellable(name)) and name in today:
                orders.append(Order(name, "sell", qty))
        cash = book.balance
        for name, dollars in sorted(self.target.items()):
            price = today.get(name)
            if price is None:
                continue
            unit = price * (1 + p["spread"])
            want = math.floor(dollars / unit) - held.get(name, 0)
            qty = min(want, math.floor(cash / unit))
            if qty >= 1:
                orders.append(Order(name, "buy", qty))
                cash -= qty * unit
        return orders

    def _target(self, market, hist, book, today) -> dict[str, float]:
        p = self.p
        now = market.now
        idx = market.index("category", p["window"] + 15)
        growth = (idx.filter(pl.col("t") == now).select("group", pl.col("level").alias("now"))
                  .join(idx.filter(pl.col("t") <= now - timedelta(days=p["window"])).sort("t")
                        .group_by("group").agg(pl.col("level").last().alias("then")), on="group")
                  .with_columns((pl.col("now") / pl.col("then") - 1).alias("g"))
                  .filter((pl.col("g") > 0) & ~pl.col("group").is_in(NEVER_SELECTED))
                  .sort(["g", "group"], descending=[True, False]).head(p["top_k"]))
        if growth.is_empty():
            return {}
        equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for l in book.lots())
        uni = set(market.universe())
        cats = market.items().select(pl.col("name").alias("market_hash_name"), "category")
        moves = (prices_now_and_lag(hist.filter(pl.col("market_hash_name").is_in(uni)), now, p["window"])
                 .join(cats, on="market_hash_name")
                 .with_columns((pl.col("price") / pl.col("lag_price") - 1).alias("r")))
        target: dict[str, float] = {}
        for cat, g in growth.select("group", "g").iter_rows():
            share = equity / p["top_k"] * min(1.0, g / p["full_growth"])
            picks = (moves.filter(pl.col("category") == cat)
                     .sort(["r", "market_hash_name"], descending=[True, False]).head(p["items_per_cat"]))
            for name in picks["market_hash_name"]:
                target[name] = share / picks.height
        return target
