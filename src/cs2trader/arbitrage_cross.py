"""Cross-venue arbitrage paper test: buy at venue A's lowest listing, sell on venue B for cash.

Venues: market.csgo, Waxpeer, Skinport. The gain is counted in cash back on the bank side, so
every fee on the way is inside the number. Assumptions, all in `CrossParams` / `Venue`:

- Quotes. Each venue's snapshots are bucketed to the UTC hour (the latest one inside the hour);
  a decision for hour h uses the quotes of bucket h only. A venue with no snapshot in an hour has
  no quote there, so no pair with it trades in that hour. Skinport: tradable listings only, and
  Doppler-style rows with a `version` are dropped (the other venues do not split them).
- Cost of a unit = ask_A * (1 + buy_fee_A) * (1 + deposit_fee_A).
- Expected sale at h = B's reference price * (1 - sell_fee_B) * (1 - withdraw_fee_B). The
  reference is B's highest bid where the venue sells into bids (market.csgo), otherwise B's
  lowest listing * (1 - undercut). Edge = expected sale / cost - 1.
- Filters. Both sides need `min_listings` listings and ask >= `min_price`; edges above `max_edge`
  are treated as two different items under one name (S-12) and skipped.
- Size. One unit per item per UTC day over all pairs: nothing here shows how many units a venue
  absorbs at its lowest price.
- Trade lock. A unit bought at hour h can be offered from h + lock_days. The first hour from then
  with a B quote, at most `max_list_wait_hours` later, is the listing hour; none means unsold,
  zero proceeds.
- Sale. Into bids: sold at the listing hour's bid. As a listing: no venue publishes sales, so the
  unit is assumed to sell only `sale_wait_days` after listing and at the lowest price B showed
  in that window, less the undercut, as if every dip had to be matched.
- Capital. Money is in flight from the buy until B pays out: lock_days + 1 + sale_wait_days (for
  listings) + payout_days_B. A revolving capital C therefore supports, per UTC day, buys with
  sum(cost * cycle_days) <= C. Gains are not reinvested.
- Daily profit = mean over resolved buy days of (cash back - cost) of that day's buys. A buy day
  is resolved once its latest possible sale hour is inside the data and no unit is still waiting;
  days on which fewer than two venues were collected are not buy days.
- Not in the number: fixed per-payment parts of deposit and withdrawal fees, withdrawal minimums
  (a small balance waits on the venue), trade reversals, bans, and the time spent.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from cs2trader.errors import DataCheckFailed
from cs2trader.store import Store

MC, WAXPEER, SKINPORT = "market.csgo", "waxpeer", "skinport"

LOTS_SCHEMA = {"hour": pl.Datetime("us", "UTC"), "buy_day": pl.Date, "venue_a": pl.String, "venue_b": pl.String,
               "market_hash_name": pl.String, "cost": pl.Float64, "edge": pl.Float64,
               "sale_hour": pl.Datetime("us", "UTC"), "proceeds": pl.Float64, "status": pl.String}


@dataclass(frozen=True)
class Venue:
    sell_fee: float
    withdraw_fee: float
    payout_days: int
    deposit_fee: float
    buy_fee: float = 0.0
    sells_into_bids: bool = False


def default_venues() -> dict[str, Venue]:
    return {
        # 5% sale and 5% card withdrawal: third-party (S-03); 8-day retention plus up to 8 days for
        # the external payout: official terms §3.7, §5.7; top-up fee exists, size not published.
        MC: Venue(sell_fee=0.05, withdraw_fee=0.05, payout_days=16, deposit_fee=0.039, sells_into_bids=True),
        # Official help pages read 2026-11-13: 6% sale (2% only with the mobile app connected),
        # 8-day hold, withdrawal 1.5% + network fee (2% taken), card deposit 3.9% + $0.35.
        WAXPEER: Venue(sell_fee=0.06, withdraw_fee=0.02, payout_days=8, deposit_fee=0.039),
        # 8% sale: official blog (S-12). Payout fee, payout delay and any buyer-side fee are not
        # confirmed, so they take the worst value known for the other two venues.
        SKINPORT: Venue(sell_fee=0.08, withdraw_fee=0.05, payout_days=16, deposit_fee=0.039),
    }


@dataclass(frozen=True)
class CrossParams:
    venues: dict[str, Venue] = field(default_factory=default_venues)
    lock_days: int = 7
    undercut: float = 0.01
    sale_wait_days: int = 3
    max_list_wait_hours: int = 24
    min_edge: float = 0.05
    max_edge: float = 0.50
    min_price: float = 0.20
    min_listings: int = 3
    top_per_hour: int = 200
    min_days: int = 14

    def cycle_days(self, venue_b: str) -> int:
        v = self.venues[venue_b]
        return self.lock_days + 1 + (0 if v.sells_into_bids else self.sale_wait_days) + v.payout_days

    @property
    def horizon_days(self) -> int:
        # Whole days after which every unit of a buy day has reached its sale hour or is unsold
        return self.lock_days + self.sale_wait_days + self.max_list_wait_hours // 24 + 1


@dataclass(frozen=True)
class CrossResult:
    capital: float
    lots: pl.DataFrame      # one row per unit bought, LOTS_SCHEMA
    daily: pl.DataFrame     # buy_day, spent, units, profit, resolved
    resolved_days: int
    profit_per_day: float   # cash; 0.0 when no day is resolved
    spent_per_day: float


def _files(store: Store, kind: str) -> str | None:
    tree = Path(store.data_dir) / "collector" / kind
    return f"read_parquet('{tree}/day=*/*.parquet', hive_partitioning = true)" if any(
        tree.glob("day=*/*.parquet")) else None


def load_quotes(store: Store) -> dict[str, int]:
    """Builds the temp table `_xq` (hour, venue, name, ask, bid, listings); returns days per venue."""
    parts = [f"""
        SELECT a.hour, '{MC}' AS venue, a.market_hash_name, a.ask, b.bid, a.listings
        FROM (SELECT date_trunc('hour', feed_time) AS hour, market_hash_name,
                     arg_max(ask, feed_time) AS ask, arg_max(listings, feed_time) AS listings
              FROM asks GROUP BY ALL) a
        LEFT JOIN (SELECT date_trunc('hour', feed_time) AS hour, market_hash_name,
                          arg_max(bid, feed_time) AS bid
                   FROM bids WHERE bid > 0 GROUP BY ALL) b USING (hour, market_hash_name)"""]
    extra = {WAXPEER: "", SKINPORT: "AND tradable AND version IS NULL"}
    for venue, cond in extra.items():
        src = _files(store, venue)
        if src:
            parts.append(f"""
        SELECT date_trunc('hour', fetched_at) AS hour, '{venue}' AS venue, market_hash_name,
               arg_max(min_price, fetched_at) AS ask, CAST(NULL AS DOUBLE) AS bid,
               arg_max(quantity, fetched_at) AS listings
        FROM {src} WHERE min_price > 0 {cond} GROUP BY ALL""")
    store.db.execute("CREATE OR REPLACE TEMP TABLE _xq AS " + " UNION ALL ".join(parts))
    return dict(store.db.execute(
        "SELECT venue, count(DISTINCT CAST(hour AS DATE)) FROM _xq GROUP BY venue").fetchall())


def cross_edges(store: Store, p: CrossParams, max_capital: float) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Returns (summary per pair and UTC day over every item-hour, candidate buys).

    Candidates are the item-hours a buyer with `max_capital` could act on: edge inside
    [min_edge, max_edge], one unit affordable, and among the `top_per_hour` best of their hour.
    The full item-hour log would be tens of millions of rows, so only its summary is kept.
    """
    days = load_quotes(store)
    n_days = store.db.execute("""
        SELECT count(*) FROM (SELECT CAST(hour AS DATE) AS d FROM _xq
                              GROUP BY d HAVING count(DISTINCT venue) >= 2)""").fetchall()[0][0]
    if n_days < max(p.min_days, 1):
        seen = ", ".join(f"{v} {days.get(v, 0)}" for v in p.venues)
        raise DataCheckFailed(f"cross arbitrage needs at least {p.min_days} days with snapshots of two venues, "
                              f"found {n_days} (days per venue: {seen}); keep the collector running")
    fees = pl.DataFrame(
        [(name, (1 + v.buy_fee) * (1 + v.deposit_fee), (1 - v.sell_fee) * (1 - v.withdraw_fee),
          v.sells_into_bids, p.cycle_days(name)) for name, v in p.venues.items()],
        schema={"venue": pl.String, "cost_mult": pl.Float64, "net_mult": pl.Float64, "into_bids": pl.Boolean,
                "cycle": pl.Int64}, orient="row")
    store.db.register("_xf", fees.to_arrow())
    try:
        store.db.execute(f"""
            CREATE OR REPLACE TEMP VIEW _xe AS
            SELECT a.hour, a.venue AS venue_a, b.venue AS venue_b, a.market_hash_name, a.ask,
                   a.ask * fa.cost_mult AS cost,
                   CASE WHEN fb.into_bids THEN b.bid ELSE b.ask * (1 - {p.undercut}) END AS sale_ref,
                   fb.net_mult, fb.cycle
            FROM _xq a JOIN _xq b ON a.hour = b.hour AND a.market_hash_name = b.market_hash_name
                                  AND a.venue <> b.venue
            JOIN _xf fa ON fa.venue = a.venue JOIN _xf fb ON fb.venue = b.venue
            WHERE a.ask >= {p.min_price} AND a.listings >= {p.min_listings} AND b.listings >= {p.min_listings}
                  AND (b.bid IS NOT NULL OR NOT fb.into_bids)""")
        edge = "sale_ref * net_mult / cost - 1"
        summary = store.db.sql(f"""
            SELECT venue_a, venue_b, CAST(hour AS DATE) AS day, count(DISTINCT hour) AS hours,
                   count(*) AS item_hours, median({edge}) AS median_edge,
                   avg(CAST({edge} >= {p.min_edge} AND {edge} <= {p.max_edge} AS INT)) AS share_tradable
            FROM _xe GROUP BY ALL ORDER BY ALL""").pl()
        candidates = store.db.sql(f"""
            SELECT hour, venue_a, venue_b, market_hash_name, ask, cost, sale_ref, {edge} AS edge
            FROM _xe WHERE {edge} BETWEEN {p.min_edge} AND {p.max_edge} AND cost * cycle <= {max_capital}
            QUALIFY row_number() OVER (PARTITION BY hour ORDER BY {edge} DESC, market_hash_name,
                                       venue_a, venue_b) <= {p.top_per_hour}
            ORDER BY hour, edge DESC, market_hash_name, venue_a, venue_b""").pl()
    finally:
        store.db.execute("DROP VIEW IF EXISTS _xe")
        store.db.unregister("_xf")
    return summary, candidates


def simulate_cross(store: Store, candidates: pl.DataFrame, capital: float, p: CrossParams) -> CrossResult:
    """Needs `_xq` from `cross_edges` on the same store."""
    spent: dict[date, float] = {}
    held: set[tuple[str, date]] = set()
    buys = []
    for hour, a, b, name, cost, edge in candidates.select(
            "hour", "venue_a", "venue_b", "market_hash_name", "cost", "edge").iter_rows():
        day = hour.date()
        load = cost * p.cycle_days(b)
        # A too-expensive unit is skipped, not a stop: a cheaper one further down may still fit
        if (name, day) in held or spent.get(day, 0.0) + load > capital + 1e-9:
            continue
        spent[day] = spent.get(day, 0.0) + load
        held.add((name, day))
        buys.append((hour, day, a, b, name, cost, edge))

    quotes = _sale_quotes(store, buys, p)
    # .pl() throughout: DuckDB's fetchall needs pytz for timestamps with a time zone
    last = dict(store.db.sql("SELECT venue, max(hour) FROM _xq GROUP BY venue").pl().iter_rows())
    lots = pl.DataFrame([(*buy, *_sale(buy, quotes, last, p)) for buy in buys], schema=LOTS_SCHEMA, orient="row")

    # Days with one venue only are collector gaps, not days without an edge: they are left out
    days = [d for (d,) in store.db.execute("""
        SELECT CAST(hour AS DATE) AS d FROM _xq GROUP BY d HAVING count(DISTINCT venue) >= 2 ORDER BY d""").fetchall()]
    last_day = store.db.execute("SELECT max(CAST(hour AS DATE)) FROM _xq").fetchall()[0][0]
    pending = set(lots.filter(pl.col("status") == "pending")["buy_day"].to_list())
    per_day = {d: (s, u, pr) for d, s, u, pr in lots.group_by("buy_day").agg(
        pl.col("cost").sum(), pl.len(), (pl.col("proceeds").fill_null(0.0) - pl.col("cost")).sum()).iter_rows()}
    daily = pl.DataFrame(
        [(d, *per_day.get(d, (0.0, 0, 0.0)), d + timedelta(days=p.horizon_days) <= last_day and d not in pending)
         for d in days],
        schema={"buy_day": pl.Date, "spent": pl.Float64, "units": pl.Int64, "profit": pl.Float64,
                "resolved": pl.Boolean}, orient="row")
    done = daily.filter(pl.col("resolved"))
    return CrossResult(capital, lots, daily, done.height,
                       done["profit"].mean() if done.height else 0.0,
                       done["spent"].mean() if done.height else 0.0)


def _sale_quotes(store: Store, buys: list[tuple], p: CrossParams) -> dict:
    """(venue_b, name) -> [(hour, ask, bid)] sorted by hour, from the unit's unlock hour on."""
    if not buys:
        return {}
    wanted = pl.DataFrame([(b, name, hour + timedelta(days=p.lock_days)) for hour, _, _, b, name, _, _ in buys],
                          schema={"venue": pl.String, "market_hash_name": pl.String,
                                  "unlock": pl.Datetime("us", "UTC")}, orient="row")
    store.db.register("_xw", wanted.group_by("venue", "market_hash_name").agg(pl.col("unlock").min()).to_arrow())
    try:
        rows = store.db.sql("""
            SELECT q.venue, q.market_hash_name, q.hour, q.ask, q.bid
            FROM _xq q JOIN _xw w USING (venue, market_hash_name)
            WHERE q.hour >= w.unlock ORDER BY q.hour""").pl().rows()
    finally:
        store.db.unregister("_xw")
    out: dict = {}
    for venue, name, hour, ask, bid in rows:
        out.setdefault((venue, name), []).append((hour, ask, bid))
    return out


def _sale(buy: tuple, quotes: dict, last: dict[str, datetime], p: CrossParams) -> tuple:
    hour, _, _, b, name, _, _ = buy
    venue = p.venues[b]
    unlock = hour + timedelta(days=p.lock_days)
    deadline = unlock + timedelta(hours=p.max_list_wait_hours)
    net = (1 - venue.sell_fee) * (1 - venue.withdraw_fee)
    series = [(t, bid if venue.sells_into_bids else ask) for t, ask, bid in quotes.get((b, name), []) if t >= unlock]
    listed = next(((t, price) for t, price in series if t <= deadline and price is not None), None)
    if listed is None:
        return (None, None, "pending") if last[b] < deadline else (None, 0.0, "unsold")
    if venue.sells_into_bids:
        return listed[0], listed[1] * net, "sold"
    sale_hour = listed[0] + timedelta(days=p.sale_wait_days)
    if last[b] < sale_hour:
        return None, None, "pending"
    low = min(price for t, price in series if listed[0] <= t <= sale_hour)
    return sale_hour, low * (1 - p.undercut) * net, "sold"


def format_assumptions(p: CrossParams) -> str:
    lines = ["assumptions (cash-to-cash, every fee inside the profit):"]
    for name, v in p.venues.items():
        how = "sells into its bids at once" if v.sells_into_bids else (
            f"sells as a listing {p.undercut:.0%} under its lowest, counted sold after {p.sale_wait_days} days "
            "at the lowest price of those days")
        lines.append(f"  {name}: deposit {v.deposit_fee:.1%}, buy fee {v.buy_fee:.0%}, sale fee {v.sell_fee:.0%}, "
                     f"withdrawal {v.withdraw_fee:.1%}, payout {v.payout_days} days after the sale; {how}; "
                     f"money in flight {p.cycle_days(name)} days")
    lines += [
        (f"  buy at the lowest listing when the edge is {p.min_edge:.0%}..{p.max_edge:.0%} (above: treated as a "
         f"different item), price >= ${p.min_price:.2f}, >= {p.min_listings} listings on both venues"),
        (f"  one unit per item per UTC day; {p.lock_days}-day trade lock, then the first hour with a quote "
         f"(within {p.max_list_wait_hours} h, else unsold = 0)"),
        "  capital revolves: per UTC day sum(cost x days in flight) <= capital; gains are not reinvested",
        "  not counted: fixed parts of deposit/withdrawal fees, withdrawal minimums, trade reversals",
    ]
    return "\n".join(lines)


def format_result(r: CrossResult) -> str:
    n = {s: r.lots.filter(pl.col("status") == s).height for s in ("sold", "unsold", "pending")}
    head = f"capital ${r.capital:,.0f}: "
    if r.resolved_days == 0:
        return head + f"no resolved buy day yet ({r.lots.height} units bought, waiting for the lock and the sale)"
    ret = r.profit_per_day / r.spent_per_day if r.spent_per_day else 0.0
    return (head + f"{r.profit_per_day:+.2f} $/day over {r.resolved_days} resolved buy days; "
            f"spent ${r.spent_per_day:,.2f}/day, {ret:+.1%} on the money spent; units: {n['sold']} sold, "
            f"{n['unsold']} unsold, {n['pending']} pending")


def pair_table(summary: pl.DataFrame, results: list[CrossResult]) -> pl.DataFrame:
    """One row per ordered pair: the short table for the report step."""
    table = summary.group_by("venue_a", "venue_b").agg(
        pl.col("day").n_unique().alias("days"), pl.col("hours").sum(), pl.col("item_hours").sum(),
        # Day medians weighted by item-hours: close to the overall median without a second pass
        ((pl.col("median_edge") * pl.col("item_hours")).sum() / pl.col("item_hours").sum()).alias("median_edge"),
        ((pl.col("share_tradable") * pl.col("item_hours")).sum() / pl.col("item_hours").sum())
        .alias("share_tradable")).sort("venue_a", "venue_b")
    for r in results:
        days = max(r.resolved_days, 1)
        resolved = r.daily.filter(pl.col("resolved"))["buy_day"]
        per_pair = r.lots.filter(pl.col("buy_day").is_in(resolved.implode())).group_by("venue_a", "venue_b").agg(
            ((pl.col("proceeds").fill_null(0.0) - pl.col("cost")).sum() / days).alias(f"profit_per_day_{r.capital:g}"))
        table = table.join(per_pair, on=["venue_a", "venue_b"], how="left").with_columns(
            pl.col(f"profit_per_day_{r.capital:g}").fill_null(0.0))
    return table


def format_table(table: pl.DataFrame) -> str:
    money = [c for c in table.columns if c.startswith("profit_per_day_")]
    head = ["buy at", "sell at", "days", "item-hours", "median edge", "share tradable",
            *(f"$/day at ${c.removeprefix('profit_per_day_')}" for c in money)]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in table.iter_rows(named=True):
        rows.append("| " + " | ".join([
            r["venue_a"], r["venue_b"], str(r["days"]), f"{r['item_hours']:,}", f"{r['median_edge']:+.1%}",
            f"{r['share_tradable']:.1%}", *(f"{r[c]:+.2f}" for c in money)]) + " |")
    return "\n".join(rows)


def run(store: Store, out: Path, capitals: list[float], p: CrossParams) -> list[str]:
    """Writes the candidate log, the lots and the pair table under `out`; returns the lines to print."""
    summary, candidates = cross_edges(store, p, max(capitals))
    out.mkdir(parents=True, exist_ok=True)
    candidates.write_parquet(out / "cross_edges.parquet", compression="zstd")
    summary.write_parquet(out / "cross_summary.parquet", compression="zstd")
    lines = [format_assumptions(p)]
    results = []
    for capital in capitals:
        results.append(simulate_cross(store, candidates, capital, p))
        results[-1].lots.write_parquet(out / f"cross_lots_{capital:g}.parquet", compression="zstd")
        lines.append(format_result(results[-1]))
    table = format_table(pair_table(summary, results))
    (out / "cross_table.md").write_text(table + "\n")
    return [*lines, table, f"candidate log, bought units and the pair table: {out}"]
