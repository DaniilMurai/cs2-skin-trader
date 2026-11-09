import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from cs2trader.models import Order
from cs2trader.strategies.base import Strategy


class BuyAndHoldUniverse:
    name = "bh_universe"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self._done = False

    def decide(self, market, book) -> list[Order]:
        if self._done:
            return []
        self._done = True
        today = market.history(1)
        if today.is_empty():
            return []
        budget = book.balance / today.height
        return [
            Order(r["market_hash_name"], "buy", q)
            for r in today.iter_rows(named=True)
            if (q := math.floor(budget / r["price"])) > 0
        ]


@dataclass(frozen=True)
class StrategyEntry:
    name: str
    family: Literal["video", "trend", "momentum", "mean_reversion", "event", "benchmark"]
    factory: Callable[[Mapping[str, Any]], Strategy]
    grid: Mapping[str, Sequence[Any]]
    spec_md: str


REGISTRY: Mapping[str, StrategyEntry] = {
    "bh_universe": StrategyEntry(
        "bh_universe", "benchmark", BuyAndHoldUniverse, {},
        "Buy every universe item in equal dollar weights on the first bar; never sell.",
    ),
}
