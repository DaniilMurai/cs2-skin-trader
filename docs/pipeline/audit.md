# Audit 2026-11-11

Scope: the worktree at commit `4318668` (= `main`). Real data: a copy-on-write clone of the main
checkout's `data/` (merged `cs2.duckdb`, `data/backfill/data/cs2.duckdb`, `collector/`, `logs/`,
`cache/`) in the auditor's scratch dir; the main checkout itself wasn't written to. No Steam or
market.csgo calls, no secrets used. `gh run list` (read-only) was used for the GitHub collector.
`uv run pytest -q` → `208 passed in 32.91s`.

Severity: **Critical** = the research result (report, verdict) is wrong or misleading;
**Major** = an acceptance criterion isn't met, or a `[x]` task's DoD isn't really met;
**Minor** = latent defects, contract drift, hygiene.

Scratch scripts quoted below live in
`/private/tmp/claude-501/-Users-daniilmurai-PycharmProjects-cs2-skin-trader--claude-worktrees-pipeline-stage-8-audit-b72852/b01824a9-63ee-4c9f-9b6f-67d37c487961/scratchpad/scripts/`
(session scratch, not kept): `bh_check.py`, `chain_check.py`, `trunc_all.py`, `sales_due_check.py`,
`dv.py`, `cap_check.py`, `mk_tiny.py` (a 300-item subset of the real DB, 25 items per category).

## Critical

- `src/cs2trader/strategies/benchmarks.py:20-31` (`equal_split`) — every item dearer than the
  current equal share is dropped **in one pass**, so on a real universe of thousands of items
  almost everything is dropped at once. Both buy-and-hold benchmarks are degenerate on real data:
  ```
  $ python bh_check.py <real data>
  2019-01-02 bh_universe       universe 4375 candidates 4375 cap $100: 0 items targeted, est. spend $0.00
  2019-01-02 bh_universe       universe 4375 candidates 4375 cap $1000: 638 items targeted, est. spend $948.07, dearest kept $0.23
  2019-01-02 bh_knives_gloves  universe 4375 candidates 113 cap $100:  0 items targeted
  2019-01-02 bh_knives_gloves  universe 4375 candidates 113 cap $1000: 0 items targeted
  2025-10-01 bh_universe       universe 7312 ... cap $100: 0 items targeted
  2025-10-01 bh_universe       universe 7312 ... cap $1000: 299 items targeted, dearest kept $0.14
  $ cs2 backtest --strategy bh_universe --capital 100 --from 2019-01-01 --to 2025-09-30   (real data, 252 s)
  run 2fb32296e408d317 ... shape: (0, 8)            # no trades in 6.75 years
   cash: 100.00 → 95.00 (-5.0%)
  ```
  So at $100 "buy-and-hold" = cash, and at $1,000 it is a basket of sub-$0.25 items. This breaks
  AC-12: `verdict` (`evaluate.py:231-234`) compares with `bh_universe`; at $100 its max drawdown
  is 0, so every strategy that has any drawdown fails on `drawdown`, and `beats_bh` duplicates
  `beats_cash`. It also breaks the author's decision in state.md ("keeps the benchmark fully
  invested"). T-21 is `[x]` but its DoD holds only on the 5-item fixture
  (`tests/test_benchmarks.py:13-14`, all items $0.10–$0.80 plus one $1,800 item).

- `src/cs2trader/report.py:65-80` (`_chain_folds`) — each fold's `equity_cash` already contains the
  ×(1 − withdraw fee) haircut, and the chain rescales fold k by `level / capital`, so the haircut is
  compounded once per fold; `equity_steam` is rescaled by the **cash** level. On the default 24
  folds a do-nothing strategy shows 0.95²⁴ − 1 = −70.8 % in the `walk_forward` rows. Checked on
  synthetic data with 3 folds:
  ```
  $ python chain_check.py   # evaluate_all(..., ["cash"]), WfSettings with 3 test folds
  [('walk_forward', 'cash', -0.142625, 0.0975), ('walk_forward', 'steam', -0.11555, 0.0975)]
  # expected: cash -0.05, steam -0.02, max drawdown 0 for a flat cash position
  ```
  Every walk-forward net return in the AC-11 table carries an extra factor 0.95^(folds − 1)
  (−70.8 % for doing nothing over 24 folds), drawdowns include the fold boundaries, and the Steam
  unit is scaled by cash-unit levels. A reader of the report sees every strategy, cash included,
  losing most of its capital in 2020–2025.
  Same on the 300-item real subset (`cs2 report --strategies mean_reversion`): `cash` walk_forward
  `net_return -0.70801`, `max_drawdown 0.69264` at both capitals, with 0 trades.
  The verdict itself is not affected (it uses the holdout and `wf.test_returns`). Fixed on the
  unmerged branch `claude/pipeline-status-964707` (933fb44), not in the audited tree.

## Major

- **AC-1 is not met by the audited code.** `cs2 data check --categories` on the merged real DB:
  ```
  FAIL  first_date: 7308/12179 items with a release date consistent; e.g. Souvenir SSG 08 | Carbon Fiber (Factory New) first 2021-10-27 release 2021-09-22; ...
  PASS  fx: 50508867 rows; ... fx_day != fetch day 0 ...
  FAIL  request_rate: 27446 requests, peak 61 in 60 s (max 24)
  exit=6
  ```
  - `src/cs2trader/cli.py:48` + `src/cs2trader/backfill.py:64` — one FX rate (today's at start) is
    used for the whole multi-day backfill. On the raw backfill DB written by this code:
    `FAIL fx: 50508858 rows; ... fx_day != fetch day 24995483` (15,657 items fetched on 2026-11-10
    carry the 2026-11-09 rate). The merged DB passes only because it was produced by unmerged code
    (`c8d5824` "per-item FX rate").
  - `data/backfill/cs2.toml` (main checkout) sets `steam.interval_s = 1.0`; the log peaks at 61
    requests/min against AC-1's 24. The unmerged branch edits AC-1 to "62 requests/min (amended
    2026-11-10 from 24…)" after the run — the criterion was moved to fit the result.
  - `src/cs2trader/check.py:86-107` — the first-date proxy (`items.release_date`) fails for 40 %:
    619 items have a first point before the DB release date, 4,264 liquid items start > 7 days
    after it (median 12 days; 2,117 of them Souvenir/StatTrak variants).
  T-14 is open, so this is expected-incomplete, but the per-run FX rate is a code defect in the
  audited tree, not a data gap.

- **AC-5 is not met on real data**, and the index behind it drifts. `cs2 regimes` on the real copy:
  `2025-10-22 rising, 10-23 flat, 10-24 falling, 10-25…10-28 flat` — 1 of the 7 crash-week days is
  `falling` (T-16 DoD requires all of 2025-10-22…28). The chain-linked median of daily log returns
  (`src/cs2trader/market/index.py:44-62`) diverges from the items it summarises:
  ```
  2013-10-15→2014-06-15: items 681,  median item ratio 0.981; index 13.83→3.92 = 0.283
  2019-01-15→2025-09-15: items 7610, median item ratio 3.129; index  5.60→25.76 = 4.597
  2025-11-15→2026-11-01: items 16463, median item ratio 0.781; index 25.62→23.92 = 0.933
  ```
  Regime labels, `trend_ma` and `v2_category_momentum` all read this index.

- `src/cs2trader/store.py:211-224` (`daily_bars`) and `src/cs2trader/check.py` — no outlier
  handling for Steam one-day price spikes. Since 2019 the real data has 55,902 bars that are > 5×
  both the previous and the next bar (5,745 items; 17,958 of them with volume ≥ 5 and previous
  price ≥ $0.10). Example in the 300-item subset: `MP5-SD | Co-Processor (Well-Worn)` 0.1400 →
  **4.4838** → 0.1416 on 2025-10-15…17 (volume 31). Held by `bh_universe` at $100, it moves the
  holdout equity +148 % and −59 % on consecutive days, so the run ends at −4.5 % with a **71 %
  max drawdown** — the very number the AC-12 `drawdown` rule compares against. Spikes also feed
  mean-reversion z-scores and V1 drops.

- `src/cs2trader/cli.py:88-89` — `cs2 backtest` builds `RunSpec` with default `costs`/`fills` and
  ignores `cs2.toml`, while `cs2 report` uses `make_spec` (`evaluate.py:130-140`) with settings.
  Checked with a scratch `cs2.toml` (`fills.spread_penalty = 0.10`, `costs.seller_fee = 0.08`):
  `spec fills: {'participation_cap': 0.2, ..., 'spread_penalty': 0.03}`, `seller_fee: 0.05`.
  A single run and the report's run with the same params disagree whenever the config isn't default.

- `src/cs2trader/store.py:226-234` (`data_version`) — run_id determinism holds for identical data,
  but the version ignores content and includes unrelated snapshots:
  ```
  (a) 25 knives re-categorised:   data_version 4f28db4abbd08585 -> 4f28db4abbd08585  same=True
  (b) every price_usd x1.5:       data_version -> 4f28db4abbd08585  same=True
  (c) one asks snapshot added:    data_version -> b785a9c7717560d1  same=False
  ```
  Different data → same `run_id` (`cs2 items update`, `--refresh`, `merge_from` with equal counts);
  a Steam-only backtest gets a new `run_id` every hour the collector runs. Contract says "row counts
  of every table/view"; the code counts 4 tables.

- `src/cs2trader/store.py:46` — every `Store` opens `data/cs2.duckdb` read-write, including
  `cs2 backtest`, `cs2 report`, `cs2 collect run/status`. The Mac collector lost runs to this:
  `data/logs/collect-mac.log`: 3× `Could not set lock on file ".../data/cs2.duckdb": Conflicting
  lock is held ... (PID 23691)`, and `cs2 collect status` on the copy shows
  `gap: 2026-11-10 13:00 -> 2026-11-10 16:00 UTC (3 h)` exactly there. The auditor hit the same
  lock: `ATTACH ... (READ_ONLY)` failed while a `cs2 backtest` (PID 5674) held the file. Threatens
  AC-3 whenever a report or backfill runs. (The collector part is addressed by unmerged `10b9e45`.)

- `src/cs2trader/collect.py:21-41` with `src/cs2trader/store.py:57-59` — `sales_due` measures
  "last fetched" as `max(fetched_at)` over the **deduplicated** `sales` view, which keeps the
  earliest `fetched_at` of each sale. A poll that brings no new sale doesn't move it, so the item is
  overdue on every later run and, being the most overdue, is polled first:
  ```
  $ python sales_due_check.py      # Thin: 100 sales over 30 days (24 h tier), re-polled at t1 with no new sales
  due at t1+1h: ['Thin']   due at t1+2h: ['Thin']   due at t1+3h: ['Thin']   due at t1+5h: ['Thin']
  ```
  With thousands of illiquid items this fills the 4,800-name cap every hour and starves liquid ones.
  No test in `tests/test_sales.py` / `tests/test_collect.py` re-polls an item that has sales but no new ones.

- **Tests that pass vacuously** (and the `[x]` tasks resting on them):
  - `tests/test_lookahead.py:103-112` (AC-9 property test, T-19) — a mutant engine that lets every
    buy take 3 extra unaffordable units (`engine.py:92` `affordable = … + 3`) still passes it,
    because `engine.py:97` clamps the balance at 0 before the invariant check at `engine.py:123`,
    so overspend can never surface as a negative balance. Only `test_engine_fills.py:111` caught the
    mutant. The property test asserts nothing about lots either.
  - `tests/test_walk_forward.py:86-93` ("tuning never reads the holdout", T-29) — both runs call
    `walk_forward` without `data`, so both load only bars ≤ `holdout_start − 1`
    (`evaluate.py:178`); a mutant view leaking 60 days of future bars (`view.py:53`) passes it (the
    look-ahead tests catch it: `6 failed, 8 passed`). The real path, `report.evaluate_all`
    (`report.py:104,112`), passes a market that **includes** the holdout; it is safe only because
    the views are point-in-time (checked below), not because of this test.
  - `src/cs2trader/check.py:92-107` — `first_date` passes when `items` is empty (0/0 consistent):
    on the raw backfill DB it printed no FAIL for `first_date` while `fx` and `request_rate` failed.
  - `tests/test_store_snapshots.py:24-32` — "gha wins" isn't asserted (both files have equal rows and
    `host` isn't selected). Checked separately: with differing rows the view returns
    `[('gha', 'AK', 10.0), ('mac', 'Only mac', 1.0)]` — gha wins per row; behaviour is correct.

- **T-08 `[x]`, DoD not met.** DoD: "two consecutive scheduled runs are green; `cs2 collect pull`
  adds ≥ 4 files". tasks.md itself says "the second scheduled run is still to be seen". `gh run
  list`: 6 scheduled runs in 39 h (2026-11-10 00:41, 08:25, 17:02, 22:22, 2026-11-11 02:46, 10:56 UTC)
  — the "main hourly collector" fires every 4–9 h. `data/collector/` in the main checkout has no
  `.git` and 0 `-gha` files (37 `-mac`), so `collect pull` never ran there. AC-3 depends on the Mac
  job alone in practice: `cs2 collect status` → `snapshots: 34 in last 48 h (below target)`.

- **T-21 `[x]`** — see Critical 1. **T-31 `[x]`** — hash reproducibility holds (see AC-13), but
  "the HTML has every column of AC-11" hides wrong walk-forward values (Critical 2).

## Minor

- `src/cs2trader/backtest/engine.py:84` — the participation cap is per order, not per item and day:
  `3 buy orders of 2, volume 10, cap 20%: filled [2, 2, 2] total 6` (AC-8 says 2). No registered
  strategy splits orders today; latent.
- `src/cs2trader/backtest/engine.py:129-138` — `equity_cash` counts sale proceeds that are still in
  the 8-day cash-out hold; a run ending < 8 days after a sale reports cash-out money that isn't
  withdrawable yet (`cashout_available` is computed but not used by metrics).
- `src/cs2trader/sources/steam.py:30-33` — a changed Steam date/row format raises a bare
  `ValueError` (`time data '2026-11-09T13:' does not match format`, `too many values to unpack`),
  not `SchemaError`; no raw body is quarantined (contracts, Errors table: "save raw body to
  data/quarantine/, exit 5"). The backfill would crash with exit 1 on the first such item.
- `src/cs2trader/cli.py:96` / `src/cs2trader/evaluate.py:47` — a window without bars crashes:
  `cs2 backtest --strategy cash --capital 100 --from 2010-01-01 --to 2010-03-01` →
  `polars.exceptions.OutOfBoundsError: index 0 is out of bounds`. A holdout with no bars
  (`store.last_day()` < `holdout_start`) would crash `cs2 report` the same way.
- `src/cs2trader/report.py:83-91` — per-regime rows use the stored `regimes` table whenever it is
  non-empty, whatever data it was computed on; the table isn't part of `data_version`.
- `src/cs2trader/strategies/video.py:155-165` vs `V2_SPEC` (`video.py:113-114`) — the spec says
  "between rebalances nothing is traded"; the code tops up target positions and sells non-targets
  every bar.
- `src/cs2trader/collect.py:173-179, 209` — the item share uses the asks count only (bids size is
  never checked), and `format_status` prints `ok` on the count alone even when low-share hours exist.
- `src/cs2trader/market/events.py:5` — `events.csv` is located via `parents[3]` of the source file
  (breaks in a non-editable install); `events.csv` content is not part of `run_id`.
- `ops/com.cs2trader.collect.plist:9,20,28,30` — absolute `/Users/<author>/…` and
  `/opt/homebrew/bin/uv` paths committed to a public repo.
- `src/cs2trader/store.py:203` — `steam_fetch_log.http_status` is always `NULL`.
- Contract drift (`contracts.md` vs code): `sales_due(store, now)` → `sales_due(store, now, s)`
  (`collect.py:21`); `variants_tried` key adds `capital` (`store.py:27-29`); `data_version`
  composition (above); `RateLimited` is never raised (`errors.py:30`, `marketcsgo.py:99`);
  `regimes` table has an extra `ret` column; `FillModel.ratio: Mapping[str, float]` and
  `RunResult.cashout_available: … | None` (`models.py:144,177`). Not yet present (open, time-gated):
  `market/ratio.py`, `forward.py`, `Store.hourly_bars`, `cs2 ratio`, `cs2 forward`, datetime `now`
  in `MarketView`.

## Observations (not verified by a run unless stated)

- Look-ahead (AC-9) — verified by a run beyond the shipped tests: full vs truncated data over
  **every** registered strategy, 3 seeds each, random cut, trades and equity ≤ cut compared:
  `FAILS 0` (27 cases, up to 215 fills before the cut). No look-ahead found in the views.
- Survivorship: the backfill universe is the 2026-11-09 market.csgo feed (`backfill.py:25-32`) and
  `items` is today's classification; items delisted before that day are absent from 2013–2025.
- Deflated Sharpe (`evaluate.py:206-221`) is SR − SR0 (no PSR with skew/kurtosis/T); `n_variants`
  = grid × 25 tuning windows, i.e. conservative. Acceptable reading of AC-12, but stated nowhere in
  the report.
- The verdict benchmark is `bh_universe` only; `bh_knives_gloves` (V1's natural benchmark) is
  computed but never used in `verdict`.
- A participation cap of 20 % floors to 0 units for items with < 5 sales/day: V1's real holdout log
  has `★ Hydra Gloves | Emerald (Battle-Scarred) buy 1 → 0 volume_cap`.
- Real-data V1 holdout runs (2025-10-01…2026-11-10): $100 → $29.81 cash (fees $28.71);
  $1,000 → $158.21 (fees $330.70). Not a defect; recorded for scale.
- Resources: one real-data backtest 2019–2025 = 252 s, 4.7 GB RSS; `cs2 regimes` = 75 s, 6.5 GB RSS.
  The full report on the audited code is single-process (T-33 open).
- Unmerged work: branch `claude/pipeline-status-964707` (6 commits past `main`) and
  `claude/pipeline-continue-f58959` change backfill FX, AC-1, check rules, chaining, the parallel
  report, and close T-14/T-22/T-23/T-27a/T-33. The real DB in the main checkout was produced by
  that code, not by the audited tree. On that branch T-22/T-23 are `[x]` while their notes still
  say "the 2019–2025 run waits for T-14" — not audited here.
- Secrets: `git log --all -p` has no cookie/key/PAT-shaped values; only `.env.example` is tracked;
  `.env` and `data/` are ignored (`git check-ignore`). The keyed client never logs the URL
  (`marketcsgo.py:176-186`). No imports from `spikes/` in `src/`, no TODO/FIXME.

## Acceptance criteria — summary

| AC | Status | How it was checked |
|---|---|---|
| AC-1 | not met | `cs2 data check --categories` on the real copy: exit 6 (first_date 7308/12179, request rate 61/min > 24); raw backfill DB: fx_day ≠ fetch day for 24,995,483 rows (audited code uses one rate per run) |
| AC-2 | not verifiable (needs live Steam + cookie) | `tests/test_backfill.py` green with a fake client; the live check is only claimed in T-11 |
| AC-3 | not met yet / not verifiable until 48 h of both collectors | `cs2 collect status` on the copy: 34 snapshots in 48 h, gap 2026-11-10 13:00–16:00 caused by the DuckDB lock; GHA ran 6 times in 39 h |
| AC-4 | met | same data check: `unknown_share 0/26898 = 0.00%`, `ac4_list 20/20` |
| AC-5 | not met | `cs2 regimes` on the copy: 4,835 days, one label each (met); 2025-10-22…28 = rising, flat, falling, flat×4 (not met); truncation tests green |
| AC-6 | not verifiable (not implemented, T-17 time-gated) | `market/ratio.py` and `cs2 ratio` absent |
| AC-7 | met | `tests/test_engine_fills.py` green; own check with 3 % spread: buy 41.20, sell 38.80, fee 1.94, balance 95.66 = hand value, cash-out 90.88 |
| AC-8 | met for one order; bypassable | test green; 3 orders of 2 on volume 10 fill 6 (`cap_check.py`) |
| AC-9 | met (truncation); property part vacuous | own truncation run over all 9 strategies: 0 differences; overspend mutant passes `test_property_no_negative_qty_or_overspend` |
| AC-10 | partly | real `cs2 backtest` bh_universe $100 2019–2025 writes trades/equity in both units but makes 0 trades; V1 real holdout runs OK; specs in appendix (test_report); other strategies not run over 2019–2025 in this audit (≈4 min each) |
| AC-11 | not met | columns present (test), but walk-forward rows are wrong (`chain_check.py`: cash −14.3 % over 3 folds, should be −5 %) |
| AC-12 | not met in substance | rule order and deflation implemented (`tests/test_verdict.py` green); benchmark at $100 = cash with 0 drawdown, at $1,000 a sub-$0.25 basket (`bh_check.py`) |
| AC-13 | met (on a 300-item real subset) | `cs2 report --strategies mean_reversion` twice on `mk_tiny.py` data: `results hash: b01ac8a63a90a0a4` both times; full data not run (≈10 h single-process per the unmerged T-33 note) |
| AC-14 | not verifiable (not implemented, T-32 time-gated) | `forward.py`, `cs2 forward` absent |

## New terms

- **Mutation test** — deliberately breaking one line of the code and re-running the tests; if they
  still pass, they don't actually check that line.
- **Deflated Sharpe ratio** — a Sharpe ratio reduced by the best Sharpe you'd expect from pure luck
  after trying N variants; positive means the result beats that luck threshold.
- **Copy-on-write clone (`cp -c`)** — an APFS file copy that shares disk blocks until one side
  changes, so a 3.6 GB database can be copied instantly and safely worked on.
