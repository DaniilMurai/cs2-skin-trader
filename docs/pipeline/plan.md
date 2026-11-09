# Plan

All commands below are the intended CLI (`cs2 ...`); exact names are fixed at stage 4.
"Holdout" = 2025-10-01 → the last collected day: the October 2025 crash and the 2026
decline. Nothing in the holdout is used to choose rules or parameters.

## Steps
1. **Data collection** — backfill multi-year daily Steam history for the item universe;
   start the hourly market.csgo collector as early as possible, because every week it
   runs is a week of forward data we can't get later. Done = AC-1, AC-2, AC-3.
2. **Data model of the market** — item categories (knife, glove, rifle skin, case,
   sticker capsule, ...), a point-in-time universe, a market index and per-category
   indices, regime labels (rising / falling / flat), and the Steam→market.csgo price ratio.
   Done = AC-4, AC-5, AC-6.
3. **Backtester with the market.csgo cost model** — daily fills with a spread penalty,
   volume caps, the 7-day lock, the 5% seller fee, the withdrawal fee, the proceeds hold,
   no shorting, two profit units. Done = AC-7, AC-8, AC-9.
4. **Strategies and benchmarks** — the video's V1 (dip buying in knives/gloves, with and
   without a stop-loss) and V2 (category momentum); then trend-following/go-to-cash,
   cross-sectional momentum, mean reversion; benchmarks: cash, buy-and-hold of the whole
   universe, buy-and-hold of knives/gloves. Done = AC-10.
5. **Evaluation and report** — walk-forward on the history before the holdout, one run on
   the holdout, results per regime, cost and fill sensitivity, capital $100 and $1,000,
   and a verdict per strategy and capital by a fixed rule. Done = AC-11, AC-12, AC-13.
6. **Forward paper test on market.csgo data** — run the same strategies hour by hour on
   our own collected snapshots, no real orders. Done = AC-14.

## Acceptance criteria

AC-1: Given — the universe list and a valid `STEAM_LOGIN_SECURE` in `.env`.
      When — `cs2 backfill steam` runs to completion (resumable after interruption).
      Then — `cs2 data check` reports ≥ 95% of universe items with history; each item's
      first date is within 7 days of its first Steam sale date; no item has duplicate
      dates; the run log shows no request rate above 24 requests/min (spike S-01);
      every stored price has both the raw wallet-currency value (UAH) and a USD value
      = raw / the NBU UAH→USD rate on the item's fetch day (Steam converts the whole
      history at one current rate, spike S-08), and no item lacks a rate.

AC-2: Given — the backfill was interrupted (process killed) halfway.
      When — `cs2 backfill steam` is run again.
      Then — it skips already-stored items, and the final row count equals that of an
      uninterrupted run on a 20-item test universe.

AC-3: Given — the hourly collector has been scheduled for 48 hours.
      When — `cs2 collect status` runs.
      Then — it shows ≥ 44 market.csgo snapshots in the last 48 h (each = the lowest-ask
      file `prices/USD.json` + the best-bid file `prices/orders/USD.json`), each with ≥ 90%
      of the item count of the largest snapshot, and lists every gap longer than 2 h with its
      start and end time.

AC-4: Given — the stored universe.
      When — `cs2 data check --categories` runs.
      Then — ≤ 1% of items are in category `unknown`, and a fixed test list of 20 named
      items (e.g. `★ Karambit | Doppler (Factory New)` → knife,
      `Recoil Case` → case) maps exactly as expected.

AC-5: Given — the Steam history.
      When — `cs2 regimes` runs.
      Then — every day from the first index day to the last has exactly one regime label,
      and the week of the October 2025 trade-up crash is labelled `falling`; labels for
      all days ≤ t are identical when data after t is deleted (a test truncates the data
      at a random t).

AC-6: Given — ≥ 14 days of market.csgo snapshots.
      When — `cs2 ratio` runs.
      Then — it prints, per category, the median ratio of the market.csgo lowest ask and
      of the market.csgo best bid to the Steam daily price, its
      interquartile range and the number of items it's based on; a category with fewer
      than 20 items falls back to the all-items ratio and says so.

AC-7: Given — a synthetic price series and a strategy that buys on day 0 and asks to sell
      every day.
      When — the backtest runs (unit test).
      Then — no sale happens before day 7; the sale on day 7 credits
      price × (1 − 0.05) to the market.csgo balance, usable for a new purchase the same
      day; cash-out equals that minus the withdrawal fee and is available no earlier than
      sale day + 8 (market.csgo Terms §3.7); all numbers
      match hand-computed values to the cent.

AC-8: Given — an item with daily volume 10 and a participation cap of 20%.
      When — a strategy orders 5 units in one day (unit test).
      Then — 2 are filled that day, the rest is cancelled (not carried), and a sell order
      is never filled above the day's price minus the spread penalty.

AC-9: Given — any strategy and the full history.
      When — the look-ahead test deletes all data after a random day t and re-runs.
      Then — all decisions on days ≤ t are identical to the full run. Also: a strategy can
      never hold a negative quantity or spend more than its available balance
      (property test over random strategies).

AC-10: Given — the strategy registry.
       When — `cs2 backtest --strategy <name> --capital 100 --from 2019-01-01 --to 2025-09-30`
       (and again with `--capital 1000`) runs for every registered strategy and benchmark.
       Then — each writes a trade log and a daily equity curve in both profit units, and
       V1/V2 each have a written rule spec in the report appendix.

AC-11: Given — all strategies.
       When — `cs2 report` runs.
       Then — it writes `reports/<date>/report.html` with, per strategy × regime × capital,
       net return, max drawdown, number of trades and fees paid, in both profit units,
       for the walk-forward period and the holdout separately.

AC-12: Given — the report.
       When — the verdict is computed.
       Then — the verdict is computed separately for $100 and for $1,000. A strategy is
       marked **holds up** at a given capital only if, on the holdout in cash-out units: net return > cash and > buy-and-hold; max drawdown ≤ buy-and-hold's; the
       return stays above both benchmarks with costs +50% and the spread penalty doubled;
       and its walk-forward Sharpe stays positive after a deflation for the number of
       variants tried. Otherwise **fails**, with the first rule it broke. If none holds
       up, the report's first line says so.

AC-13: Given — the same data snapshot and config.
       When — `cs2 report` runs twice.
       Then — both reports have identical numbers (a hash of the results table matches).

AC-14: Given — ≥ 28 days of hourly market.csgo snapshots.
       When — `cs2 forward --strategy <name>` runs.
       Then — it writes paper results (trades, equity, fees) using only snapshots up to
       each decision hour, and the report gets a "forward" column next to the backtest
       results for the same dates.

## Non-goals
- Real-money trading, placing or cancelling orders on market.csgo or Steam.
- Paid data APIs.
- Float, pattern, sticker premiums; each `market_hash_name` is one fungible item.
- Intraday strategies on the past (no hourly history before our collector starts).
- A web UI or a hosted service: CLI + a static HTML report.

## Spikes (stage 3) — answered, see spikes/FINDINGS.md
- S-01: Does Steam `pricehistory` return multi-year daily price + volume with the author's
  cookie? How deep, what granularity, which price (median?), what rate before throttling,
  how long would a full backfill take?
- S-02: market.csgo `prices/USD.json`: is `price` the lowest listing or a sale price,
  what period does `volume` cover, how often does it change, any rate limit? Is there a
  free endpoint with recent sales history?
- S-03: market.csgo rules: can a trade-locked item be listed; how long are proceeds held;
  the exact withdrawal fee by method (official pages).
- S-04: Where do categories come from — parsing names, or a free item database
  (e.g. an open CS2 items dataset), and how complete is it?
- S-05: Do duckdb and polars install and run on Python 3.14 with `uv`, or do we pin 3.13?
- S-06: Can the hourly collector run reliably on this Mac (sleep, launchd), or does it
  need an always-on machine?
