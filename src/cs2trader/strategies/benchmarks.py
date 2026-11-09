import math
from collections.abc import Mapping
from typing import Any

from cs2trader.models import Order

KNIFE_GLOVE = ("knife", "glove")


class Cash:
    name = "cash"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params

    def decide(self, market, book) -> list[Order]:
        return []


def equal_split(prices: Mapping[str, float], budget: float) -> dict[str, int]:
    """Equal dollar shares; an item dearer than its share is dropped and the budget re-split
    across the rest, until every kept item is affordable. `prices` are expected fill prices."""
    kept = dict(prices)
    while kept:
        share = budget / len(kept)
        dear = [n for n, p in kept.items() if p > share]
        if not dear:
            return {n: math.floor(share / p) for n, p in kept.items()}
        for n in dear:
            del kept[n]
    return {}


class BuyAndHoldUniverse:
    name = "bh_universe"
    categories: tuple[str, ...] | None = None

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.spread = params.get("spread", 0.03)
        self.margin = params.get("margin", 0.02)
        self.retry_days = params.get("retry_days", 10)
        self._targets: dict[str, int] | None = None
        self._days_left = self.retry_days

    def _candidates(self, market) -> list[str]:
        names = set(market.universe())
        if self.categories is not None:
            items = market.items()
            names &= set(items.filter(items["category"].is_in(self.categories))["name"])
        return sorted(names)

    def _prices(self, market, names: list[str]) -> dict[str, float]:
        # Latest bar in the last week; items with no recent bar cannot be bought tomorrow anyway.
        hist = market.history(7).filter(market.history(7)["market_hash_name"].is_in(names))
        last = hist.sort("t").group_by("market_hash_name", maintain_order=True).last()
        return {r["market_hash_name"]: r["price"] * (1 + self.spread) * (1 + self.margin)
                for r in last.iter_rows(named=True)}

    def decide(self, market, book) -> list[Order]:
        if self._targets is None:
            prices = self._prices(market, self._candidates(market))
            targets = {n: q for n, q in equal_split(prices, book.balance).items() if q > 0}
            if not targets:
                return []
            self._targets = targets
        if self._days_left <= 0:
            return []
        self._days_left -= 1
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
        todo = {n: q - held.get(n, 0) for n, q in self._targets.items() if q > held.get(n, 0)}
        prices = self._prices(market, list(todo))
        balance = book.balance
        orders = []
        for n in sorted(todo, key=lambda n: prices.get(n, math.inf)):
            if n not in prices:
                continue
            qty = min(todo[n], math.floor(balance / prices[n]))
            if qty > 0:
                orders.append(Order(n, "buy", qty))
                balance -= qty * prices[n]
        return orders


class BuyAndHoldKnivesGloves(BuyAndHoldUniverse):
    name = "bh_knives_gloves"
    categories = KNIFE_GLOVE
