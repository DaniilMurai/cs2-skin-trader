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
7. **Local UI** (added 2026-11-11 at the author's request) — `cs2 ui` serves a web app on
   localhost to see the verdict, one run's equity curve and trades, the market index with
   regimes, and the collector's health, and to edit settings and start backtests. Done =
   AC-15, AC-16, AC-17, AC-18.

## Acceptance criteria

AC-1: Given — the universe list and a valid `STEAM_LOGIN_SECURE` in `.env`.
      When — `cs2 backfill steam` runs to completion (resumable after interruption).
      Then — `cs2 data check` reports ≥ 95% of universe items with history; no item has
      duplicate dates; the request log shows no HTTP 429 and a peak of at most 62 requests in
      any 60 s; every stored price has both the raw wallet-currency value (UAH) and a USD value
      = raw / the NBU UAH→USD rate on the item's fetch day (Steam converts the whole history at
      one current rate, spike S-08), and no item lacks a rate; first dates: of the items with a
      DB release date (cases excluded), at most 10% have a first Steam point more than 1 day
      before it, and the median first point lies 0…7 days after it.
      Amended 2026-11-11 (both changes were made after the backfill, to fit its result; accepted
      by the author with these reasons): (1) the rate limit was 24/min, only the pace tried in
      spike S-01; the real evidence of a safe pace is zero HTTP 429 in 27,236 requests at ~1 s
      per request, so zero 429 is now part of the criterion; (2) "each item's first date within
      7 days of its first Steam sale date" cannot be checked, since no independent first-sale
      source exists and the DB release date fails it for 40% of items; the share rule above is
      the strongest check available.

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
      Then — every day from the first index day to the last has exactly one regime label;
      labels for all days ≤ t are identical when data after t is deleted (a test truncates the
      data at a random t); on a synthetic crash and rally the labels are `falling` after the
      crash and `rising` after the rally (tests/test_regimes.py). On real data the command prints
      the label shares, the flips per year and the latest label runs, and the week of the
      October 2025 trade-up crash is reported as an observation, not a pass/fail.
      Amended 2026-11-11: the crash-week pass/fail was dropped. A causal label cannot mark the
      day of the shock, and the median item fell about 18% on 2025-10-23/24 and recovered within
      days, so the 30-day lookback labels 1 of the 7 days `falling`. Fitting the lookback to that
      week would be tuning on the example; a lookback picked without it (fewest label flips per
      year on 2019-01…2025-09: 30 d = 22.7, 7 d = 24.0, 14 d = 29.9) keeps 30 days. 7 and 14 days
      would label 10-23…28 `falling`; both are recorded in decisions.md as the known alternative.

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

AC-15: Given — a report and data exist.
       When — `cs2 ui` runs and the pages are opened.
       Then — four views work: **Verdict** (every strategy × capital × regime, holds up / fails
       with the first broken rule, the benchmarks next to it); **Run** (equity curve in both
       profit units, drawdown, trade list of one run); **Market** (index level with regime
       bands, category indices, one item's price); **Collector** (snapshots per hour, gaps, item
       share, GitHub vs Mac, data freshness). Every page shows the data version and the last
       data day. The numbers on the Verdict page equal the report's results table (same hash),
       and an empty database shows a message instead of an error.

AC-16: Given — the Settings page.
       When — a value is edited and saved.
       Then — the groups strategy parameters, costs and fills, regime and universe, and report
       windows (walk-forward windows, capitals, holdout start) can be changed; an invalid
       value is rejected with the reason and nothing is written; a valid one goes to `cs2.toml`
       (the old file kept as `cs2.toml.bak`) and the page says that results made under other
       settings have a different `run_id`. The verdict rule's thresholds (AC-12: cost +50%,
       spread ×2, deflation) are shown read-only; secrets (`.env`) are never read or shown.
       Changing the holdout start shows a warning that the holdout then stops being untouched.

AC-17: Given — the Run form (strategy, params, capital, date range).
       When — a backtest is started from the UI.
       Then — it runs as a separate process, shows progress, and the result appears on the Run
       page; the same spec gives the same `run_id` and numbers as `cs2 backtest`; only one job
       writes to the database at a time and the UI says "busy" instead of failing when a
       collector run, backfill or other job holds the DuckDB file; closing the browser does not
       kill the job.

AC-18: Given — the UI process.
       When — it is started and used.
       Then — it listens on 127.0.0.1 only; no page or endpoint places, cancels or prepares an
       order on Steam or market.csgo; secret values never appear in any response; every
       state-changing request must carry a token created at start-up (a page on another site
       cannot trigger a run or a settings write); a test checks each of these.

## Non-goals
- Real-money trading, placing or cancelling orders on market.csgo or Steam.
- Paid data APIs.
- Float, pattern, sticker premiums; each `market_hash_name` is one fungible item.
- Intraday strategies on the past (no hourly history before our collector starts).
- A hosted service, or a UI reachable from outside this Mac, or any trading from the UI. (A local UI is
  in scope since 2026-11-11: step 7. Earlier this line excluded any web UI.)

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
