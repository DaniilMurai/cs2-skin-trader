"""Plain-language layer of the UI: turns the report's codes and numbers into sentences.

Presentation only. Every sentence is built from numbers the report, the runs or the database already
hold; nothing here changes a result or re-computes a verdict."""
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from cs2trader.config import Settings

BENCHMARKS = ("cash", "bh_universe", "bh_median", "bh_knives_gloves")

# AC-12's rules in the order the verdict checks them.
RULES: dict[str, dict[str, str]] = {
    "beats_cash": {
        "question": "Did it end with more money than doing nothing?",
        "fail": "Did worse than just holding cash.",
        "ok": "Ended with more than just holding cash.",
    },
    "beats_bh": {
        "question": "Did it end with more money than buying a basket of skins and holding it?",
        "fail": "Did worse than buying and holding a basket of skins.",
        "ok": "Ended with more than buying and holding.",
    },
    "drawdown": {
        "question": "Was its worst dip no deeper than buy-and-hold's?",
        "fail": "Lost more at its worst point than buy-and-hold did.",
        "ok": "Its worst dip was no deeper than buy-and-hold's.",
    },
    "cost_stress": {
        "question": "Does it still win if trading costs are higher than we assumed?",
        "fail": "Stopped beating the benchmarks when fees were 50 % higher and the spread penalty doubled.",
        "ok": "Still beat both benchmarks with fees 50 % higher and the spread penalty doubled.",
    },
    "deflated_sharpe": {
        "question": "Is the result more than luck from trying many variants?",
        "fail": "Looks like luck: too many variants were tried for a result this size.",
        "ok": "Stays positive after discounting for the number of variants tried.",
    },
}

STRATEGIES: dict[str, dict[str, str]] = {
    "cash": {
        "label": "Hold cash",
        "summary": "Do nothing: keep the starting balance as cash for the whole period. It is the yardstick "
                   "every strategy must beat. It still shows a small loss because withdrawing money from "
                   "market.csgo costs a fee.",
    },
    "bh_universe": {
        "label": "Buy and hold (all skins)",
        "summary": "On day one, spread the money equally over up to 25 tradable skins and never sell. "
                   "It shows what the market itself did; a strategy that cannot beat it adds nothing.",
    },
    "bh_knives_gloves": {
        "label": "Buy and hold (knives and gloves)",
        "summary": "Same as buy and hold, but the basket is picked only from knives and gloves, the "
                   "expensive end of the market.",
    },
    "v1_dip_knives": {
        "label": "Buy the dip in knives and gloves (video V1)",
        "summary": "From the video, for a falling market: when a knife or glove has dropped a set amount "
                   "over the past week, buy it; sell when it has gained a set amount, when it has been held "
                   "too long, or (optionally) when it keeps falling past a stop-loss.",
    },
    "v2_category_momentum": {
        "label": "Follow the rising categories (video V2)",
        "summary": "From the video, for a rising market: every week or two, find the item categories that "
                   "grew the most recently and buy their best performers, investing more the stronger the "
                   "rise. Categories that stop rising are sold.",
    },
    "xs_momentum": {
        "label": "Buy recent winners (momentum)",
        "summary": "Every few weeks, rank all tradable skins by how much they rose over a look-back period, "
                   "hold the top few in equal amounts and sell the ones that dropped out of the top.",
    },
    "xs_momentum_liquid": {
        "label": "Buy recent winners, liquid items only, with a stop",
        "summary": "Like buying recent winners, but only among items that sell at least 20 times a day and "
                   "cost $1 or more, ranked on weekly median prices. When the account falls a set share "
                   "below its peak, everything is sold and the strategy waits before buying again.",
    },
    "cheap_flip": {
        "label": "Flip cheap items bought below their usual price",
        "summary": "Bid for skins under $5 that sell at least 20 times a day, but only where selling at the "
                   "item's usual price (its 30-day median) would still leave a profit after every fee. "
                   "Sell when the 7-day trade lock ends, or wait a while for the usual price to come back.",
    },
    "trend_ma": {
        "label": "Follow the trend, else go to cash",
        "summary": "Stay invested in the most-traded skins only while the whole market is above its own "
                   "recent average price; when the market falls below that average, sell everything and wait "
                   "in cash.",
    },
    "mean_reversion": {
        "label": "Buy overshoots (mean reversion)",
        "summary": "Buy a skin when its price falls unusually far below its own recent average, betting it "
                   "bounces back; sell when it returns to that average or after a maximum holding time.",
    },
    "quality_basket": {
        "label": "Hold liquid, growing items (quality basket)",
        "summary": "Pick the items that trade the most and whose trading is not shrinking, hold them, and "
                   "re-pick only every few months, so fees are paid rarely.",
    },
    "case_dip": {
        "label": "Buy cases in a falling market",
        "summary": "While the whole market is falling, buy the most-traded cases; sell them once the market "
                   "is rising again.",
    },
    "cheap_reversion": {
        "label": "Buy cheap overshoots (cheap mean reversion)",
        "summary": "Like “buy overshoots”, but only for cheap skins that have to rise at least a set amount "
                   "to get back to normal, so that the round-trip fees of about 11 % can be earned back.",
    },
    "event": {
        "label": "Trade around events",
        "summary": "Trade around known calendar events: hold a Major tournament's sticker capsules around "
                   "the tournament, or buy a case on the day it leaves the active drop pool and sell it later.",
    },
}

FAMILIES = {"benchmark": "Yardstick, not a strategy", "video": "From the video", "trend": "Trend following",
            "momentum": "Momentum", "mean_reversion": "Mean reversion", "event": "Events",
            "low_turnover": "Few trades", "flip": "Quick resale"}

# (strategy, key) -> (label, unit, help). unit: "share" = a fraction shown as a percent, "days", "count", "".
PARAMS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("v1_dip_knives", "drop_7d"): ("Buy after a 7-day drop of", "share", "How far the price must have fallen over the last week before the item is bought."),
    ("v1_dip_knives", "max_positions"): ("Items held at once", "count", "The money is split into this many equal slots."),
    ("v1_dip_knives", "take_profit"): ("Sell after a gain of", "share", "Sell once the price is this far above the buy price."),
    ("v1_dip_knives", "max_hold"): ("Sell after at most", "days", "Sell an item held this long even without a gain."),
    ("v1_dip_knives", "stop_loss"): ("Stop-loss (the video's “protection”)", "share", "Sell once the price is this far below the buy price. Off = never sell at a loss early."),
    ("v2_category_momentum", "window"): ("Measure growth over", "days", "How far back a category's growth is measured."),
    ("v2_category_momentum", "top_k"): ("Categories to follow", "count", "How many of the fastest-growing categories are bought."),
    ("v2_category_momentum", "full_growth"): ("Growth for a full position", "share", "A category that grew this much gets its full share of the money; less growth gets proportionally less."),
    ("v2_category_momentum", "rebalance_days"): ("Re-pick every", "days", "How often the categories are re-ranked."),
    ("xs_momentum", "lookback"): ("Rank by the rise over", "days", "The look-back period for ranking items."),
    ("xs_momentum", "k"): ("Items held", "count", "How many of the top-ranked items are held."),
    ("xs_momentum", "rebalance"): ("Re-rank every", "days", "How often the ranking is refreshed."),
    ("xs_momentum_liquid", "lookback"): ("Rank by the rise over", "days", "The look-back period for ranking items."),
    ("xs_momentum_liquid", "rebalance"): ("Re-rank every", "days", "How often the ranking is refreshed."),
    ("xs_momentum_liquid", "stop"): ("Sell everything after a fall of", "share", "How far the account may fall below its peak before the strategy goes to cash."),
    ("cheap_flip", "hurdle"): ("Profit after fees of at least", "share", "What a sale at the usual price must leave after all fees, compared with the buy price."),
    ("cheap_flip", "discount"): ("Bid below the recent price by", "share", "The buy order is placed this far under the item's 3-day median price; it fills only if the price comes down to it."),
    ("cheap_flip", "max_hold"): ("Sell after at most", "days", "7 = sell as soon as the trade lock ends. Longer = wait for the usual price, at most this many days."),
    ("trend_ma", "ma_days"): ("Average the market over", "days", "The market must be above its average of this many days to stay invested. Longer = slower to react."),
    ("trend_ma", "n_items"): ("Items held", "count", "How many of the most-traded items are held while invested."),
    ("quality_basket", "rebalance_days"): ("Re-pick the items every", "days", "How often the basket is chosen again."),
    ("quality_basket", "n_items"): ("Items held", "count", "The number of items in the basket."),
    ("case_dip", "n_cases"): ("Cases held", "count", "How many different cases are bought."),
    ("case_dip", "min_hold"): ("Sell no earlier than", "days", "A case is held at least this long before a rising market sells it."),
    ("cheap_reversion", "z"): ("How unusual the drop must be", "", "In standard deviations below the recent average: 2 is a rare drop, 3 a very rare one."),
    ("cheap_reversion", "min_move"): ("Expected rise of at least", "share", "The average it should return to must be this far above the price."),
    ("mean_reversion", "z"): ("How unusual the drop must be", "", "In standard deviations below the recent average: 2 is a rare drop, 3 a very rare one."),
    ("mean_reversion", "lookback"): ("Recent average over", "days", "The period used for an item's normal price and its normal swings."),
    ("event", "rule"): ("Which events", "", "majors = tournament sticker capsules; cases = cases leaving the active drop pool."),
    ("event", "pre"): ("Buy before the Major by", "days", "Only for majors: how early the capsules are bought."),
    ("event", "post"): ("Hold after the Major for", "days", "Only for majors: how long the capsules are kept after the tournament ends."),
    ("event", "hold"): ("Hold a case for", "days", "Only for cases: how long a case is kept after it leaves the drop pool."),
}

GROUP_INTROS = {
    "Costs and fills": "What trading costs and how much we assume can actually be bought or sold. These are the "
                       "assumptions that decide whether a paper profit survives in real life.",
    "Regime and universe": "Which skins count as tradable, and when the market counts as rising or falling.",
    "Report windows": "Which years the strategies are tuned and tested on, and with how much starting money.",
}
EFFECT_RESULTS = "Changes every result; runs made before the change keep their old run ID."
EFFECT_REPORT = "Applies to the next `cs2 report`; single runs from the Try a strategy page are not affected."
EFFECT_REGIMES = "Applies after `cs2 regimes` is run again, then to the next report."

# key -> (label, unit, help, effect)
SETTINGS_TEXT: dict[str, tuple[str, str, str, str]] = {
    "costs.seller_fee": ("Seller fee", "share", "Cut market.csgo takes from every sale.", EFFECT_RESULTS),
    "costs.buyer_fee": ("Buyer fee", "share", "Extra charged on every purchase (market.csgo charges none).", EFFECT_RESULTS),
    "costs.withdraw_fee_cash": ("Withdrawal fee (cash-out)", "share", "Lost when money is withdrawn from market.csgo to a bank or card. This is why even holding cash shows a loss.", EFFECT_RESULTS),
    "costs.withdraw_fee_steam": ("Conversion cost (Steam wallet)", "share", "Lost when the result is counted as Steam wallet credit instead of withdrawn cash.", EFFECT_RESULTS),
    "costs.lock_days": ("Trade lock after buying", "days", "A bought skin cannot be sold for this many days.", EFFECT_RESULTS),
    "costs.cashout_hold_days": ("Wait before sale money can be withdrawn", "days", "Sale proceeds are held this long before they can leave market.csgo.", EFFECT_RESULTS),
    "fills.participation_cap": ("Share of daily volume we can take", "share", "At most this part of an item's traded volume on a day can be bought or sold; the rest of an order is cancelled.", EFFECT_RESULTS),
    "fills.spread_penalty": ("Spread penalty", "share", "We assume buying costs this much above, and selling this much below, the recorded price.", EFFECT_RESULTS),
    "fills.ratio_source": ("Steam → market.csgo price ratio", "", "fixed = use the fallback ratio below; measured = use the ratio measured from our own snapshots.", EFFECT_RESULTS),
    "fills.ratio_fallback": ("Fallback price ratio", "x", "market.csgo price as a multiple of the Steam price when no measured ratio exists. 1 = same price.", EFFECT_RESULTS),
    "universe.min_history_days": ("Minimum price history", "days", "A skin is tradable only once it has this much history.", EFFECT_RESULTS),
    "universe.min_median_volume": ("Minimum typical daily sales", "per day", "Skins that sell less often than this are ignored: they could not be bought or sold in practice.", EFFECT_RESULTS),
    "universe.min_price_usd": ("Minimum price", "$", "Cheaper skins are ignored.", EFFECT_RESULTS),
    "regimes.lookback_days": ("Look-back for rising / falling", "days", "The market's change over this many days decides its label.", EFFECT_REGIMES),
    "regimes.threshold": ("Change that counts as a move", "share", "Up more than this = rising, down more than this = falling, in between = flat.", EFFECT_REGIMES),
    "wf.start": ("Tuning period starts", "date", "First day of the history used to tune and test strategies.", EFFECT_REPORT),
    "wf.end": ("Tuning period ends", "date", "Last day strategies may be tuned on. Must be before the final test period.", EFFECT_REPORT),
    "wf.train_days": ("Tune on the previous", "days", "Each round picks the best settings on this much past data …", EFFECT_REPORT),
    "wf.test_days": ("… then test on the next", "days", "… and tests them on this much unseen data, then moves forward.", EFFECT_REPORT),
    "wf.holdout_start": ("Final test period (holdout) starts", "date", "From this day on, the data is never used for tuning; the verdict is judged here.", EFFECT_REPORT),
    "capitals": ("Starting amounts", "$", "Each amount is run and judged separately, comma-separated.", EFFECT_REPORT),
}
READ_ONLY_TEXT = {
    "verdict.cost_stress": ("Cost stress test", "x", "To hold up, a strategy must still win with every fee multiplied by this."),
    "verdict.spread_stress": ("Spread stress test", "x", "… and with the spread penalty multiplied by this."),
}
READ_ONLY_WHY = ("The pass / fail rule was fixed before any result was seen, on purpose: if it could be edited "
                 "here, it could be tuned until a strategy looks good.")


def money(v: float) -> str:
    sign = "−" if v < 0 else ""
    v = abs(v)
    return f"{sign}${v:,.0f}" if v >= 100 or float(v).is_integer() else f"{sign}${v:,.2f}"


def pct(v: float, signed: bool = True) -> str:
    # A no-break space keeps the number and its % sign on one line.
    v = round(v * 100, 1)
    if v == 0:
        return "0.0\u00a0%"
    return f"{'+' if v > 0 and signed else '−' if v < 0 else ''}{abs(v):,.1f}\u00a0%"


def day_text(d: date | datetime | str) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return f"{d:%b} {d.day}, {d.year}"


def became(capital: float, ret: float) -> str:
    return f"{money(capital)} became {money(capital * (1 + ret))} ({pct(ret)})"


# Report-only rows that are not registered strategies.
REPORT_ONLY = {
    "bh_median": {
        "label": "Buy and hold (typical basket)",
        "summary": "The same buy and hold, run on 15 different random baskets; this is the basket with the "
                   "middle result. One basket can be lucky, so strategies are judged against this one.",
    },
}


def strategy_label(name: str) -> str:
    return {**STRATEGIES, **REPORT_ONLY}.get(name, {}).get("label", name)


# ---------------------------------------------------------------- verdict

def _row(results: pl.DataFrame, strategy: str, capital: float, period: str, regime: str = "all") -> dict | None:
    rows = results.filter((pl.col("strategy") == strategy) & (pl.col("capital") == capital)
                          & (pl.col("period") == period) & (pl.col("regime") == regime)
                          & (pl.col("unit") == "cash")).to_dicts()
    return rows[0] if rows else None


def _checks(v: dict | None, row: dict, stress: dict | None, cash: dict, bh: dict) -> list[dict]:
    """One entry per rule. `ok` comes from the same holdout numbers the verdict used; the official first
    broken rule always wins, so a rounding difference can never contradict the report."""
    computed: dict[str, bool | None] = {
        "beats_cash": row["net_return"] > cash["net_return"],
        "beats_bh": row["net_return"] > bh["net_return"],
        "drawdown": row["max_drawdown"] <= bh["max_drawdown"],
        "cost_stress": None if stress is None else (stress["net_return"] > cash["net_return"]
                                                    and stress["net_return"] > bh["net_return"]),
        "deflated_sharpe": None if v is None or v.get("deflated_sharpe") is None else v["deflated_sharpe"] > 0,
    }
    if v is not None:
        broken = v.get("first_broken_rule")
        order = list(RULES)
        for i, code in enumerate(order):
            if broken is None or (broken in order and i < order.index(broken)):
                computed[code] = True
            elif code == broken:
                computed[code] = False
    out = []
    for code, text in RULES.items():
        ok = computed[code]
        out.append({"code": code, "question": text["question"], "ok": ok,
                    "text": "Not checked in this report." if ok is None else text["ok" if ok else "fail"]})
    return out


def scoreboard(results: pl.DataFrame, verdicts: list[dict] | None, settings: Settings) -> list[dict]:
    """Per starting amount: the benchmarks and one card per strategy, all from the holdout rows in
    cash-out units (the rows AC-12 judges)."""
    out = []
    by_key = {(v["strategy"], float(v["capital"])): v for v in verdicts or []}
    for capital in sorted(results["capital"].unique().to_list()):
        cash = _row(results, "cash", capital, "holdout")
        bh_name = "bh_median" if _row(results, "bh_median", capital, "holdout") is not None else "bh_universe"
        bh = _row(results, bh_name, capital, "holdout")
        names = [n for n in results.filter(pl.col("capital") == capital)["strategy"].unique().sort().to_list()
                 if n not in BENCHMARKS]
        names += [s for (s, c) in by_key if c == capital and s not in names and s not in BENCHMARKS]
        cards = []
        for name in names:
            v = by_key.get((name, capital))
            row = _row(results, name, capital, "holdout")
            card: dict[str, Any] = {
                "strategy": name, "label": strategy_label(name), "capital": capital,
                "status": v["status"] if v else None,
                "first_broken_rule": v.get("first_broken_rule") if v else None,
                "deflated_sharpe": v.get("deflated_sharpe") if v else None,
                "n_variants": v.get("n_variants") if v else None,
                "net_return": row["net_return"] if row else None,
                "max_drawdown": row["max_drawdown"] if row else None,
                "end_value": capital * (1 + row["net_return"]) if row else None,
                "n_trades": row["n_trades"] if row else None,
                "fees_paid": row["fees_paid"] if row else None,
                "regimes": {}, "checks": [],
            }
            for regime in ("rising", "falling", "flat"):
                r = _row(results, name, capital, "holdout", regime)
                if r:
                    card["regimes"][regime] = r["net_return"]
            if row and cash and bh:
                card["checks"] = _checks(v, row, _row(results, name, capital, "holdout_stress"), cash, bh)
            card["result"] = (f"{became(capital, row['net_return'])} in the final test period; its worst dip "
                              f"was {pct(-row['max_drawdown'])}." if row
                              else "No final-test-period numbers for this strategy in the report.")
            if v is None:
                card["reason"] = ""
            elif v["status"] == "holds_up":
                card["reason"] = "Passed all five checks."
            else:
                rule = RULES.get(v.get("first_broken_rule") or "", {})
                card["reason"] = rule.get("fail", f"Failed the check “{v.get('first_broken_rule')}”.")
            cards.append(card)
        # Passing strategies first, then by result: the reader wants the best news at the top.
        cards.sort(key=lambda c: (c["status"] != "holds_up", -(c["net_return"] if c["net_return"] is not None else -9)))
        bench = {}
        for key, name in (("cash", "cash"), ("bh", bh_name)):
            r = cash if key == "cash" else bh
            bench[key] = None if r is None else {
                "strategy": name, "label": strategy_label(name), "net_return": r["net_return"],
                "max_drawdown": r["max_drawdown"], "end_value": capital * (1 + r["net_return"]),
                "result": f"{became(capital, r['net_return'])}."}
        holds = [c["strategy"] for c in cards if c["status"] == "holds_up"]
        judged = [c for c in cards if c["status"] is not None]
        if not judged:
            line = "This report has no pass / fail verdicts."
        elif holds:
            line = (f"{len(holds)} of {len(judged)} strategies hold{'s' if len(holds) == 1 else ''} up with {money(capital)}: "
                    f"{', '.join(strategy_label(h) for h in holds)}.")
        else:
            line = f"None of the {len(judged)} strategies holds up with {money(capital)}."
        out.append({"capital": capital, "holds": holds, "n_judged": len(judged), "headline": line,
                    "benchmarks": bench, "cards": cards,
                    "holdout_start": settings.wf.holdout_start.isoformat()})
    return out


def report_freshness(day: str, has_verdicts: bool, hash_ok: bool | None, last_day: str | None) -> dict:
    """Why the shown report may no longer describe the current data. Derived from files only."""
    reasons = []
    if not has_verdicts:
        reasons.append({"code": "no_verdicts",
                        "text": "This report was made by an older version of the tool and has no pass / fail "
                                "verdicts, only the raw results."})
    if hash_ok is False:
        reasons.append({"code": "hash",
                        "text": "The results file does not match the report it was saved with (it was changed "
                                "afterwards)."})
    if last_day and last_day > day:
        n = (date.fromisoformat(last_day) - date.fromisoformat(day)).days
        reasons.append({"code": "newer_data",
                        "text": f"The price history now runs to {day_text(last_day)}, {n} day{'s' if n != 1 else ''} "
                                f"past this report."})
    return {"stale": bool(reasons), "reasons": reasons,
            "advice": "Run `cs2 report` in a terminal to rebuild it from the current data." if reasons else None}


def verdict_headline(boards: list[dict], has_verdicts: bool) -> dict:
    if not has_verdicts:
        return {"status": "unknown", "headline": "No verdict yet",
                "detail": "The newest report has results but no pass / fail verdicts."}
    holds = [(b["capital"], b["holds"]) for b in boards if b["holds"]]
    amounts = " or ".join(money(b["capital"]) for b in boards)
    if not holds:
        n = max((b["n_judged"] for b in boards), default=0)
        return {"status": "none", "headline": "No strategy is good enough to use",
                "detail": f"All {n} strategies failed at least one check with {amounts}. That is a valid research "
                          f"result: on this data, none of them beats simply holding cash or holding skins after "
                          f"real fees."}
    parts = [f"{', '.join(strategy_label(h) for h in names)} with {money(c)}" for c, names in holds]
    return {"status": "some", "headline": "A strategy passed every check",
            "detail": f"Passed all five checks: {'; '.join(parts)}. This is a backtest on past prices, not a "
                      f"promise; nothing here trades real money."}


# ---------------------------------------------------------------- market

def market_now(points: list[dict], lookback_days: int, threshold: float) -> dict | None:
    """Words for the last regime row. `ret` is the index change over the look-back window."""
    if not points:
        return None
    last = points[-1]
    since = last["t"]
    for p in reversed(points):
        if p["regime"] != last["regime"]:
            break
        since = p["t"]
    year_ago = None
    target = (date.fromisoformat(last["t"][:10]) - timedelta(days=365)).isoformat()
    older = [p for p in points if p["t"][:10] <= target]
    if older and older[-1]["index_level"]:
        year_ago = last["index_level"] / older[-1]["index_level"] - 1
    word = {"rising": "rising", "falling": "falling", "flat": "flat"}.get(last["regime"], last["regime"])
    line = (f"The typical tradable skin is {'up' if last['ret'] >= 0 else 'down'} "
            f"{pct(abs(last['ret']), signed=False)} over the last {lookback_days} days.")
    rule = (f"More than {pct(threshold)} counts as rising, below {pct(-threshold)} as falling, "
            f"anything in between as flat.")
    return {"regime": last["regime"], "as_of": last["t"], "since": since, "change": last["ret"],
            "change_1y": year_ago, "lookback_days": lookback_days, "threshold": threshold,
            "headline": f"The market is {word}", "detail": line, "rule": rule,
            "year": None if year_ago is None else (f"Over the last 12 months it is {'up' if year_ago >= 0 else 'down'} "
                                                   f"{pct(abs(year_ago), signed=False)}.")}


# ---------------------------------------------------------------- collector

def _hours(n: float) -> str:
    n = round(n)
    return f"{n} hour{'s' if n != 1 else ''}"


def collector_summary(c: dict) -> dict:
    """Traffic light for the hourly market.csgo collector, from /api/collector's own numbers."""
    if c.get("state") != "ok":
        return {"status": "grey", "headline": "No collector data yet",
                "detail": "No hourly market.csgo snapshots are stored on this computer.", "points": [],
                "advice": "Run `cs2 collect pull` to fetch the snapshots collected so far."}
    age, snaps, target, window = c.get("age_hours"), c["snapshots"], c["target"], c["window_h"]
    gaps = []
    for a, b in c["gaps"]:
        start, end = datetime.fromisoformat(a), datetime.fromisoformat(b)
        gaps.append(f"{_hours((end - start).total_seconds() / 3600)} missing from {start:%b} {start.day}, "
                    f"{start:%H:%M} to {end:%b} {end.day}, {end:%H:%M} UTC")
    points = [f"{snaps} of {window} hourly snapshots arrived in the last {window} hours "
              f"(at least {target} are expected)."]
    points += [g + "." for g in gaps]
    if c["low_share_hours"]:
        n = len(c["low_share_hours"])
        points.append(f"{n} snapshot{'s' if n != 1 else ''} covered clearly fewer items than usual "
                      f"(the smallest had {pct(c['min_item_share'], signed=False)} of the largest).")
    stale = age is not None and age > 3
    if stale or snaps == 0:
        status = "red"
        when = "no snapshot at all" if age is None else f"the newest snapshot is {_age(age)} old"
        headline = f"Collector data on this computer is behind: {when}"
        advice = ("Run `cs2 collect pull` to fetch what the hourly collector has gathered; if that brings "
                  "nothing new, the collector itself has stopped.")
    elif snaps < target or gaps or c["low_share_hours"]:
        status = "amber"
        missing = window - snaps
        headline = (f"Collecting, with holes: {_hours(missing)} missing in the last {window} hours"
                    if missing else "Collecting, but some snapshots were incomplete")
        advice = "Missed hours cannot be recovered; check the collector's schedule if the holes repeat."
    else:
        status = "green"
        headline = f"Collecting normally: newest snapshot {_age(age)} ago" if age is not None else "Collecting normally"
        advice = None
    return {"status": status, "headline": headline, "detail": points[0], "points": points, "advice": advice,
            "missing_hours": window - snaps}


def _age(hours: float) -> str:
    if hours < 1:
        return f"{max(round(hours * 60), 1)} min"
    if hours < 48:
        return _hours(hours)
    return f"{round(hours / 24)} days"


# ---------------------------------------------------------------- runs

def run_summary(spec: dict, equity: list[dict], n_filled: int, n_orders: int, costs: dict | None) -> dict | None:
    if not equity:
        return None
    capital = spec["capital"]
    last = equity[-1]
    worst = min(equity, key=lambda e: e["drawdown_cash"])
    ret_cash, ret_steam = last["equity_cash"] / capital - 1, last["equity_steam"] / capital - 1
    text = (f"Started with {money(capital)} on {day_text(spec['start'])} and ended with "
            f"{money(last['equity_cash'])} ({pct(ret_cash)}) on {day_text(last['t'])}, counted as cash you "
            f"could withdraw after all fees.")
    if worst["drawdown_cash"] < 0:
        dip = (f"The worst dip was {pct(worst['drawdown_cash'])} below the best value reached before it "
               f"(on {day_text(worst['t'])}).")
    else:
        dip = "The value never fell below an earlier high."
    trades = (f"It made {n_filled:,} trade{'s' if n_filled != 1 else ''} and paid {money(last['fees_paid'])} in fees."
              if n_filled else "It made no trades.")
    notes = []
    if n_orders > n_filled:
        notes.append(f"{n_orders - n_filled:,} of {n_orders:,} orders bought or sold nothing; the trade list "
                     f"says why for each.")
    fee = (costs or {}).get("withdraw_fee_cash")
    if not n_filled and fee and abs(ret_cash + fee) < 1e-9:
        notes.append(f"The loss is the {pct(fee, signed=False)} fee for withdrawing the money from market.csgo.")
    return {"capital": capital, "end_cash": last["equity_cash"], "end_steam": last["equity_steam"],
            "return_cash": ret_cash, "return_steam": ret_steam, "worst_dip": worst["drawdown_cash"],
            "worst_dip_day": worst["t"], "fees_paid": last["fees_paid"], "n_filled": n_filled,
            "n_orders": n_orders, "outcome": "gain" if ret_cash > 0 else "loss" if ret_cash < 0 else "even",
            "text": text, "dip": dip, "trades": trades, "notes": notes,
            "steam": f"Counted as Steam wallet credit instead: {money(last['equity_steam'])} ({pct(ret_steam)})."}


# ---------------------------------------------------------------- home

def data_block(overview: dict, today: date | None = None) -> dict:
    if overview.get("state") == "busy":
        return {"status": "grey", "headline": "The database is busy", "detail": overview["message"], "advice": None}
    if overview.get("state") != "ok" or not overview.get("last_day"):
        return {"status": "red", "headline": "No price history yet",
                "detail": "The database has no Steam price history.",
                "advice": "Run `cs2 backfill steam` to download it (a one-off job that takes hours)."}
    today = today or datetime.now(UTC).date()
    age = (today - date.fromisoformat(overview["last_day"])).days
    detail = (f"Daily Steam prices for {overview['items_with_history']:,} items, up to "
              f"{day_text(overview['last_day'])}.")
    if age <= 3:
        return {"status": "green", "headline": "Price history is up to date", "detail": detail, "advice": None}
    return {"status": "amber", "headline": f"Price history ends {age} days ago", "detail": detail,
            "advice": "Run `cs2 backfill steam` to add the newest days, then `cs2 regimes` and `cs2 report`."}


def next_steps(verdict: dict, data: dict, coll: dict, job_running: bool) -> list[dict]:
    """Concrete suggestions, most urgent first. Each comes from a state shown elsewhere on the page."""
    steps: list[dict] = []
    if job_running:
        steps.append({"text": "A backtest is running right now.", "link": "#/runs", "link_text": "See its progress"})
    if data.get("advice"):
        steps.append({"text": f"{data['headline']}. {data['advice']}"})
    if verdict.get("state") == "empty":
        steps.append({"text": ("There is no report yet. Run `cs2 report` to test every strategy and get a "
                               "verdict (this takes a while).")})
    elif verdict.get("freshness", {}).get("stale"):
        why = " ".join(r["text"] for r in verdict["freshness"]["reasons"])
        steps.append({"text": f"The report is out of date. {why} {verdict['freshness']['advice']}",
                      "link": "#/verdict", "link_text": "Open the verdict"})
    if coll.get("advice") and coll["status"] in ("red", "amber", "grey"):
        steps.append({"text": f"{coll['headline']}. {coll['advice']}", "link": "#/collector",
                      "link_text": "Open collector health"})
    if not steps:
        steps.append({"text": "Everything is current. Nothing needs doing."})
    steps.append({"text": "Curious how one strategy behaves? Try it on any dates.", "link": "#/runs",
                  "link_text": "Try a strategy", "optional": True})
    return steps
