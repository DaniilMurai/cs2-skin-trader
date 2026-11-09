# Pipeline state

Project: cs2-skin-trader (research)
Updated: 2026-11-09

Current stage: **7 — implementation**
Gates: passed up to and including stage 6 (author's go to stage 7, 2026-11-09)

Next action: T-06 (keyed sales) → T-07…T-10 (collector first). Done 2026-11-09: T-01…T-05, T-15, T-18.

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
- 2026-11-09 — Task order: the market.csgo collector (T-02…T-10) before the Steam backfill
  and everything else — collector days can't be recovered later.
- 2026-11-09 — Event/seasonal strategy family has no known dated event source → spike S-09
  inside T-27; the family may be dropped or narrowed.
