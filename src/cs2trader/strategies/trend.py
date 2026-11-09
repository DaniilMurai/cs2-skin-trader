import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.models import Order

TREND_SPEC = """**trend_ma — trend following with go-to-cash.**
Each bar, at the close, the `all` market index level is compared with its simple moving average
over the last `ma_days` calendar days (the current level included); nothing is traded until the index
history covers that whole window. While level > MA the strategy is invested: the `n_items` universe items
with the highest summed volume over the last 30 days are held in equal dollar amounts of current equity.
Missing quantity (volume caps leave buys unfilled) is re-ordered every bar while level > MA, so the
position is built up over several days; units already held are never sold or rotated while invested.
While level ≤ MA, every sellable unit of every held item is sold on every bar until the book is flat;
lots still inside the 7-day trade lock are sold on the bar they unlock. No shorting; cash earns nothing.
"""

TREND_GRID = {
    "ma_days": [20, 50, 100, 200],
    "n_items": [5, 20],
}

TREND_DEFAULTS = {"ma_days": 50, "n_items": 10, "spread": 0.03}

LIQUIDITY_DAYS = 30


class TrendFollow:
    name = "trend_ma"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**TREND_DEFAULTS, **params}

    def decide(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        idx = market.index("all", p["ma_days"] + 5)
        window = idx.filter(pl.col("t") > now - timedelta(days=p["ma_days"]))
        if window.is_empty() or window["t"].max() != now:
            return []
        level = window.sort("t")["level"][-1]
        if level <= window["level"].mean():
            return self._sell_all(book)
        # Too little history for the MA: stay out rather than trade on a partial average.
        if (now - window["t"].min()).days < p["ma_days"] - 5:
            return []
        return self._buy_targets(market, book)

    @staticmethod
    def _sell_all(book) -> list[Order]:
        names = sorted({l.market_hash_name for l in book.lots()})
        return [Order(n, "sell", q) for n in names if (q := book.sellable(n))]

    def _buy_targets(self, market, book) -> list[Order]:
        p = self.p
        now = market.now
        hist = market.history(LIQUIDITY_DAYS)
        if hist.is_empty():
            return []
        today = dict(hist.filter(pl.col("t") == now).select("market_hash_name", "price").iter_rows())
        uni = set(market.universe())
        liquid = (hist.filter(pl.col("market_hash_name").is_in(uni))
                  .group_by("market_hash_name").agg(pl.col("volume").sum().alias("vol"))
                  .sort(["vol", "market_hash_name"], descending=[True, False]))
        targets = [n for n in liquid["market_hash_name"] if n in today][:p["n_items"]]
        if not targets:
            return []
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
        equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for l in book.lots())
        dollars = equity / len(targets)
        cash = book.balance
        orders: list[Order] = []
        for name in targets:
            unit = today[name] * (1 + p["spread"])
            want = math.floor(dollars / unit)
            if want < 1 and unit <= dollars + cash and not held.get(name):
                want = 1
            qty = min(want - held.get(name, 0), math.floor(cash / unit))
            if qty >= 1:
                orders.append(Order(name, "buy", qty))
                cash -= qty * unit
        return orders
