import math
from collections.abc import Mapping
from typing import Any

import polars as pl

from cs2trader.models import Order
from cs2trader.strategies.mean_reversion import MR_DEFAULTS, MeanReversion

QUALITY_SPEC = """**quality_basket — hold liquid items with growing volume, rebalance rarely.**
Every `rebalance_days` bars the point-in-time universe is filtered to items with a price bar on at
least 120 of the last 180 days and a summed volume over the last 60 days at least that of the 60 days
before; the `n_items` with the highest summed 180-day volume are the targets. Each bar the missing
quantity of every target is bought (equal dollars of equity, limited by cash), and units of items that
dropped out of the targets are sold once unlocked. A current target stays while it ranks within the top
`2 x n_items`, so ranking noise does not cause trades. Nothing else is ever sold, so turnover stays low.
"""
QUALITY_GRID = {"rebalance_days": [60, 120], "n_items": [5, 15]}
QUALITY_DEFAULTS = {"rebalance_days": 90, "n_items": 10, "spread": 0.03}

CASEDIP_SPEC = """**case_dip — buy cases while the market is falling, sell when it rises.**
While the regime label is `falling`, the `n_cases` case-category items of the point-in-time universe
with the highest summed volume over the last 30 days are held in equal dollars of equity (missing
quantity is re-ordered every bar). While the label is `rising`, unlocked units held at least `min_hold`
days are sold. In `flat` nothing is traded.
"""
CASEDIP_GRID = {"n_cases": [3, 8], "min_hold": [14, 60]}
CASEDIP_DEFAULTS = {"n_cases": 5, "min_hold": 14, "spread": 0.03}

CHEAP_SPEC = """**cheap_reversion — mean_reversion restricted to cheap items with a large expected move.**
The same entries and exits as mean_reversion, but an item is a candidate only if its price is at most
`max_price` USD and the mean it should revert to is at least `min_move` above the price. Round trips on
market.csgo cost about 11%, so a smaller expected move cannot pay for itself.
"""
CHEAP_GRID = {"z": [2.0, 3.0], "min_move": [0.15, 0.25]}
CHEAP_DEFAULTS = {**MR_DEFAULTS, "max_price": 20.0, "min_move": 0.15}


def _held(book) -> dict[str, int]:
    held: dict[str, int] = {}
    for lot in book.lots():
        held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
    return held


def _top_up(targets: list[str], today: Mapping[str, float], book, spread: float) -> list[Order]:
    """Buy the missing quantity of each target so that it holds an equal dollar share of equity."""
    if not targets:
        return []
    held = _held(book)
    equity = book.balance + sum(l.qty * today.get(l.market_hash_name, l.unit_cost) for l in book.lots())
    dollars, cash = equity / len(targets), book.balance
    orders = []
    for name in targets:
        unit = today[name] * (1 + spread)
        want = math.floor(dollars / unit)
        if want < 1 and unit <= dollars + cash and not held.get(name):
            want = 1
        qty = min(want - held.get(name, 0), math.floor(cash / unit))
        if qty >= 1:
            orders.append(Order(name, "buy", qty))
            cash -= qty * unit
    return orders


class QualityBasket:
    name = "quality_basket"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**QUALITY_DEFAULTS, **params}
        self.targets: list[str] = []
        self.last_pick = None

    def _pick(self, market) -> list[str]:
        hist = market.history(180)
        now = market.now
        uni = set(market.universe())
        recent = pl.col("t") > now - pl.duration(days=60)
        stats = (hist.filter(pl.col("market_hash_name").is_in(uni))
                 .group_by("market_hash_name")
                 .agg(pl.len().alias("bars"),
                      pl.col("volume").filter(recent).sum().alias("v_new"),
                      pl.col("volume").filter(~recent).sum().alias("v_old"))
                 .filter(pl.col("bars") >= 120)
                 .with_columns((pl.col("v_new") + pl.col("v_old")).alias("v_all"))
                 .sort(["v_all", "market_hash_name"], descending=[True, False]))
        n = self.p["n_items"]
        # Volume ranks reshuffle every quarter; a current target stays while it ranks within the top 2n,
        # and only newcomers must also show non-shrinking volume. Without this the first version swapped
        # 7-10 of 10 items per rebalance and paid ~11% on each.
        kept = [t for t in self.targets if t in stats["market_hash_name"].to_list()[:2 * n]]
        rising = stats.filter(pl.col("v_new") >= pl.col("v_old") / 2)["market_hash_name"].to_list()
        return (kept + [r for r in rising if r not in kept])[:n]

    def decide(self, market, book) -> list[Order]:
        now = market.now
        if self.last_pick is None or (now - self.last_pick).days >= self.p["rebalance_days"]:
            picked = self._pick(market)
            if picked:
                self.targets, self.last_pick = picked, now
        if not self.targets:
            return []
        today = dict(market.history(7).sort("t").group_by("market_hash_name", maintain_order=True)
                     .last().select("market_hash_name", "price").iter_rows())
        orders = [Order(n, "sell", q) for n in sorted(_held(book)) if n not in self.targets
                  and (q := book.sellable(n))]
        orders += _top_up([n for n in self.targets if n in today], today, book, self.p["spread"])
        return orders


class CaseDip:
    name = "case_dip"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**CASEDIP_DEFAULTS, **params}

    def decide(self, market, book) -> list[Order]:
        regime = str(market.regime())
        if regime == "rising":
            now = market.now
            out = []
            for name in sorted({l.market_hash_name for l in book.lots()}):
                old = [l for l in book.lots() if l.market_hash_name == name
                       and (now - l.bought).days >= self.p["min_hold"]]
                if old and (q := min(book.sellable(name), sum(l.qty for l in old))):
                    out.append(Order(name, "sell", q))
            return out
        if regime != "falling":
            return []
        cases = set(market.items().filter(pl.col("category") == "case")["name"]) & set(market.universe())
        hist = market.history(30)
        today = dict(hist.filter(pl.col("t") == market.now).select("market_hash_name", "price").iter_rows())
        liquid = (hist.filter(pl.col("market_hash_name").is_in(cases))
                  .group_by("market_hash_name").agg(pl.col("volume").sum().alias("v"))
                  .sort(["v", "market_hash_name"], descending=[True, False]))
        targets = [n for n in liquid["market_hash_name"] if n in today][:self.p["n_cases"]]
        return _top_up(targets, today, book, self.p["spread"])


class CheapReversion(MeanReversion):
    name = "cheap_reversion"
    defaults = CHEAP_DEFAULTS
