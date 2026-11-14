import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import polars as pl

from cs2trader.market.events import load_events, majors
from cs2trader.market.universe import liquid_band
from cs2trader.models import Order
from cs2trader.strategies.event import CAPSULE_ALIASES, _fold

EVENT_SMALL_SPEC = """**event_small — a Major's sticker capsules, bought after the tournament, small positions.**
The one event rule the pre-holdout measurement supports (reports/T-60-events.md): capsules bought 90
days after a Major ends and held for half a year or longer. A Major is live from `wait` days after
its last day (docs/pipeline/events.csv; the date is announced months ahead) for `hold` days. While
it is live, hold its sticker capsules (capsule-category items whose name carries the Major's city
and year) that are inside the liquid band: last price and `entry_days`-day median price (at least
two bars) between `min_price` and `max_price`, 30-day median volume of at least `min_volume` a
day. Up to `max_positions` items, most traded first; one position is equity / `max_positions`
dollars, never more than `cap_share` of the item's median daily volume. A buy is a limit order at
the `entry_days`-day median × (1 + `spread`) × (1 + `max_slip`), so one odd print is neither a
signal nor a fill; an unfilled order is placed again the next day. Nothing is bought with fewer
than `min_left` days of the window left. A held item is not bought again. Everything is sold once
its Major is no longer live and the 7-day lock has ended.
"""

EVENT_SMALL_GRID = {
    "wait": [0, 90],
    "hold": [180, 365],
}

EVENT_SMALL_DEFAULTS = {"wait": 90, "hold": 180, "min_left": 30, "entry_days": 3, "min_price": 0.10,
                        "max_price": 5.0, "min_volume": 20, "cap_share": 0.05, "max_positions": 5,
                        "spread": 0.03, "max_slip": 0.05}

NAME = "market_hash_name"


class EventSmall:
    name = "event_small"

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params
        self.p = {**EVENT_SMALL_DEFAULTS, **params}
        self.majors = majors(load_events())

    def live(self, now, margin: int = 0) -> list[str]:
        """Folded name tags of the Majors whose window is open at `now` and for `margin` more days."""
        opens = pl.col("end") + timedelta(days=self.p["wait"])
        closes = opens + timedelta(days=self.p["hold"])
        rows = self.majors.filter((opens <= now) & (now + timedelta(days=margin) < closes))
        return [_fold(CAPSULE_ALIASES.get(n, n)) for n in rows["name"].to_list()]

    def decide(self, market, book) -> list[Order]:
        p, now = self.p, market.now
        held: dict[str, int] = {}
        for lot in book.lots():
            held[lot.market_hash_name] = held.get(lot.market_hash_name, 0) + lot.qty
        keep = self.live(now)
        orders = [Order(n, "sell", q) for n in sorted(held)
                  if not any(t in _fold(n) for t in keep) and (q := book.sellable(n))]

        free = p["max_positions"] - len(held)
        tags = self.live(now, p["min_left"])
        if free <= 0 or not tags:
            return orders
        capsules = market.items().filter(pl.col("category") == "capsule")["name"].to_list()
        wanted = [c for c in capsules if c not in held and any(t in _fold(c) for t in tags)]
        band = liquid_band(market.liquidity().filter(pl.col(NAME).is_in(wanted)),
                           p["min_price"], p["max_price"], p["min_volume"])
        hist = market.history(p["entry_days"])
        stats = (hist.filter(pl.col(NAME).is_in(band[NAME].to_list())).group_by(NAME)
                 .agg(pl.col("price").median().alias("entry"), pl.len().alias("n"))
                 .filter((pl.col("n") >= 2) & pl.col("entry").is_between(p["min_price"], p["max_price"]))
                 .join(band.select(NAME, "median_volume"), on=NAME)
                 .sort(["median_volume", NAME], descending=[True, False]))

        marks = dict(hist.filter(pl.col(NAME).is_in(list(held))).group_by(NAME)
                     .agg(pl.col("price").median()).iter_rows())
        cost = {n: sum(l.qty * l.unit_cost for l in book.lots() if l.market_hash_name == n) for n in held}
        equity = book.balance + sum(q * marks[n] if n in marks else cost[n] for n, q in held.items())
        slot = equity / p["max_positions"]
        cash = book.balance
        for name, entry, _, volume in stats.iter_rows():
            if free == 0:
                break
            unit = entry * (1 + p["spread"])
            # The epsilon keeps whole caps whole, as the engine's own cap does (0.05 * 20 in floats).
            room = math.floor(volume * p["cap_share"] + 1e-9)
            qty = min(math.floor(slot / unit), room, math.floor(cash / unit + 1e-9))
            if qty < 1:
                continue
            orders.append(Order(name, "buy", qty, unit * (1 + p["max_slip"])))
            cash -= qty * unit
            free -= 1
        return orders
