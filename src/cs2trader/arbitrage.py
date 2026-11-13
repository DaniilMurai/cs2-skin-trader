"""Wallet arbitrage paper test: buy on market.csgo at the ask, sell on Steam after the trade lock.

The gain is counted in Steam-wallet units: a Steam seller keeps price / 1.15, and wallet money
cannot be turned back into cash. Assumptions, all in `ArbParams`:

- Steam price at the decision. An ask seen at hour h is compared with the latest Steam daily
  bar of a UTC day strictly before h's day, at most `max_ref_age_days` old. The bar of h's own
  day is still forming at h, so it is never used. Bars are `Store.daily_bars` (volume-weighted,
  one-day spikes clipped using earlier bars only).
- Edge = (steam / (1 + steam_fee)) / (ask * (1 + buy_fee)) - 1. Asks below `min_ask` are
  skipped: Steam's two fees have a $0.01 floor each, so / 1.15 is wrong for cheap items.
- Size. One unit per item per snapshot (the feed shows only the lowest ask; the next listing
  costs more), and per item per UTC day at most floor(volume_share * reference-day Steam volume).
- Trade lock. A unit bought on UTC day d is sold on day d + lock_days + 1, the first full day
  after the lock ends, at that day's Steam bar / 1.15, whatever the price has become. No bar
  that day: the next day with one, up to `max_sale_wait_days`; after that the unit counts as
  unsold with zero proceeds. Sale-day volume is not re-checked against the cap; the number of
  units above it is reported instead.
- Capital. Cash is in flight for lock_days + 1 days, so a capital C that revolves supports a
  steady spend of C / (lock_days + 1) per UTC day; unspent budget does not carry over. What comes
  back is wallet money, so every cycle needs fresh cash: the result is the rate at which C of cash
  in flight turns into wallet money at a discount, not a compounding return.
- Daily profit = mean over resolved buy days of (proceeds - cost) of that day's buys. A buy day
  is resolved once its sale day is inside the Steam data and none of its units is still waiting.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from cs2trader.errors import DataCheckFailed
from cs2trader.store import Store

# Extra Steam days loaded before the first snapshot: the spike filter needs a few earlier bars
_WARMUP_DAYS = 14

LOTS_SCHEMA = {"feed_time": pl.Datetime("us", "UTC"), "buy_day": pl.Date, "market_hash_name": pl.String,
               "cost": pl.Float64, "edge": pl.Float64, "sale_day": pl.Date, "sale_volume": pl.Int64,
               "proceeds": pl.Float64, "status": pl.String}


@dataclass(frozen=True)
class ArbParams:
    steam_fee: float = 0.15
    buy_fee: float = 0.0
    lock_days: int = 7
    volume_share: float = 0.10
    min_edge: float = 0.05
    min_ask: float = 0.20
    max_ref_age_days: int = 3
    max_sale_wait_days: int = 3
    min_days: int = 14


@dataclass(frozen=True)
class ArbResult:
    capital: float
    lots: pl.DataFrame      # one row per unit bought, LOTS_SCHEMA
    daily: pl.DataFrame     # buy_day, spent, units, profit, resolved
    resolved_days: int
    profit_per_day: float   # wallet units; 0.0 when no day is resolved
    spent_per_day: float
    over_cap_units: int


def steam_bars(store: Store, first_day: date) -> pl.DataFrame:
    last = store.last_day()
    if last is None:
        return pl.DataFrame(schema={"market_hash_name": pl.String, "t": pl.Date, "price": pl.Float64,
                                    "volume": pl.Int64})
    bars = store.daily_bars(first_day - timedelta(days=_WARMUP_DAYS), last)
    return bars.filter(pl.col("price") > 0).select("market_hash_name", "t", "price", "volume")


def hourly_edges(store: Store, p: ArbParams, bars: pl.DataFrame | None = None) -> pl.DataFrame:
    """One row per (snapshot, item) that has a usable Steam bar and a volume cap of at least 1."""
    n_days, first = store.db.execute(
        "SELECT count(DISTINCT CAST(feed_time AS DATE)), min(CAST(feed_time AS DATE)) FROM asks").fetchall()[0]
    if n_days < max(p.min_days, 1):
        raise DataCheckFailed(f"arbitrage needs at least {p.min_days} days of market.csgo ask snapshots, "
                              f"found {n_days}; keep the collector running")
    if bars is None:
        bars = steam_bars(store, first)
    store.db.register("_arb_bars", bars.sort("market_hash_name", "t").to_arrow())
    try:
        # ASOF with a strict inequality: the newest bar of a day before the snapshot's own day
        edges = store.db.sql("""
            SELECT a.feed_time, a.market_hash_name, a.ask, b.t AS ref_day, b.price AS steam,
                   b.volume, CAST(floor(? * b.volume) AS BIGINT) AS cap_units
            FROM (SELECT feed_time, CAST(feed_time AS DATE) AS day, market_hash_name, ask
                  FROM asks WHERE ask >= ?) a
            ASOF JOIN _arb_bars b ON a.market_hash_name = b.market_hash_name AND a.day > b.t
            WHERE b.t >= a.day - ? AND floor(? * b.volume) >= 1
            ORDER BY a.feed_time, a.market_hash_name
        """, params=[p.volume_share, p.min_ask, p.max_ref_age_days, p.volume_share]).pl()
    finally:
        store.db.unregister("_arb_bars")
    if edges.is_empty():
        raise DataCheckFailed(f"no ask has a Steam daily bar from the {p.max_ref_age_days} days before it; "
                              "run `cs2 backfill steam --refresh` so Steam history covers the snapshot days")
    net = pl.col("steam") / (1 + p.steam_fee)
    return edges.with_columns((net / (pl.col("ask") * (1 + p.buy_fee)) - 1).alias("edge"))


def simulate(edges: pl.DataFrame, bars: pl.DataFrame, capital: float, p: ArbParams) -> ArbResult:
    hold = p.lock_days + 1
    budget = capital / hold
    last_steam = bars["t"].max()
    days = edges["feed_time"].dt.date()
    all_days = pl.date_range(days.min(), days.max(), "1d", eager=True).to_list()

    picks = edges.filter(pl.col("edge") >= p.min_edge).sort(
        ["feed_time", "edge", "market_hash_name"], descending=[False, True, False])
    wanted = picks["market_hash_name"].unique()
    bar_at = {(n, t): (price, vol) for n, t, price, vol in
              bars.filter(pl.col("market_hash_name").is_in(wanted.implode())).iter_rows()}

    spent: dict[date, float] = {}
    held: dict[tuple[str, date], int] = {}
    rows = []
    for feed, name, ask, cap, edge in picks.select(
            "feed_time", "market_hash_name", "ask", "cap_units", "edge").iter_rows():
        day = feed.date()
        cost = ask * (1 + p.buy_fee)
        # A too-expensive item is skipped, not a stop: a cheaper one further down may still fit
        if spent.get(day, 0.0) + cost > budget + 1e-9 or held.get((name, day), 0) >= cap:
            continue
        spent[day] = spent.get(day, 0.0) + cost
        held[(name, day)] = held.get((name, day), 0) + 1
        rows.append((feed, day, name, cost, edge, *_sale(name, day + timedelta(days=hold), bar_at,
                                                         last_steam, p)))
    lots = pl.DataFrame(rows, schema=LOTS_SCHEMA, orient="row")

    pending_days = set(lots.filter(pl.col("status") == "pending")["buy_day"].to_list())
    per_day = {d: (s, u, pr) for d, s, u, pr in lots.group_by("buy_day").agg(
        pl.col("cost").sum(), pl.len(), (pl.col("proceeds").fill_null(0.0) - pl.col("cost")).sum()).iter_rows()}
    daily = pl.DataFrame(
        [(d, *per_day.get(d, (0.0, 0, 0.0)), d + timedelta(days=hold) <= last_steam and d not in pending_days)
         for d in all_days],
        schema={"buy_day": pl.Date, "spent": pl.Float64, "units": pl.Int64, "profit": pl.Float64,
                "resolved": pl.Boolean}, orient="row")
    done = daily.filter(pl.col("resolved"))
    sold = lots.filter(pl.col("status") == "sold").group_by("market_hash_name", "sale_day").agg(
        pl.len().alias("units"), pl.col("sale_volume").first())
    over = (sold["units"] - (sold["sale_volume"] * p.volume_share).floor()).clip(lower_bound=0).sum()
    return ArbResult(capital, lots, daily, done.height,
                     done["profit"].mean() if done.height else 0.0,
                     done["spent"].mean() if done.height else 0.0, int(over or 0))


def _sale(name: str, first_day: date, bar_at: dict, last_steam: date, p: ArbParams) -> tuple:
    for k in range(p.max_sale_wait_days + 1):
        day = first_day + timedelta(days=k)
        if day > last_steam:
            return None, None, None, "pending"
        if (name, day) in bar_at:
            price, volume = bar_at[(name, day)]
            return day, volume, price / (1 + p.steam_fee), "sold"
    return None, None, 0.0, "unsold"


def format_assumptions(p: ArbParams) -> str:
    hold = p.lock_days + 1
    return "\n".join([
        "assumptions:",
        f"  buy at the market.csgo lowest ask (buy fee {p.buy_fee:.0%}), one unit per item per snapshot, ask >= ${p.min_ask:.2f}",
        f"  decide on the last Steam daily bar before the snapshot's UTC day (at most {p.max_ref_age_days} days old), edge >= {p.min_edge:.0%}",
        f"  per item per day at most {p.volume_share:.0%} of that bar's Steam volume",
        (f"  {p.lock_days}-day trade lock: sold on buy day + {hold} at that day's Steam bar / {1 + p.steam_fee:.2f} "
         f"(wait up to {p.max_sale_wait_days} more days for a bar, then unsold = 0)"),
        (f"  capital revolves over {hold} days: spend at most capital / {hold} per UTC day; proceeds are wallet "
         "money, so each cycle needs fresh cash"),
    ])


def format_result(r: ArbResult, p: ArbParams) -> str:
    lots = r.lots
    n = {s: lots.filter(pl.col("status") == s).height for s in ("sold", "unsold", "pending")}
    head = f"capital ${r.capital:,.0f} (budget ${r.capital / (p.lock_days + 1):,.2f}/day): "
    if r.resolved_days == 0:
        return head + (f"no resolved buy day yet ({lots.height} units bought, all waiting for Steam data "
                       f"{p.lock_days + 1} days later); run `cs2 backfill steam --refresh` once it exists")
    ret = r.profit_per_day / r.spent_per_day if r.spent_per_day else 0.0
    return "\n".join([
        head + f"{r.profit_per_day:+.2f} wallet $/day over {r.resolved_days} resolved buy days",
        (f"  spent ${r.spent_per_day:,.2f}/day, {ret:+.1%} on the money spent; units: {n['sold']} sold, "
         f"{n['unsold']} unsold, {n['pending']} pending; {r.over_cap_units} sold above the volume share on the sale day"),
    ])


def format_edges(edges: pl.DataFrame, p: ArbParams) -> str:
    hours = edges["feed_time"].n_unique()
    good = edges.filter(pl.col("edge") >= p.min_edge)
    return (f"edges: {edges.height} item-hours over {hours} snapshots "
            f"({edges['feed_time'].min():%Y-%m-%d %H:%M} .. {edges['feed_time'].max():%Y-%m-%d %H:%M} UTC), "
            f"{edges['market_hash_name'].n_unique()} items; median edge {edges['edge'].median():+.1%}, "
            f"{good.height / edges.height:.0%} at or above {p.min_edge:.0%}")
