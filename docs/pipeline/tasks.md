# Tasks

Updated: 2026-11-09. Order in the file = order of execution. The collector goes first: every
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

- [ ] T-08 — GitHub Actions hourly collector + `cs2 collect pull` *(workflow and pull done; needs the author's GitHub setup, ops/README.md)*
      Files: .github/workflows/collect.yml, src/cs2trader/collect.py, src/cs2trader/cli.py
      Do: hourly cron workflow: `uv sync`, `cs2 collect run --host gha`, push new Parquet
          files to the private repo `cs2-skin-data` with `DATA_REPO_TOKEN`; `cs2 collect pull`
          = clone/pull that repo into `data/collector/`. The author creates the GitHub repos,
          the PAT and both secrets, and pushes the code repo
      DoD: two consecutive scheduled runs are green in the Actions tab; `uv run cs2 collect pull`
           adds ≥ 4 files (2 runs × asks+bids) locally
      Executor: main loop
      Depends on: T-01, T-07

- [ ] T-09 — Mac launchd gap filler *(plist ready; the author installs it, ops/README.md)*
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

- [ ] T-14 — Full Steam backfill run (operational, ~19 h) *(running since 2026-11-09 ~16:00 UTC in data/backfill/, merged into data/ when done)*
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

- [ ] T-16 — Market index, regimes, `cs2 regimes` *(code and tests done 2026-11-09; real-data check waits for T-14)*
      Files: src/cs2trader/market/index.py, src/cs2trader/market/regimes.py,
             src/cs2trader/cli.py, tests/test_regimes.py
      Do: decide the index method (weights, how items enter and leave, chain-linking) and
          write it into the function docstring; per-category indices; `label_regimes` by
          `regimes.lookback_days` / `threshold`; store table `regimes`
      DoD: `uv run pytest tests/test_regimes.py` green: random-t truncation gives identical
           labels ≤ t; on real data every day has one label and 2025-10-22…28 are `falling`
           (AC-5)
      Executor: main loop
      Depends on: T-14, T-15

- [ ] T-17 — Steam→market.csgo ratio + spread calibration, `cs2 ratio` *(time-gated: ≥ 14 days)*
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
      Do: buy-and-hold sizing (author, 2026-11-09): an item dearer than its equal share is
          skipped and the capital re-split across the affordable ones, repeated until every
          kept item is affordable; spec_md for each
      DoD: `uv run pytest tests/test_benchmarks.py` green: cash equity is flat; with $100 and
           one $1,800 item in the universe, bh_universe skips it and spends ≥ 95% of capital
           on the rest; all three
           run via `cs2 backtest` at both capitals
      Executor: subagent
      Depends on: T-20

- [ ] T-22 — V1 "falling market": dip buying in knives/gloves *(code and tests done 2026-11-09; the 2019–2025 run waits for T-14)*
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

- [ ] T-23 — V2 "rising market": category momentum *(code and tests done 2026-11-09; the 2019–2025 run waits for T-14)*
      Files: src/cs2trader/strategies/video.py, src/cs2trader/strategies/registry.py,
             tests/test_v2.py
      Do: rule spec (category growth window, top-k categories, how "buy more actively"
          scales exposure, rebalancing given the lock) in `spec_md` + grid; implement
      DoD: `uv run pytest tests/test_v2.py` green: on synthetic category indices it holds only
           the top-k growing categories and adds exposure while growth persists; runs at both
           capitals over the walk-forward period
      Executor: main loop
      Depends on: T-21

- [ ] T-24 — Trend following / go-to-cash
      Files: src/cs2trader/strategies/trend.py, src/cs2trader/strategies/registry.py,
             tests/test_trend.py
      Do: hold the liquid universe while the market index is above its N-day moving average,
          sell everything (as locks allow) below it; grid N ∈ {20, 50, 100, 200}
      DoD: `uv run pytest tests/test_trend.py` green: synthetic index crossing below the MA →
           sell orders next bar, locked lots sold on unlock; runs at both capitals
      Executor: subagent
      Depends on: T-21

- [ ] T-25 — Cross-sectional momentum
      Files: src/cs2trader/strategies/momentum.py, src/cs2trader/strategies/registry.py,
             tests/test_xs_momentum.py
      Do: every `rebalance_days`, hold the top-k items by `lookback`-day return within the
          universe, equal weight; grid lookback ∈ {14, 30, 90}, k ∈ {3, 10}, rebalance ∈ {7, 14, 28}
      DoD: `uv run pytest tests/test_xs_momentum.py` green: on synthetic data it picks the
           known top-k and never rebalances a lot before it unlocks; runs at both capitals
      Executor: subagent
      Depends on: T-21

- [ ] T-26 — Mean reversion after overshoots
      Files: src/cs2trader/strategies/mean_reversion.py, src/cs2trader/strategies/registry.py,
             tests/test_mean_reversion.py
      Do: buy an item when its price is ≥ z standard deviations below its `lookback`-day mean,
          sell at the mean or after `max_hold` days; grid z ∈ {1.5, 2, 3}, lookback ∈ {14, 30}
      DoD: `uv run pytest tests/test_mean_reversion.py` green on a synthetic overshoot;
           runs at both capitals
      Executor: subagent
      Depends on: T-21

- [ ] T-27 — Event and seasonal strategy
      Files: src/cs2trader/strategies/event.py, data source TBD
      Do: needs a dated event calendar (Majors, sticker capsule releases, case drop-pool
          changes) — no source is known. First a spike: is there a free, dated, machine-
          readable source (ByMykel `first_sale_date` covers case/capsule releases only)?
          Then rules + implementation
      DoD: spike S-09 answered in spikes/FINDINGS.md; if yes, the strategy runs at both
           capitals; if no, a decision in decisions.md drops or narrows the family
      Executor: main loop
      Depends on: T-21

## F. Evaluation and report (Plan step 5, AC-11, AC-12, AC-13)

- [x] T-28 — Metrics, total and per regime
      Files: src/cs2trader/evaluate.py, tests/test_metrics.py
      Do: `metrics(result, regimes)`: net return and max drawdown in both units, daily Sharpe
          (cash units, annualised √365), trades, fees; per regime = the days with that label
      DoD: `uv run pytest tests/test_metrics.py` green against hand-computed values on a
           10-bar equity curve
      Executor: subagent
      Depends on: T-20

- [ ] T-29 — Walk-forward, holdout, stress runs
      Files: src/cs2trader/evaluate.py, src/cs2trader/store.py, tests/test_walk_forward.py
      Do: rolling folds per `wf.*` settings, grid search on the train window by cash Sharpe,
          `variants_tried` recorded, holdout run with chosen params, stress run (fees × 1.5,
          spread × 2); a test asserts no holdout bar is ever read during tuning
      DoD: `uv run pytest tests/test_walk_forward.py` green: fold boundaries match a hand list
           for 2019-01-01…2025-09-30; tuning with holdout data deleted gives the same
           chosen params
      Executor: main loop
      Depends on: T-28

- [ ] T-30 — Verdict and deflated Sharpe
      Files: src/cs2trader/evaluate.py, tests/test_verdict.py
      Do: AC-12 rules in their order; deflated Sharpe ratio (Bailey & López de Prado) over
          `n_variants`; first broken rule recorded
      DoD: `uv run pytest tests/test_verdict.py` green: one crafted case per rule fails on
           exactly that rule; a case passing all rules → `holds_up`
      Executor: main loop
      Depends on: T-29

- [ ] T-31 — `cs2 report` HTML + results hash
      Files: src/cs2trader/report.py, src/cs2trader/cli.py, tests/test_report.py
      Do: `reports/<date>/report.html`: verdict line first, table per strategy × regime ×
          capital × unit for walk-forward and holdout, sensitivity to costs and spread,
          V1/V2 spec appendix, assumptions (ratio, spread) stated; `results_hash`
      DoD: `uv run pytest tests/test_report.py` green; `uv run cs2 report` twice on the same
           data → identical results hash (AC-13); the HTML has every column of AC-11
      Executor: subagent
      Depends on: T-30, T-22, T-23, T-24, T-25, T-26

## G. Forward paper test (Plan step 6, AC-14)

- [ ] T-32 — Hourly bars and `cs2 forward` *(time-gated: ≥ 28 days)*
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
