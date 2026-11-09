# Pipeline state

Project: cs2-skin-trader (research)
Updated: 2026-11-09

Current stage: **7 — implementation**
Author's standing instruction (2026-11-09): work through tasks autonomously, commit each verified
task or batch without asking; stop only for questions that need the author.
Gates: passed up to and including stage 6 (author's go to stage 7, 2026-11-09)

Next action: wait for T-14 (Steam backfill in data/backfill/; restarted 2026-11-09 ~22:20 local with the bulk-insert fix and interval 1.0 s, ~7 h remaining, was ~25 h), then merge it into data/ (`merge_from`), run `cs2 items update`, `cs2 data check --categories`, `cs2 regimes` (T-16 real check), T-27a first-point check, V1/V2 2019–2025 runs, T-33 full report. Author: GitHub setup for T-08, launchd install for T-09. Done 2026-11-09: T-01…T-07, T-11…T-13, T-15, T-18…T-21, T-24…T-26, T-27, T-27b, T-28…T-31; code-only (real-data check pending): T-10, T-16, T-22, T-23, T-27a.

## Stage history
- 0 context load — 2026-11-09 (new project: README only, no git, Python 3.14.7, uv)
- 1 idea — 2026-11-09 (brief.md: market.csgo as target venue, free data only)
- 2 plan + criteria — 2026-11-09 (plan.md: 6 steps, AC-1…AC-14, spikes S-01…S-06)
- 3 risk spikes — 2026-11-09 (no assumption killed; AC-1, AC-3, AC-6, AC-7 adjusted;
  S-07: keyed sales history = last 100 sales per item, prices in RUB)
- 4 contracts — 2026-11-09 (contracts.md; S-08 found: Steam history is converted at the
  current FX rate → AC-1 corrected; .env.example created, reports/ gitignored)
- 5 vertical slice — 2026-11-09 (NBU fx → Steam history for 3 items → DuckDB → daily bars →
  bh_universe backtest $1,000, both profit units; same spec + data → same run_id)
- 6 tasks — 2026-11-09 (tasks.md: 32 tasks in 7 groups, collector first; T-17 and T-32
  time-gated by collected days; T-27 event strategy needs spike S-09 first)

## Draft decisions (to collect into decisions.md at stage 9)
- 2026-11-09 — Backfill speed (T-14): the bottleneck was `upsert_steam` (executemany, 2.6 s per item, 85% of
  run time), not Steam's rate limit. Bulk Arrow insert → ~0.1 s. Steam interval lowered 2.5 → 1.0 s after
  probes at 1.5 s (160 requests) and 1.0 s (100 requests): all HTTP 200, no 429. Not pushed below 1.0 s.
  The 2.5 s default was only the one pace tried in S-01 (60 requests), never a measured limit.
- 2026-11-09 — Data: Steam price history + own forward collector, no paid APIs.
- 2026-11-09 — Target venue: market.csgo. Steam history is a proxy for its past;
  our own collector records market.csgo snapshots from now on.
- 2026-11-09 — Profit reported in two units: market.csgo cash-out (main) and Steam wallet.
- 2026-11-09 — Starting capital $100 and $1,000, judged separately (was $100 only).
- 2026-11-09 — Horizons: days–months on Steam history; hours–days on market.csgo data
  collected hourly from now on (forward test only).
- 2026-11-09 — The video's two strategies (V1 dip buying in knives/gloves, V2 category
  momentum) are tested first; exact rules are our reading of a screenshot.
- 2026-11-09 — Own daily-bar backtester instead of backtrader/vectorbt.
- 2026-11-09 — Holdout = 2025-10-01 → last collected day; never used for tuning.
- 2026-11-09 — Verdict rule fixed in advance (plan.md AC-12).
- 2026-11-09 — Collector: GitHub Actions hourly (main) + Mac launchd (gap filler).
- 2026-11-09 — Python 3.14 pinned; duckdb 2.x, polars 2.x, pyarrow 26 (spike S-05).
- 2026-11-09 — Categories from ByMykel/CSGO-API (MIT) + name rules (spike S-04).
- 2026-11-09 — Steam prices stored raw (UAH) + USD via a daily official rate (spike S-01).
  Superseded the same day by S-08: USD = raw / NBU rate on the fetch day (Steam re-converts
  the whole history at a current rate).
- 2026-11-09 — market.csgo API key in use: the collector also polls the last-100-sales window,
  tiered by liquidity (spike S-07); RUB→USD daily rate added.
- 2026-11-09 — Package `cs2trader` (src layout), CLI `cs2` on argparse, config `cs2.toml` + `.env`.
- 2026-11-09 — Decisions at bar close, fills on the next bar; sells before buys within a bar.
- 2026-11-09 — Today's market.csgo rules (7-day lock, fees) applied to all history; no
  historical rule schedule is known.
- 2026-11-09 — Backfill universe = every name in today's asks feed (~19 h), not a liquidity cut,
  to avoid choosing items by today's liquidity; liquidity filter is point-in-time at backtest.
- 2026-11-09 — Steam→market.csgo ratio fixed at 1.0 until ≥ 14 days of snapshots; a constant
  ratio leaves returns unchanged and only affects affordability.
- 2026-11-09 — GHA collector stores snapshots in a separate private repo `cs2-skin-data`
  (author's choice over 90-day Actions artifacts); needs a `DATA_REPO_TOKEN` secret.
- 2026-11-09 — duckdb 2.0 specifics (stage 5): `.pl()` exists only on relations
  (`con.sql(q, params=...)`), not on `execute()` results; fetching a TIMESTAMPTZ into Python
  needs `pytz` → cast to VARCHAR in SQL instead of adding the dependency.
- 2026-11-09 — Slice finding: thin items (Karambit Doppler FN, 1–4 sales/day) have days with
  no Steam point → no bar; the engine values holdings at the last known price.
- 2026-11-09 — Slice finding: equal-dollar bh_universe leaves the share of an unaffordable item
  (Karambit ≈ $1,800 vs a $333 slot) in cash → the benchmark's sizing rule must be fixed at
  stage 6 (skip and re-split, or leave in cash).
- 2026-11-09 — bh_universe sizing (author): skip items dearer than their equal share and
  re-split the capital across affordable ones (keeps the benchmark fully invested).
- 2026-11-09 — Engine (T-18): cost model picked per bar by `valid_from`; ratio looked up by a
  `category` bar column (fallback when absent); volume cap and affordability use a 1e-9 epsilon
  against float error; limit orders fill only at or better than the limit.
- 2026-11-09 — FX (T-05): CBR rates forward-filled over non-publication days (`filled=True`);
  a missing earlier record is a SchemaError, never an invented rate.
- 2026-11-09 — Snapshots (T-04): when GHA and the Mac record the same feed_time, the `gha`
  row wins in the views.
- 2026-11-09 — Steam (T-11): a never-sold item returns 200 `success:true, prices:[]`, the same as an
  expired cookie; one canary request (`Clutch Case`) tells them apart → status `empty` or
  SteamAuthError. An unknown name returns HTTP 500 with `prices:false` → `empty`. A 429 on the first
  request = no auth; after a success = throttling (backoff). Requests logged to
  `data/logs/steam_requests.tsv` for the rate check (T-13).
- 2026-11-09 — Sales polling (T-06): never-fetched names from the latest asks snapshot are due;
  an item with zero sales leaves no rows and is re-polled every run → T-07 caps the batch.
- 2026-11-09 — Collector (T-07): exit-code priority when several kinds fail: 5, 3, 2, 4; sales capped
  at `mc.sales_max_names_per_run` = 4800 (~10 min); a poll log (`sales_polls`) stops zero-sale items
  from being re-polled every run — they wait 24 h.
- 2026-11-09 — GHA collector (T-08) checks out only `sales/` and `sales_polls/` of `cs2-skin-data`:
  asks+bids are ~0.54 MB per hourly run (~13 MB/day, ~4.7 GB/year), too big to clone every hour.
- 2026-11-09 — Items (T-12): 27,209 feed names, 0 unknown; 26,681 by ByMykel DB, 528 by name rules
  (mostly Souvenir Charm highlights). Pins, passes, keys, terminals → `other`; souvenir highlight
  packages → `capsule`.
- 2026-11-09 — Index (T-16): chain-linked median daily log return of items with a bar ≤ 7 days old,
  30-day mean volume ≥ 5 and price ≥ $0.10; ≥ 10 items (≥ 5 per category) or the level holds.
  Regime = 30-calendar-day index return vs ±5%.
- 2026-11-09 — Engine (T-19): strategies see only `MarketData` views (history, universe, items, index,
  regime); bars are loaded 400 days before `start` as warmup; universe and regime settings are part
  of the run_id.
- 2026-11-09 — Data check (T-13): first-sale date proxy = `items.release_date` (first point ≥ release −1 d;
  ≤ release +7 d only for items with ≥ 100 points released after 2013). On slice data Recoil Case
  fails it (DB release 2022-05-30, first Steam point 2022-07-01) → decide a tolerance after the
  full backfill shows how many items fail.
- 2026-11-09 — Steam backfill runs in its own data dir so its 19-hour DuckDB write lock doesn't block
  other commands.
- 2026-11-09 — V1 rules (T-22): knives/gloves in the universe, 7-day drop ≥ `drop_7d`, deepest first,
  slots = equity / `max_positions` but at least one unit if cash covers it (so $100 can hold a knife);
  exits on take-profit, `max_hold` or stop-loss ("protection"), only on unlocked units; 7-day
  cooldown after an exit (otherwise a stop-loss is re-bought at once). Grid 72 variants.
- 2026-11-09 — V2 rules (T-23): every `rebalance_days`, top-`top_k` categories by `window`-day index
  growth > 0; exposure = min(1, growth / `full_growth`) of each category's share; best
  `items_per_cat` items by own return, equal dollars. Grid 16 variants.
- 2026-11-09 — Benchmarks (T-21): buy-and-hold targets fixed on the first day, unfilled parts re-ordered
  for 10 decision days (volume cap cancels the rest), never sells; price estimate × (1 + spread + 2%).
- 2026-11-09 — Metrics (T-28): per-regime return chains the labelled days' daily returns; drawdown on
  that chained path; Sharpe × √365 on daily simple returns (ddof 1).
- 2026-11-09 — Walk-forward (T-29): 24 folds (365-day train, 91-day test, last fold 7 days); tuned by
  train-window cash Sharpe, ties → earlier grid point; holdout params tuned once more on the 365 days
  before 2025-10-01; all tuning runs share one prepared market (one load, cached universes).
- 2026-11-09 — Verdict (T-30): deflated Sharpe = annualised walk-forward test Sharpe − SR0 (Bailey &
  López de Prado 2014) from the variance of all tuning-run Sharpes and n_variants; holds if > 0.
  The stressed holdout run is compared with the benchmarks' normal runs (stricter reading).
- 2026-11-09 — Events (S-09, T-27): the event family is narrowed to Majors (25) and case pool exits
  (~32 moves to the rare pool, 32 discontinuations incl. 2025-12-18), plus two update markers;
  operations and seasonal sales are too few. ByMykel case `first_sale_date` is not a release date
  (only 7/45 within 3 days) → the T-13 first-date check skips cases. Case dates come from a
  hand-kept third-party table (dates only, not its text); Majors from Wikipedia (CC BY-SA).
- 2026-11-09 — Report (T-31): rows strategy × capital × period (walk_forward = fold test runs chained,
  holdout, holdout_stress, per-fold rows) × regime × unit; cash and bh_universe always evaluated;
  hash over the results table only. POLARS_MAX_THREADS defaults to 1 (2m50s → 83 s, same hash).
- 2026-11-09 — Code repo public at github.com/DaniilMurai/cs2-skin-trader (author's choice, for
  unlimited Actions minutes); data stays in the private `cs2-skin-data`. Commits use the GitHub
  noreply address (history rewritten before the first push, author's choice).
- 2026-11-09 — Task order: the market.csgo collector (T-02…T-10) before the Steam backfill
  and everything else — collector days can't be recovered later.
- 2026-11-09 — Event/seasonal strategy family has no known dated event source → spike S-09
  inside T-27; the family may be dropped or narrowed.
