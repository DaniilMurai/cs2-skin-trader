import math
import unicodedata
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.market.events import load_events, majors
from cs2trader.models import Order

EVENT_SPEC = """**event — Majors and case pool exits (S-09: the only event types with enough cases).**
`rule = "majors"`: from `pre` days before a Major starts until `post` days after it ends, hold
that Major's sticker capsules (capsule-category items whose name carries the Major's city and
year; DreamHack for the Jönköping Majors), equal dollars across up to `max_positions` items;
everything is sold once the window closes and the lots unlock. Major dates are announced months
ahead, so acting `pre` days early uses no future information.
`rule = "cases"`: on the day a case moves from the active to the rare drop pool or is
discontinued (docs/pipeline/events.csv), buy it with one slot (equity / `max_positions`) and sell
it `hold` days later. Events come from a curated list; see spikes/FINDINGS.md S-09.
"""

EVENT_GRID = {
    "rule": ["majors", "cases"],
    "pre": [0, 14],
    "post": [0, 14, 30],
    "hold": [30, 90],
}

EVENT_DEFAULTS = {"rule": "majors", "pre": 14, "post": 14, "hold": 30, "max_positions": 5, "spread": 0.03}

# Majors whose capsules carry the organiser's name instead of the city.
CAPSULE_ALIASES = {"Jönköping 2013": "DreamHack 2013", "Jönköping 2014": "DreamHack 2014"}


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


class EventStrategy:
    name = "event"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**EVENT_DEFAULTS, **params}
        ev = load_events()
        self.majors = majors(ev)
        self.pool_exits = (ev.filter(pl.col("type").is_in(["case_to_rare_pool", "case_discontinued"]))
                           .group_by("name").agg(pl.col("date").min()))
        self.sell_after: dict[str, Any] = {}
        self.traded: set[str] = set()

    def decide(self, market, book) -> list[Order]:
        p, now = self.p, market.now
        today = dict(market.history(1).select("market_hash_name", "price").iter_rows())
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
        targets = self._majors_targets(market, now) if p["rule"] == "majors" else self._case_targets(now)

        orders = []
        for name in held:
            done = name not in targets if p["rule"] == "majors" else now >= self.sell_after.get(name, now)
            if done and (qty := book.sellable(name)) and name in today:
                orders.append(Order(name, "sell", qty))
        free = p["max_positions"] - len(held)
        equity = book.balance + sum(q * today.get(n, 0.0) for n, q in held.items())
        cash = book.balance
        for name in sorted(targets - set(held)):
            if free <= 0 or name not in today:
                continue
            unit = today[name] * (1 + p["spread"])
            qty = math.floor(min(equity / p["max_positions"], cash) / unit)
            if qty < 1 and unit <= cash:
                qty = 1
            if qty >= 1:
                orders.append(Order(name, "buy", qty))
                cash -= qty * unit
                free -= 1
                self.sell_after[name] = now + timedelta(days=p["hold"])
                self.traded.add(name)
        return orders

    def _majors_targets(self, market, now) -> set[str]:
        p = self.p
        live = self.majors.filter((pl.col("start") - timedelta(days=p["pre"]) <= now)
                                  & (now < pl.col("end") + timedelta(days=p["post"])))
        if live.is_empty():
            return set()
        tags = [_fold(CAPSULE_ALIASES.get(n, n)) for n in live["name"].to_list()]
        capsules = market.items().filter(pl.col("category") == "capsule")["name"].to_list()
        return {c for c in capsules if any(t in _fold(c) for t in tags)}

    def _case_targets(self, now) -> set[str]:
        # Never before the exit day (the event is unknown until then); up to 3 days after it,
        # since a thin case may have no bar on the day itself. Each case is traded once.
        recent = self.pool_exits.filter(pl.col("date").is_between(now - timedelta(days=3), now))
        return set(recent["name"]) - self.traded
