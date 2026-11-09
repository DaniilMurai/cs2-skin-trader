# Checkpoint — 2026-11-09 ~17:00 UTC

For the next session: read this, then `state.md` (stage, decisions) and `tasks.md` (checkboxes).
Mode: stage 7, autonomous (the author's standing instruction: work through tasks, commit each
verified task, stop only for questions only the author can answer). Last commit: see `git log`.

## Running right now
- **Full Steam backfill (T-14)**, detached (`nohup caffeinate -i … cs2 backfill steam`), in
  `data/backfill/` with its own `cs2.toml` and DuckDB, so it doesn't lock `data/cs2.duckdb`.
  Log: `data/backfill/backfill.log`. 27,209 names (the asks feed of 2026-11-09), ~4 s per item
  (Steam response time) → done around **2026-11-11 00:00 UTC**. Resumable: if it stops, re-run
  from `data/backfill/`:
  `STEAM_LOGIN_SECURE=… uv run --project ../.. cs2 backfill steam --names names.txt`.
  Exit 3 = cookie expired → refresh `STEAM_LOGIN_SECURE` in `.env` and re-run.

## Done (verified by own runs; 208 tests pass)
| Area | Tasks |
|---|---|
| Repo, settings | T-01 git, T-02 `cs2.toml` + CLI overrides |
| Collector | T-03 asks/bids client, T-04 snapshot storage, T-05 UAH+RUB rates 2013→, T-06 keyed sales + polling tiers, T-07 `collect run`, T-08 code (workflow + `collect pull`), T-09 plist, T-10 `collect status` code |
| Steam + items | T-11 resumable backfill (AC-2 checked live), T-12 categories (0 unknown of 27,209), T-13 `data check` |
| Market model | T-15 universe, T-16 index + regimes code |
| Engine | T-18 fills to the cent (AC-7/8), T-19 views + look-ahead/property tests (AC-9), T-20 run output |
| Strategies | T-21 benchmarks, T-22 V1 dip, T-23 V2 category momentum, T-24 trend, T-25 xs momentum, T-26 mean reversion, T-27 spike S-09 + T-27b event strategy |
| Evaluation | T-28 metrics, T-29 walk-forward/holdout/stress, T-30 verdict + deflated Sharpe, T-31 HTML report |

## Left
**Needs the author:**
1. T-08 GitHub setup — status 2026-11-09 ~17:40 UTC: code repo public and the workflow registered;
   `cs2-skin-data` private with an initial commit; the secrets `DATA_REPO_TOKEN` and
   `MARKETCSGO_API_KEY` were added to **cs2-skin-data** — they must be in **cs2-skin-trader**
   (the repo that runs the workflow). Once they are: `gh workflow run collect.yml`, then
   `gh run watch`, then check `cs2-skin-data` got files under `asks/` and `bids/` (close T-08).
2. T-09 install the launchd job (`ops/README.md`).

**After the backfill finishes (in this order):**
1. `merge_from(Store(Path("data")), Path("data/backfill/data"))` (in `cs2trader.backfill`), then
   `cs2 items update`, `cs2 data check --categories` (AC-1, AC-4; decide a tolerance for the
   first-date check if many items fail), `cs2 regimes` (T-16: 2025-10-22…28 must be `falling`).
2. T-27a: ≥ 90% of case-release dates in `docs/pipeline/events.csv` within 7 days of the first
   Steam point.
3. V1/V2 runs 2019-01-01…2025-09-30 at $100 and $1,000 (close T-22, T-23).
4. T-33: full `cs2 report` is hours long (V1: 72 variants × 25 windows × 2 capitals) → parallelise,
   add progress, target < 2 h; then the first real report and verdict.

**Time-gated:** T-10 real check (48 h of collection), T-17 ratio + spread calibration (≥ 14 days),
T-34 measured ratio in backtests, T-32 forward paper test (≥ 28 days). Then stage 8 audit.

## Decisions made this session (full list with reasons in `state.md` → Draft decisions)
- bh_universe sizing: skip items dearer than their equal share, re-split (author's choice).
- Collector first; Steam backfill in its own data dir (DuckDB single-writer lock).
- Steam: a never-sold item and an expired cookie look the same (`success:true, prices:[]`); a
  canary request (`Clutch Case`) tells them apart. Unknown names return HTTP 500.
- Sales polling log (`sales_polls`) so zero-sale items wait 24 h instead of every run; GHA checks
  out only `sales/` and `sales_polls/` of the data repo (asks+bids grow ~4.7 GB/year).
- Engine: costs picked per bar; ratio by category; fills at the next bar; strategies see only
  point-in-time views; 400-day warmup; universe/regime settings are part of the run_id.
- Index: chain-linked median daily log return; regime = 30-day return vs ±5%.
- Strategy rules for V1/V2 are our reading of the video (specs in each strategy's `spec_md`):
  V1 adds a one-unit minimum (so $100 can hold a knife) and a 7-day cooldown after exits.
- Verdict: deflated Sharpe (Bailey & López de Prado) from all tuning-run Sharpes; the stressed
  holdout is compared with unstressed benchmarks (stricter).
- Event family narrowed to Majors and case pool exits (S-09); ByMykel case dates are unreliable,
  so the first-date data check skips cases.
- `POLARS_MAX_THREADS=1` by default (report 2m50s → 83 s, same hash).

## Known gaps
- Real-data results don't exist yet: the main `data/` has only 3 slice items, so every strategy
  except the benchmarks shows no trades there.
- The hourly engine path (forward test) still uses a `Date` trades schema — T-32 must widen it.
- `ruff` flags RUF009 in `config.py:111` (a `CostModel(...)` dataclass default) — harmless.
