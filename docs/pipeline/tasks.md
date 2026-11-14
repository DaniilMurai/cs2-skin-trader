# Tasks

Updated: 2026-11-13 (group J, small-budget strategies, added). Order in the file = order of execution. The collector goes first: every
day it isn't running is a day of market.csgo data we can never get back (AC-6 needs ≥ 14 days,
AC-14 needs ≥ 28). Shapes, names and exit codes are in `contracts.md`; this file doesn't repeat
them. Already built by the stage-5 slice: `errors.py`, `models.py` (part), `require_secret`,
NBU `fetch_rates("UAH")`, `SteamClient.price_history`, `Store` (fx, steam, fetch log,
`daily_bars`, `data_version`), `engine.run`/`apply_fills` (first cut), `bh_universe`.

Time-gated tasks: T-17 can start once the collector has ≥ 14 days of snapshots
(≈ 2026-11-24 if T-08 lands on 2026-11-10); T-32 once it has ≥ 28 days (≈ 2026-12-08).
Until then they are skipped and the next task in order is taken.

## A. Repository and the market.csgo collector (Plan step 1, AC-3)

- [x] T-01 — git repository and first commit
      Files: .gitignore, (git)
      Do: `git init`; check that `.env`, `data/`, `reports/`, `.idea/`, caches are ignored;
          commit the slice as it is. The author makes the commit (no commit without an
          explicit "commit")
      DoD: `git status --ignored` lists `.env` and `data/` as ignored; `git log --oneline`
           has 1 commit; `git ls-files | grep -E '\.env$|cs2\.duckdb'` prints nothing
      Executor: main loop
      Depends on: —

- [x] T-02 — Settings from `cs2.toml` + CLI overrides
      Files: src/cs2trader/config.py, cs2.toml, src/cs2trader/cli.py, tests/test_config.py
      Do: `Settings` with every row of the contracts Configuration table and its default;
          `load_settings(path, **overrides)`; a bad type or unknown key → `ConfigError`;
          CLI replaces the hard-coded `DATA_DIR`, 2.5 s, 3 retries with settings
      DoD: `uv run pytest tests/test_config.py` green (missing file = defaults; one nested
           key overridden by the file and again by a CLI override; unknown key → ConfigError);
           `uv run cs2 backtest ...` from "How to run" still prints the same run_id
      Executor: subagent
      Depends on: —

- [x] T-03 — market.csgo public client: asks + bids
      Files: src/cs2trader/sources/marketcsgo.py, tests/test_marketcsgo_public.py,
             tests/fixtures/mc_asks.json, tests/fixtures/mc_bids.json
      Do: `MarketCsgoPublic.asks()/bids()` → `Snapshot` per the source schemas (asks `price`
          is a string, bids a number; `time` → `feed_time`); any other shape → `SchemaError`
          with the raw body saved to `data/quarantine/mc_public/<utc>.json`; retries per
          the Errors table
      DoD: `uv run pytest tests/test_marketcsgo_public.py` green on trimmed real fixtures
           incl. one malformed body (→ SchemaError + quarantine file); a live
           `uv run python -c "...MarketCsgoPublic().asks()"` returns ≥ 20,000 rows
      Executor: subagent
      Depends on: T-02

- [x] T-04 — Snapshot storage + `asks`/`bids` views
      Files: src/cs2trader/store.py, tests/test_store_snapshots.py
      Do: `Store.write_snapshot` → `data/collector/<kind>/day=YYYY-MM-DD/<feed_time>-<host>.parquet`
          (zstd); DuckDB views `asks`, `bids` over the Parquet tree, dedup on
          (feed_time, name)
      DoD: `uv run pytest tests/test_store_snapshots.py` green: the same snapshot written as
           `gha` and `mac` → 2 files, 1 set of rows in the view; two feed_times → both kept
      Executor: subagent
      Depends on: T-03

- [x] T-05 — CBR RUB rates + full-history `cs2 fx update`
      Files: src/cs2trader/sources/fx.py, src/cs2trader/cli.py, tests/test_fx.py,
             tests/fixtures/cbr.xml
      Do: `fetch_rates("RUB", ...)` from CBR XML (windows-1251, comma decimal, Value/Nominal,
          weekends forward-filled with `filled=True`); `cs2 fx update` fetches both bases
          from 2013-01-01 to today, only the missing days on re-run
      DoD: `uv run pytest tests/test_fx.py` green (a Saturday gets Friday's rate, filled=True);
           after `uv run cs2 fx update`, a DuckDB query finds no missing day for UAH or RUB
           between 2013-01-01 and today
      Executor: subagent
      Depends on: T-02

- [x] T-06 — market.csgo keyed sales: client, storage, polling tiers
      Files: src/cs2trader/sources/marketcsgo.py, src/cs2trader/store.py,
             src/cs2trader/collect.py, tests/test_sales.py
      Do: `MarketCsgoKeyed.sales(names)` in batches of `mc.sales_batch`, ≥ `mc.interval_s`
          apart, key never logged; `Store.write_sales` + `sales` view (dedup on
          name, ts, price_raw); `sales_due(store, now)` by `mc.sales_refill_share` and
          `mc.sales_tiers_h` (S-07)
      DoD: `uv run pytest tests/test_sales.py` green: 45 names → 3 requests; 401 → MarketCsgoKeyError;
           an item whose last 100 sales span 10 h (half = 5 h) gets the 1 h tier
           with tiers [1, 6, 24] and 5 h with [1, 5, 24]; one spanning 30 days gets 24 h; a live call for 3 names stores rows in RUB
      Executor: subagent
      Depends on: T-04, T-05

- [x] T-07 — `cs2 collect run`
      Files: src/cs2trader/collect.py, src/cs2trader/cli.py, tests/test_collect.py
      Do: `collect_once` per the contract: kinds independent, a failed kind doesn't stop the
          others; `MarketCsgoKeyError` → sales skipped, exit 3 after asks/bids are saved;
          exit codes per contracts
      DoD: `uv run pytest tests/test_collect.py` green (bids source down → asks still written,
           exit 4); live `uv run cs2 collect run --kinds asks,bids,sales --host mac` writes
           3 files under `data/collector/` and exits 0
      Executor: subagent
      Depends on: T-06

- [ ] T-08 — GitHub Actions hourly collector + `cs2 collect pull` *(first run 2026-11-09 20:10 UTC green: 27,230 asks, 26,132 bids, 459,621 sales rows pushed to cs2-skin-data; reopened by the 2026-11-11 audit: only 6 scheduled runs in 39 h and `collect pull` never ran locally; DoD needs hourly scheduled runs green and `cs2 collect pull` adding files)*
      Files: .github/workflows/collect.yml, src/cs2trader/collect.py, src/cs2trader/cli.py
      Do: hourly cron workflow: `uv sync`, `cs2 collect run --host gha`, push new Parquet
          files to the private repo `cs2-skin-data` with `DATA_REPO_TOKEN`; `cs2 collect pull`
          = clone/pull that repo into `data/collector/`. The author creates the GitHub repos,
          the PAT and both secrets, and pushes the code repo
      DoD: two consecutive scheduled runs are green in the Actions tab; `uv run cs2 collect pull`
           adds ≥ 4 files (2 runs × asks+bids) locally
      Executor: main loop
      Depends on: T-01, T-07

- [x] T-09 — Mac launchd gap filler *(installed 2026-11-09 ~20:15 UTC; 2026-11-11 check: 14 `-mac.parquet` files for the day, far above the ≥ 2 required. Known limit: it skips hours while the Mac sleeps, e.g. 2026-11-11 12:00–13:44 UTC)*
      Files: ops/com.cs2trader.collect.plist, ops/README.md
      Do: a launchd agent running `cs2 collect run --kinds asks,bids --host mac` hourly
          (StartCalendarInterval, so a missed run fires on wake); logs to `data/logs/`.
          Installed by the author with the command in ops/README.md
      DoD: `launchctl print gui/$(id -u)/com.cs2trader.collect` shows the job; after 2 h
           ≥ 2 new `-mac.parquet` files exist
      Executor: main loop
      Depends on: T-07

- [ ] T-10 — `cs2 collect status` *(code and tests done 2026-11-09; real 48 h check waits for T-08)*
      Files: src/cs2trader/collect.py, src/cs2trader/cli.py, tests/test_collect_status.py
      Do: `collect_status(window_h)`: snapshots in the window (a snapshot = an asks file and
          a bids file in the same hour), each one's item share vs the largest, gaps > 2 h
      DoD: `uv run pytest tests/test_collect_status.py` green on synthetic files (a 3-hour hole
           is listed with its start and end; a 70%-size snapshot is flagged); on real data,
           48 h after T-08, the command prints ≥ 44 snapshots (AC-3)
      Executor: subagent
      Depends on: T-08

## B. Steam backfill and item data (Plan steps 1–2, AC-1, AC-2, AC-4)

- [x] T-11 — Backfill hardening: universe, limits, error policy
      Files: src/cs2trader/cli.py, src/cs2trader/sources/steam.py, tests/test_backfill.py
      Do: `--limit`, `--names`, default universe = names in the latest asks snapshot;
          backoff `steam.backoff_s × 2^n`; `stop_after_errors` in a row → exit 4;
          `SteamAuthError` → exit 3 with everything kept; empty history → status `empty`;
          request timestamps logged so the rate can be checked
      DoD: `uv run pytest tests/test_backfill.py` green with a fake client (resume skips `ok`;
           10 errors in a row stop; auth error keeps stored rows); live: 20-name list killed
           after ~10 items and re-run gives the same row count as an uninterrupted run into a
           fresh data dir (AC-2)
      Executor: main loop
      Depends on: T-02, T-04

- [x] T-12 — Item categories: `cs2 items update`
      Files: src/cs2trader/sources/items_db.py, src/cs2trader/store.py, src/cs2trader/cli.py,
             tests/test_items.py, tests/fixtures/items_db_sample.json
      Do: fetch ByMykel `all.json` (at most daily, cached), `classify` by the DB then by name
          rules (spike S-04), store `items` with `first_seen`; the raw 78 MB file is cached in data/cache/
          `daily_bars` gains a `category` column from `items` (the engine looks up
          `FillModel.ratio` by it, T-18; without it every item gets `ratio_fallback`)
      DoD: `uv run pytest tests/test_items.py` green on the fixed 20-name list from AC-4;
           `uv run cs2 items update` on the current asks universe leaves ≤ 1% `unknown`
      Executor: subagent
      Depends on: T-04

- [x] T-13 — `cs2 data check [--categories]`
      Files: src/cs2trader/check.py, src/cs2trader/cli.py, tests/test_check.py
      Do: coverage vs universe, first date vs first sale date (≤ 7 days), duplicate
          (name, ts), every steam row has fx, rate in the request log ≤ 24/min; `--categories`:
          unknown share + the 20-name list. Any failure → exit 6
      DoD: `uv run pytest tests/test_check.py` green: each check fails on a crafted bad
           database and passes on a good one
      Executor: subagent
      Depends on: T-11, T-12

- [x] T-14 — Full Steam backfill run (operational) *(finished and merged 2026-11-10; `cs2 data check --categories` exits 0)*
      Files: — (data only)
      Do: `cs2 fx update`, then `cs2 backfill steam` over the feed universe in the background,
          resuming after cookie expiry (author refreshes `STEAM_LOGIN_SECURE`)
      DoD: `uv run cs2 data check --categories` exits 0 (AC-1, AC-4)
      Executor: main loop
      Depends on: T-05, T-13

## C. Market model (Plan step 2, AC-5, AC-6)

- [x] T-15 — Point-in-time universe
      Files: src/cs2trader/market/universe.py, tests/test_universe.py
      Do: `universe(bars, on, s)`: ≥ `min_history_days` of history, trailing 30-day median
          volume ≥ `min_median_volume`, last price ≥ `min_price_usd`, data ≤ `on` only
      DoD: `uv run pytest tests/test_universe.py` green: an item listed after `on` never appears;
           result for `on` is unchanged when bars after `on` are deleted
      Executor: subagent
      Depends on: T-02

- [x] T-16 — Market index, regimes, `cs2 regimes` *(2026-11-11 on the real data: 4,835 days 2013-08-16…2026-11-10, one label each; falling 1,131 / flat 2,301 / rising 1,403; crash week 2025-10-22…28 = rising, flat, falling, flat ×4 — an observation, AC-5 amended)*
      Files: src/cs2trader/market/index.py, src/cs2trader/market/regimes.py,
             src/cs2trader/cli.py, tests/test_regimes.py
      Do: decide the index method (weights, how items enter and leave, chain-linking) and
          write it into the function docstring; per-category indices; `label_regimes` by
          `regimes.lookback_days` / `threshold`; store table `regimes`
      DoD: `uv run pytest tests/test_regimes.py` green: random-t truncation gives identical
           labels ≤ t; on real data every day has one label and `cs2 regimes` prints shares, flips
           per year and the 2025-10-22…28 labels as an observation (AC-5, amended 2026-11-11)
      Executor: main loop
      Depends on: T-14, T-15

- [ ] T-17 — Steam→market.csgo ratio + spread calibration, `cs2 ratio` *(code and synthetic tests done 2026-11-13, 279 tests, result stored in table `steam_to_market_ratio`; the real-data run waits for ≥ 14 days of snapshots, ~2026-11-23)*
      Files: src/cs2trader/market/ratio.py, src/cs2trader/cli.py, tests/test_ratio.py
      Do: per category and side (ask, bid): median, q25, q75, n_items of market.csgo price /
          Steam daily price on the same day; < 20 items → all-items fallback, flagged; also
          print median (ask − bid) / mid per category as the measured spread
      DoD: `uv run pytest tests/test_ratio.py` green on synthetic data (a 5-item category falls
           back and says so); on real data with ≥ 14 days `uv run cs2 ratio` prints the table
           (AC-6)
      Executor: subagent
      Depends on: T-10, T-14

## D. Backtest engine (Plan step 3, AC-7, AC-8, AC-9)

- [x] T-18 — Fills, costs and settlement to the cent
      Files: src/cs2trader/backtest/engine.py, tests/test_engine_fills.py
      Do: bring `apply_fills` and the run loop to the contract: cost schedule picked by bar
          date, Steam→market.csgo ratio from `FillModel`, volume cap with the rest cancelled,
          `limit` orders, sells before buys, `cashout_available`, `InvariantViolation` on a
          negative balance or quantity
      DoD: `uv run pytest tests/test_engine_fills.py` green: the AC-7 scenario and the AC-8
           scenario match hand-computed numbers within 0.005
      Executor: main loop
      Depends on: T-02

- [x] T-19 — Market/portfolio views and look-ahead guards
      Files: src/cs2trader/backtest/view.py, src/cs2trader/backtest/engine.py,
             tests/test_lookahead.py
      Do: `MarketView` (history, universe, items, index, regime) raising `LookAheadError`
          past `now`; `PortfolioView`; the engine passes only these to strategies
      DoD: `uv run pytest tests/test_lookahead.py` green: random-t truncation gives identical
           decisions ≤ t; a property test over ≥ 200 random strategies never sees a negative
           quantity or overspend (AC-9)
      Executor: main loop
      Depends on: T-15, T-16, T-18

- [x] T-20 — Run output and `cs2 backtest` / `cs2 strategies`
      Files: src/cs2trader/cli.py, src/cs2trader/backtest/engine.py, tests/test_cli_backtest.py
      Do: `data/runs/<run_id>/` with `spec.json` as real JSON (not `str(spec)`); `--params`
          validated against the registry grid keys; `cs2 strategies` lists name, family, grid
      DoD: `uv run pytest tests/test_cli_backtest.py` green: same spec twice → same run_id and
           identical parquet bytes; unknown param → exit 2
      Executor: subagent
      Depends on: T-19

## E. Strategies and benchmarks (Plan step 4, AC-10)

- [x] T-21 — Benchmarks: cash, bh_universe, bh_knives_gloves
      Files: src/cs2trader/strategies/benchmarks.py, src/cs2trader/strategies/registry.py,
             tests/test_benchmarks.py
      Do: buy-and-hold sizing: a fixed-size equal-dollar basket (≤ 25 items) of items cheaper
          than their share, picked by a stable name hash (changed 2026-11-11 after the audit: the
          first "skip the dear ones and re-split" rule emptied the basket on thousands of real
          items); spec_md for each
      DoD: `uv run pytest tests/test_benchmarks.py` green: cash equity is flat; with $100 and
           one $1,800 item in the universe, bh_universe skips it and spends ≥ 95% of capital
           on the rest; all three
           run via `cs2 backtest` at both capitals
      Executor: subagent
      Depends on: T-20

- [x] T-22 — V1 "falling market": dip buying in knives/gloves *(code and tests done 2026-11-09; the 2019–2025 run waits for T-14)*
      Files: src/cs2trader/strategies/video.py, src/cs2trader/strategies/registry.py,
             tests/test_v1.py
      Do: write the rule spec (entry on a 7-day drop, liquidity filter, sizing, exit,
          "protection" as a stop-loss on/off) into `spec_md` and the grid; implement
      DoD: `uv run pytest tests/test_v1.py` green: on a synthetic series it buys only after
           a drop ≥ the threshold, only knives/gloves, and the stop-loss variant sells at the
           first unlocked bar below the stop; `cs2 backtest --strategy v1_dip_knives` runs
           2019-01-01…2025-09-30 at $100 and $1,000
      Executor: main loop
      Depends on: T-21

- [x] T-23 — V2 "rising market": category momentum *(code and tests done 2026-11-09; the 2019–2025 run waits for T-14)*
      Files: src/cs2trader/strategies/video.py, src/cs2trader/strategies/registry.py,
             tests/test_v2.py
      Do: rule spec (category growth window, top-k categories, how "buy more actively"
          scales exposure, rebalancing given the lock) in `spec_md` + grid; implement
      DoD: `uv run pytest tests/test_v2.py` green: on synthetic category indices it holds only
           the top-k growing categories and adds exposure while growth persists; runs at both
           capitals over the walk-forward period
      Executor: main loop
      Depends on: T-21

- [x] T-24 — Trend following / go-to-cash
      Files: src/cs2trader/strategies/trend.py, src/cs2trader/strategies/registry.py,
             tests/test_trend.py
      Do: hold the liquid universe while the market index is above its N-day moving average,
          sell everything (as locks allow) below it; grid N ∈ {20, 50, 100, 200}
      DoD: `uv run pytest tests/test_trend.py` green: synthetic index crossing below the MA →
           sell orders next bar, locked lots sold on unlock; runs at both capitals
      Executor: subagent
      Depends on: T-21

- [x] T-25 — Cross-sectional momentum
      Files: src/cs2trader/strategies/momentum.py, src/cs2trader/strategies/registry.py,
             tests/test_xs_momentum.py
      Do: every `rebalance_days`, hold the top-k items by `lookback`-day return within the
          universe, equal weight; grid lookback ∈ {14, 30, 90}, k ∈ {3, 10}, rebalance ∈ {7, 14, 28}
      DoD: `uv run pytest tests/test_xs_momentum.py` green: on synthetic data it picks the
           known top-k and never rebalances a lot before it unlocks; runs at both capitals
      Executor: subagent
      Depends on: T-21

- [x] T-26 — Mean reversion after overshoots
      Files: src/cs2trader/strategies/mean_reversion.py, src/cs2trader/strategies/registry.py,
             tests/test_mean_reversion.py
      Do: buy an item when its price is ≥ z standard deviations below its `lookback`-day mean,
          sell at the mean or after `max_hold` days; grid z ∈ {1.5, 2, 3}, lookback ∈ {14, 30}
      DoD: `uv run pytest tests/test_mean_reversion.py` green on a synthetic overshoot;
           runs at both capitals
      Executor: subagent
      Depends on: T-21

- [x] T-27 — Spike S-09: event calendar source *(answered 2026-11-09: partly — spikes/FINDINGS.md S-09)*

- [x] T-27a — Curated event list `docs/pipeline/events.csv` *(first-point check 2026-11-10: 39/42 case releases = 92.9% within 7 days; misses: two 2013 cases before Steam history, Gallery Case +8 d)*
      Files: docs/pipeline/events.csv, src/cs2trader/market/events.py, tests/test_events.py
      Do: hand-curated rows (date, type, name, source_url): 25 Majors (start, end) from the
          Wikipedia table via the MediaWiki API; ~45 case releases and drop-pool exits
          (incl. 2025-12-18) from the third-party table, each re-checked against a Steam news
          post or the Steam price history's first day; update markers 2025-07-16 and
          2025-10-22. No ByMykel case dates. `load_events()` returns a polars frame
      DoD: `uv run pytest tests/test_events.py` green: ≥ 25 Majors with start < end, no
           duplicate (date, type, name), every row has a source URL; for ≥ 90% of case-release
           rows the item's first Steam point is within 7 days of the listed date
      Executor: main loop
      Depends on: T-14

- [x] T-27b — Event strategy: Majors and case pool exits
      Files: src/cs2trader/strategies/event.py, src/cs2trader/strategies/registry.py,
             tests/test_event.py
      Do: two rules from S-09 (only these have enough events): (1) hold the current Major's
          sticker capsules from `pre` days before the Major starts to `post` days after it
          ends; (2) buy a case when it leaves the active drop pool and hold `hold` days.
          Events are known at their date only (a Major's dates are announced well before —
          use the start date minus `pre` as the earliest decision day). Grid small.
      DoD: `uv run pytest tests/test_event.py` green: no position before an event's decision
           day; the look-ahead truncation test passes for this strategy; runs at both capitals
      Executor: main loop
      Depends on: T-27a, T-21

## F. Evaluation and report (Plan step 5, AC-11, AC-12, AC-13)

- [x] T-28 — Metrics, total and per regime
      Files: src/cs2trader/evaluate.py, tests/test_metrics.py
      Do: `metrics(result, regimes)`: net return and max drawdown in both units, daily Sharpe
          (cash units, annualised √365), trades, fees; per regime = the days with that label
      DoD: `uv run pytest tests/test_metrics.py` green against hand-computed values on a
           10-bar equity curve
      Executor: subagent
      Depends on: T-20

- [x] T-29 — Walk-forward, holdout, stress runs
      Files: src/cs2trader/evaluate.py, src/cs2trader/store.py, tests/test_walk_forward.py
      Do: rolling folds per `wf.*` settings, grid search on the train window by cash Sharpe,
          `variants_tried` recorded, holdout run with chosen params, stress run (fees × 1.5,
          spread × 2); a test asserts no holdout bar is ever read during tuning
      DoD: `uv run pytest tests/test_walk_forward.py` green: fold boundaries match a hand list
           for 2019-01-01…2025-09-30; tuning with holdout data deleted gives the same
           chosen params
      Executor: main loop
      Depends on: T-28

- [x] T-30 — Verdict and deflated Sharpe
      Files: src/cs2trader/evaluate.py, tests/test_verdict.py
      Do: AC-12 rules in their order; deflated Sharpe ratio (Bailey & López de Prado) over
          `n_variants`; first broken rule recorded
      DoD: `uv run pytest tests/test_verdict.py` green: one crafted case per rule fails on
           exactly that rule; a case passing all rules → `holds_up`
      Executor: main loop
      Depends on: T-29

- [x] T-31 — `cs2 report` HTML + results hash *(hash reproducible on `--strategies mean_reversion`; a full run on all strategies → T-33)*
      Files: src/cs2trader/report.py, src/cs2trader/cli.py, tests/test_report.py
      Do: `reports/<date>/report.html`: verdict line first, table per strategy × regime ×
          capital × unit for walk-forward and holdout, sensitivity to costs and spread,
          V1/V2 spec appendix, assumptions (ratio, spread) stated; `results_hash`
      DoD: `uv run pytest tests/test_report.py` green; `uv run cs2 report` twice on the same
           data → identical results hash (AC-13); the HTML has every column of AC-11
      Executor: subagent
      Depends on: T-30, T-22, T-23, T-24, T-25, T-26

## F2. Follow-ups found during implementation

- [x] T-33 — Full report run time *(2026-11-10: 57 min with `--jobs 8`, was ~10 h single-process; two runs gave hash c1c2074822aba264)*
      Files: src/cs2trader/evaluate.py, src/cs2trader/report.py
      Do: a full `cs2 report` runs every grid point in 25 tuning windows × 2 capitals (V1 alone:
          72 × 25 × 2 = 3,600 runs); with the real ~27k-item data it will take hours. Measure on
          the merged backfill, then cut it: run strategies/capitals in parallel processes (each
          builds its own MarketData), and add progress output
      DoD: a full `cs2 report` on the merged data finishes in < 2 h on this Mac; two runs give the
           same results hash
      Executor: main loop
      Depends on: T-14, T-31

- [x] T-34 — Measured Steam→market.csgo ratio in backtests *(2026-11-13: `make_spec(..., store=)` reads `steam_to_market_ratio`, ratio = mid of the ask and bid medians; default `fixed` unchanged; `cs2 backtest` does not pass the store yet → done inside T-47)*
      Files: src/cs2trader/evaluate.py, src/cs2trader/market/ratio.py
      Do: when `fills.ratio_source = "measured"`, `make_spec` fills `FillModel.ratio` per category
          from `steam_to_market_ratio` (T-17); today only `ratio_fallback` is passed
      DoD: a test with a stored ratio table gives per-category fill prices; the report's
           assumptions block names the source
      Executor: subagent
      Depends on: T-17

## G. Forward paper test (Plan step 6, AC-14)

- [ ] T-32 — Hourly bars and `cs2 forward` *(code and synthetic tests done 2026-11-13: 12 forward tests, choices in `reports/T-32.md`, engine and view also changed; the real run needs ≥ 28 days of snapshots, ~2026-12-07, and T-67 first)*
      Files: src/cs2trader/store.py, src/cs2trader/forward.py, src/cs2trader/cli.py,
             src/cs2trader/report.py, tests/test_forward.py
      Do: `hourly_bars` from asks/bids/sales (price, observed sales as volume, ask, bid);
          engine at `freq="1h"` buying at the ask and selling at the bid; report gets a
          "forward" column for the same dates
      DoD: `uv run pytest tests/test_forward.py` green: a decision at hour h never sees a
           snapshot with feed_time > h; on ≥ 28 days of real snapshots `uv run cs2 forward
           --strategy v1_dip_knives` writes trades and equity, and the report shows the column
      Executor: main loop
      Depends on: T-17, T-31

## H. Local UI (Plan step 7, AC-15…AC-18; added 2026-11-11)

Design choices made here, not in the plan: Python standard library `http.server` (no new runtime
dependency), JSON endpoints under `/api/`, plain HTML + vanilla JS pages, charts drawn as inline SVG
by a small vendored function (no CDN, works offline). The UI process opens DuckDB read-only per
request and never keeps the file open; anything that writes runs as a subprocess (`cs2 ...`).

- [x] T-35 — Re-run the full report after the audit fixes *(2026-11-11: 71 min with `--jobs 4` at `nice 19`, results hash d3ea18d4859fd53e, "No strategy holds up at $100 / $1,000"; NOT done: the second run to confirm the same hash)*
      Files: reports/ (output only), docs/pipeline/state.md
      Do: `uv run cs2 report` on the merged data with the fixed benchmarks and chaining; record the
          new verdict in state.md and decisions.md (the 2026-11-10 verdict was computed before the fixes)
      DoD: the report finishes in < 2 h; two runs give the same results hash; the first line says which
           strategies hold up (or that none does)
      Executor: main loop
      Depends on: T-31

- [x] T-36 — UI server skeleton and safety rails (AC-18) *(tests/test_ui.py security tests green 2026-11-11)*
      Files: src/cs2trader/ui/server.py, src/cs2trader/ui/security.py, src/cs2trader/cli.py,
             tests/test_ui_security.py
      Do: `cs2 ui [--port N]` binds 127.0.0.1; a start-up token required on every POST (sent in a
          header, embedded in the served page); a response filter that fails a test if a secret value
          appears; no order-related endpoint exists
      DoD: `uv run pytest tests/test_ui_security.py` green: bind address, POST without token → 403,
           with `.env` values set no response body contains them, route list has no order route
      Executor: main loop
      Depends on: T-31

- [x] T-37 — Read API: report, runs, market, collector (AC-15) *(read API tests green; real-data pages checked in the browser 2026-11-11)*
      Files: src/cs2trader/ui/api.py, tests/test_ui_api.py
      Do: JSON for the latest verdict table, one run's equity and trades, index + regimes + category
          indices + one item's bars, collector status (reuse `collect_status`); each carries
          `data_version` and the last data day; an empty database gives an empty-state payload
      DoD: `uv run pytest tests/test_ui_api.py` green; the verdict payload's results hash equals the
           report's for the same run; works while another process holds the database (busy flag)
      Executor: subagent
      Depends on: T-36

- [x] T-38 — Verdict and Run pages (AC-15) *(Verdict and Run pages checked in the browser on the real data clone 2026-11-11)*
      Files: src/cs2trader/ui/static/*, tests/test_ui_pages.py
      Do: table with holds up / fails + first broken rule and benchmarks beside it; a run page with the
          equity curve in both units, drawdown shading and the trade table; inline-SVG chart helper
      DoD: pages served and render in the built-in browser against a real run (screenshot checked by the
           author); `tests/test_ui_pages.py` green on a fixture run
      Executor: subagent
      Depends on: T-37

- [x] T-39 — Market and Collector pages (AC-15) *(Market and Collector pages checked in the browser on the real data clone 2026-11-11 (the index chart starts at 2018 by default: the 2013–2017 index is unreliable))*
      Files: src/cs2trader/ui/static/*
      Do: index level with regime bands and a category selector; one item's price chart with search;
          collector page with snapshots per hour, gaps, item share, source (gha/mac), freshness
      DoD: both pages render on real data in the built-in browser; a fixture test covers the empty state
      Executor: subagent
      Depends on: T-37

- [x] T-40 — Settings editor (AC-16) *(saved a setting through the page, invalid value rejected with the file unchanged, 2026-11-11)*
      Files: src/cs2trader/ui/settings_api.py, src/cs2trader/config.py, tests/test_ui_settings.py
      Do: read the editable groups from `Settings`, validate against the dataclass types and ranges,
          write `cs2.toml` atomically with a `.bak`, show verdict thresholds read-only, never touch
          `.env`; the holdout-start warning
      DoD: `uv run pytest tests/test_ui_settings.py` green: invalid value → rejected, file unchanged;
           valid → file written, `load_settings` returns it, `.bak` has the old content; verdict
           thresholds and secrets are not writable through the endpoint
      Executor: main loop
      Depends on: T-36

- [x] T-41 — Job runner and the Run form (AC-17) *(started bh_universe $1,000 2025-01-01…09-30 from the form: run_id 4791e350a5aea310, identical to `cs2 backtest`, 2026-11-11)*
      Files: src/cs2trader/ui/jobs.py, src/cs2trader/ui/static/*, tests/test_ui_jobs.py
      Do: a single-slot queue that starts `cs2 backtest` as a subprocess, streams its progress, survives
          the browser closing; "busy" when the DuckDB file is locked or a job is running; the form
          validates the strategy and params with the same code as `cs2 backtest`
      DoD: `uv run pytest tests/test_ui_jobs.py` green: the same spec through the UI and the CLI gives the
           same `run_id`; a second job while one runs → busy; a killed browser connection does not stop it
      Executor: main loop
      Depends on: T-40, T-38

- [x] T-42 — End-to-end check of the UI *(author confirmed 2026-11-13)* *(all pages and both forms ran live on a clone of the real data 2026-11-11; re-audit of `ui/` done 2026-11-13 on synthetic data, `docs/pipeline/reports/T-42-ui-audit.md`: AC-15 and AC-18 pass, AC-16 and AC-17 each fail one clause → T-63, T-64; still waits for the author to look)*
      Files: docs/pipeline/brief.md (How to run)
      Do: start `cs2 ui` on the real data clone, open every page in the built-in browser, edit a setting,
          start a backtest, compare its numbers with the CLI; write the commands into "How to run"
      DoD: all four views and both forms work live; the author has seen them; the audit (stage 8) is
           repeated on `ui/` with the AC-18 checks run, not read
      Executor: main loop
      Depends on: T-39, T-41

- [x] T-43 — UI rewrite for readability *(2026-11-11, strong-model subagent; 258 tests; every page checked in the browser on the real data clone at desktop and 375 px; not yet verified: busy/failed-job/no-collector states in the browser, keyboard-only use, widths above 800 px)*
      Files: src/cs2trader/ui/plain.py, src/cs2trader/ui/static/*, src/cs2trader/ui/api.py, settings_api.py
      Do: Home page, glossary, plain-language verdict scoreboard, guided "Try a strategy" flow, explained settings
      DoD: tests/test_ui.py green; the author finds the pages understandable (to be confirmed)
      Executor: subagent
      Depends on: T-41

## I. Cloudflare buffer (added 2026-11-11)

- [x] T-44 — Worker with quota guard *(ops/cloudflare/worker.js; `node --test` 6 tests; deployed 2026-11-11, 13 runs ok, delay 0 s)*
- [x] T-45 — Importer `cs2trader.sources.cloudflare` + `cs2 collect pull --cf-only` *(tests/test_cloudflare.py, 5 tests)*
- [x] T-46 — *(checked 2026-11-13: both names in `.env`, launchd job loaded, 108 `-cf` files since 2026-11-11, hourly "3 files imported, 3 removed from R2" in `collect-mac.log`, 44 snapshots in 48 h, no gap > 2 h; `/status` itself not queried)* Switch on: rotate the token, add `CF_WORKER_URL`/`CF_WORKER_TOKEN` to `.env`, run `ops/install-launchd.sh`
      DoD: after two hours `cs2 collect status` counts `cf` snapshots and `/status` shows `used_bytes` falling after an import
      Executor: author (secrets), main loop (check)

- [ ] T-47 — Wallet arbitrage paper test (also: `cmd_backtest` passes `store=` to `make_spec`, T-34 follow-up): from the hourly snapshots, log every hour the (Steam/1.15)/ask edge per item with the Steam volume cap, and report the realistic daily profit on $100 and $1,000 in wallet units *(code and synthetic tests done 2026-11-13: `cs2 arbitrage`, assumptions in `reports/T-47.md`; the real run needs ≥ 14 days of snapshots, ~2026-11-24, and `cs2 backfill steam --refresh` first; a re-run ~8 days later resolves all buy days)*
- [x] T-51 — Add Skinport `min_price`/`quantity` to the hourly collector (public `/v1/items`, USD, `tradable=true`, `Accept-Encoding: br`, 1 request per run) and store it next to market.csgo; DoD: 24 hourly snapshots in a day, join to asks by name *(S-12; extends T-47 to a cash-to-cash direction)* *(code and tests done 2026-11-12, live run ok; launchd job switched to `--kinds asks,bids,skinport` and merged to main 2026-11-12; first hourly file landed; Mac only, the Cloudflare Worker still fetches market.csgo only)*
- [x] T-52 — ~~Buy aggregator history~~ *(dropped 2026-11-12: the project spends no money, free data only)*
      Executor: main loop
- [x] T-48 — New low-turnover strategies, backtested on the walk-forward first, holdout once: (a) quality basket of items with the longest price history and rising volume, rebalanced quarterly; (b) buy cases when the index is `falling`, hold; (c) cheap-item mean reversion with a minimum 15% expected move *(2026-11-11: quality_basket, case_dip, cheap_reversion built, 268 tests; all three fail beats_cash on the holdout at $100 and $1,000)*
      Executor: main loop
- [x] T-49 — Check the bh_universe +60% (other hash seeds, only items alive at the start) before any strategy is compared with it *(2026-11-11: 30 seeds, median 0% at $1,000 and +3.4% at $100; the +60% was luck)*
      Executor: subagent
- [x] T-50 — Re-run the full report with the median benchmark (`bh_median`) and compare with the T-35 verdict *(done 2026-11-12: 80 min, hash fe95acd963df7a5f, "No strategy holds up at $100 / $1,000"; all 18 strategy/capital rows fail beats_cash; second run for hash identity not done)*
      Executor: main loop
- [x] T-53 — Collect Waxpeer prices hourly (keyless `/v1/prices`, `min` in thousandths of a dollar, parquet in `data/collector/waxpeer/`, ~0.36 MB/hour) *(done 2026-11-12: 272 tests pass, launchd job runs `--kinds asks,bids,skinport,waxpeer`, first file landed; Mac only; Waxpeer's terms on automation not confirmed)*
- [x] T-54 — *(2026-11-13: Skinport answers Cloudflare Workers with HTTP 403 / error 1005 (network banned), so it is removed from the Worker and stays Mac-only; deployed version fb436b3a at 15:42 UTC with two crons :07 (asks, bids) and :09 (waxpeer), 3 raw files per hour; CPU 8 ms and 5 ms on one cycle; DoD amended from 4 files / 3 crons)* Deploy the Worker with Waxpeer and Skinport (`cd ops/cloudflare && npx wrangler deploy`; three crons :07 :09 :11, strict 8 GB guard) and confirm the three crons store 4 raw files per hour, then read CPU per invocation with `npx wrangler tail` (Skinport alone must stay near 10 ms, else drop it from the Worker) and the Mac imports them *(code, Node tests (6) and Python import tests done 2026-11-12; local workerd run fetched all 4 feeds incl. 15.5 MB Skinport; deploy was blocked by the permission classifier, the user runs it)*

## J. Small-budget strategies (added 2026-11-13, AC-19…AC-21)

T-59 waits for ≥ 14 days of snapshots (~2026-11-24), T-62 for ≥ 28 days (~2026-12-07). Holdout rules: AC-21.

- [x] T-55 — *(2026-11-13: `price_step` 0.001, `min_price` 0.005, `min_fee` 0.01 (an assumption, binds below $0.20), worst-case rounding; 328 tests; every run_id and the next report's results hash change; adding capital $50 to the report is left to T-61; report `reports/T-55-costs.md`)* Small-budget cost model: fees with minimums, price rounding, smallest tradable price, whole-item lots, capital $50
      Files: src/cs2trader/backtest/costs.py, tests/test_costs_small.py
      DoD: `uv run pytest tests/test_costs_small.py` green; a $50 run never buys a fraction of an item
      Executor: main loop
      Depends on: —

- [x] T-56 — *(2026-11-13: `universe.max_price_usd` and `fills.position_cap`, both off by default so run_ids hold; an over-cap buy is cut with reason `position_cap`; 346 tests; file is `src/cs2trader/market/universe.py`; T-57/T-58 should run with `fills.position_cap = 0.05` and a volume floor ≥ 20, since 5% of the median needs ≥ 20 sales a day to hold one unit; report `reports/T-56-liquidity.md`)* Liquidity filter and position cap: price band, volume floor, 5% of median daily volume cap
      Files: src/cs2trader/universe.py, tests/test_liquidity.py
      DoD: tests green; unit test shows an over-cap order is cut, not filled
      Executor: main loop
      Depends on: T-55

- [x] T-57 — *(2026-11-14: `xs_momentum_liquid` and `cs2 report --walk-forward-only`; walk-forward 2020-01…2025-09 on real data: −99.3% at $50, −96.8% at $100 (max drawdown 99.5% / 98.3%), cash −5.0%, bh_median −58.0% / −50.6%; better than cash in 4 of 24 folds; 16 variants tried; fails on walk-forward, the holdout was not touched; `reports/T-57-momentum.md`)* Filtered momentum: xs_momentum on the liquid band with a drawdown stop (fix the 99.6% drawdown)
      DoD: walk-forward run at $50 and $100 logged; the drawdown is reported next to the return
      Executor: main loop
      Depends on: T-56

- [x] T-58 — *(2026-11-14: `cheap_flip`; base rate on 7.72 M item-days under $5 with ≥ 20 sales a day: a round trip beats its cost in 8.3% of cases, median net −12.3%, not predictable on the buy day; walk-forward −93.2% at $50 and −89.6% at $100 (cash −5.0%), about the same at `min_fee` 0.001; beats cash in 5 / 3 of 24 folds; 12 variants; fails on walk-forward, holdout not touched; no hourly run (6 days of snapshots); `reports/T-58-flipping.md`)* Cheap-item flipping: buy under $5 where the net margin after fees exceeds the spread, on hourly snapshots and Steam history
      DoD: strategy registered, tests green, walk-forward result logged
      Executor: main loop
      Depends on: T-56

- [ ] T-59 — *(code and synthetic tests done 2026-11-14: `cs2 arbitrage --mode cross`, 355 tests, assumptions in `reports/T-59-cross-arb.md` (Skinport payout fee/delay and market.csgo top-up fee unconfirmed); real run ≥ 2026-11-26, re-run ~12 days later)* Cross-platform arbitrage paper test: market.csgo, Waxpeer, Skinport, net of fees, small lots
      Extends T-47 (wallet arbitrage). Needs ≥ 14 days of snapshots (~2026-11-24); T-54 helps.
      DoD: daily edge and realistic profit on $50 and $100 written to reports/
      Executor: main loop
      Depends on: T-47, T-54, T-55

- [x] T-60 — *(2026-11-14: `event_small` (a Major's sticker capsules bought `wait` days after it ends); measured to 2025-09-30: release plays lose 46–85% in a month, Major start −61%, pool exits flip sign with the hold; only "Major end + 90 d, hold 180–365 d" is positive (+33% / +93% median, 18–19 events); walk-forward −70.0% at $50 and −58.7% at $100 (cash −5.0%), beats cash in 6 of 24 folds; 4 variants; the 91-day folds cannot test the long hold → T-70; `reports/T-60-events.md`)* Event and release plays on small positions (spike 09 calendar)
      DoD: strategy registered, tests green, walk-forward result logged
      Executor: main loop
      Depends on: T-55, S-09

- [ ] T-61 — Verdict extension (AC-19, AC-21): $50 capital, trades/drawdown/volume rules, deflation count
      DoD: `uv run pytest` green; a synthetic strategy breaking each rule gets the right "first rule broken"
      Executor: main loop
      Depends on: T-55, T-56

- [ ] T-62 — Full report re-run with all new strategies, then forward paper test (AC-20)
      DoD: report hash identical on two runs; each strategy marked holds up / confirmed / fails; decisions.md entry
      Executor: main loop
      Depends on: T-57…T-61, ≥ 28 days of forward data (~2026-12-07)

## K. UI audit fixes and follow-ups (added 2026-11-13 from `reports/T-42-ui-audit.md`)

- [x] T-63 — UI job slot, priority and secrets filter (audit M1, M2, M3) *(2026-11-13: 36 UI tests, Stop button checked in the browser on a stand-in job; not checked: stopping a real backtest mid-write)*
      Files: src/cs2trader/ui/jobs.py, src/cs2trader/ui/security.py, src/cs2trader/ui/server.py,
             src/cs2trader/ui/static/*, tests/test_ui.py
      Do: take the job slot and spawn under one lock; start jobs at `nice 19`; `POST /api/jobs/stop` (token) and a
          Stop button; build the response filter from every secret name in `config.py`
      DoD: `uv run pytest tests/test_ui.py` green with new tests: 6 parallel POSTs → exactly one 202 and five 409;
           the child's niceness is 19 and stop ends it with state `stopped`; the secrets test sets all four names
           and also checks POST and 4xx/5xx bodies
      Executor: main loop
      Depends on: T-41

- [x] T-64 — UI input validation and small defects (audit M4, m1…m5) *(2026-11-13: 44 UI tests; busy Run form and focus checked in the browser; not fixed: m6, m7, comments in `cs2.toml` dropped on save)*
      Files: src/cs2trader/ui/settings_api.py, src/cs2trader/ui/server.py, src/cs2trader/ui/static/pages.js,
             tests/test_ui.py
      Do: reject non-finite numbers and non-object `changes`; skip the write when nothing changed; validate
          `Content-Length` and integer query parameters → 400; no date claim on the Run form while the database
          is busy; return focus to the Run button; tighten the second-job test to `== 409`
      DoD: `uv run pytest tests/test_ui.py` green with tests for `inf`, `NaN`, `[1]` → 422 and file unchanged,
           an unchanged save leaving `.bak` alone, `Content-Length: -1` and `?days=abc` → 400
      Executor: subagent
      Depends on: T-63

- [x] T-65 — *(2026-11-13: the cause was the UI server's listen backlog of 5 dropping one of six connects; `request_queue_size = 32`; 20/20 passes)* Make `test_parallel_starts_take_one_slot` deterministic (failed 1 run in 3 under load: the fast `cash` job frees the slot before a late thread posts)
      Files: tests/test_ui.py
      DoD: the test passes 20 times in a row under `nice -n 19`
      Executor: subagent
      Depends on: T-63

- [x] T-66 — *(covered by T-59, 2026-11-14)* Cash-to-cash edge (market.csgo ↔ Skinport / Waxpeer) as a second `cs2 arbitrage` mode with its own sale model (sell at the then-lowest listing minus fee) *(proposed by T-47; after T-47's real run)*

- [ ] T-67 — Before the real `cs2 forward` run: restrict hourly bars to the Steam universe plus held items (each decision now concatenates up to 45 days × ~27,000 items ≈ 29 M rows), then `cs2 backfill steam --refresh` *(from T-32)*
      DoD: `cs2 forward` on ≥ 28 days of real snapshots finishes under `nice 19` without the Mac lagging; results equal the unrestricted run on a synthetic fixture
      Executor: main loop
      Depends on: T-32

- [ ] T-68 — *(2026-11-13: the sales were in `cs2-skin-data` all along; `cs2 collect pull` ran for the first time, 52 files added, `sales` now covers 2026-11-09…13, 2.9 M deduplicated rows, 17,511 items. Coverage: GHA polls 4,800 names every 4–9 h, so ~42 fast items (~31% of sales volume) lose sales between polls and ~9,350 listed items were never polled. `sales` added to the Mac launchd job 2026-11-13 (author's go), run after the price snapshots. Open: define forward volume for the fastest items; `sales_due` puts overdue items before never-polled ones)* Sales in the local data: only one day (2026-11-09, 120 items) is present, so hourly `volume` is ~0. Check whether the GitHub collector's sales are in `cs2-skin-data` (`cs2 collect pull`), else add `sales` back to a collector or drop "observed sales as volume" from the contract *(from T-32; author decides)*

- [ ] T-69 — UI follow-ups of T-55/T-56: labels for fill reasons `min_price` and `position_cap` in `pages.js`; the settings `costs.price_step`, `costs.min_price`, `costs.min_fee`, `universe.max_price_usd`, `fills.position_cap` in the editable list
      DoD: `uv run pytest tests/test_ui.py` green with a test per new setting; both reasons show a plain-language label
      Executor: subagent
      Depends on: T-56

- [ ] T-70 — Long-horizon check of "buy a Major's capsules 90 days after it ends, hold 180–365 days" (from T-60: the walk-forward folds restart in cash every 91 days, so the hold was never tested as such). Write the test design and the pass rule into decisions.md before any result is read; 18–19 events only, so the result can at most say "not contradicted"
      Executor: main loop
      Depends on: T-60
