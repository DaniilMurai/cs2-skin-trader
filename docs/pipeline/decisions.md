# Decisions (append-only)

## 2026-11-11 — stage 8 audit (docs/pipeline/audit.md) → fixes

| Decision | Alternatives | Why |
|---|---|---|
| Buy-and-hold benchmarks hold a ≤ 25-item equal-dollar basket picked by a stable name hash among items cheaper than their share (supersedes the 2026-11-09 "skip dear items and re-split" rule) | keep the iterative skip; weight by liquidity | The iterative rule emptied the basket on 4–7k real items ($100: no trades; $1,000: sub-$0.25 items). Real-data check 2024-01-01…2025-09-30: bh_universe +25.8 % ($100, 84 fills) / +21.1 % ($1,000); bh_knives_gloves +15.5 % / +21.8 % |
| Steam daily prices are clipped to 5× the median of the item's previous 7 bars (`cap_price_jumps`), causal | drop bars that are > 5× both neighbours (needs the next bar = look-ahead) | 55,902 one-day spikes since 2019 gave a 71 % drawdown to a buy-and-hold basket |
| Volume cap is per item and day across all orders; a buy that would overspend raises `InvariantViolation` instead of being clamped | keep per-order cap; keep the clamp | AC-8 wording; the clamp hid overspend from the property test |
| `data_version` = content fingerprint of steam_points, items, fx_rates, regimes; snapshots moved to `snapshot_version()` | row counts as before | Re-categorised items / rescaled prices kept the same run_id; an hourly collector run changed it |
| `cs2 backtest` builds its spec with `make_spec` (reads `cs2.toml` costs and fills) | leave defaults | A single run and the report disagreed on any non-default config |
| `sales_due` uses the latest poll time from `sales_polls` | keep `max(sales.fetched_at)` | A poll with no new sales did not move the clock, so thin items were re-polled every hour and starved liquid ones |
| Collector commands use an in-memory DuckDB (cherry-picked 10b9e45 from `claude/pipeline-continue-f58959`) | retry on lock | The Mac collector lost 3 runs (2026-11-10 13:00–16:00) to the file lock |
| Merged `claude/pipeline-status-964707` (per-item FX rate, chained walk-forward haircut applied once, parallel report) | re-implement | Audit: it fixes Critical 2 and the FX defect |
| Steam row-format change → `SchemaError` + raw body in `data/quarantine/steam/` | bare ValueError | Contract: exit 5 with quarantine |
| `events.csv` hash is part of `run_id`; its path falls back to the working directory | parents[3] only | Edited events changed results without changing run_id |
| launchd plist is a template rendered by `ops/install-launchd.sh` | committed absolute paths | Personal paths in a public repo |
| V2 spec text now says the target is fixed until the next rebalance while the book is moved toward it every bar | change the code to trade only on rebalance days | The code retries unfilled buys, which is the better behaviour |
| `cs2 data check` first_date fails when `items` is empty | pass on 0/0 | Hid a missing `cs2 items update` |

### Open — needs the author
- **AC-1 amendments after the fact** (request-rate limit 24 → 62/min; "first date within 7 days" → a share-based rule on the median offset) were made in the unmerged branch to fit the backfill result. They stay in as merged; confirm or revert. The first-date proxy (`items.release_date`) is noisy: 40 % of items fail the original per-item rule.
- **AC-5**: the 30-calendar-day regime label calls only 2025-10-24 `falling` in the crash week 2025-10-22…28. The median item fell ≈ −18 % on 10-23/24 and recovered within days, so a 30-day lookback can't label it; a 7-day lookback would label 10-23…28 (not 10-22: the drop is not in the data yet). Changing the lookback to fit this week is tuning on the example, so it was not done.
- **Index drift**: the chain-linked median of daily returns diverges from the median item ratio in thin early years (2013-14). Kept; treat pre-2018 regime labels as unreliable.
- **T-08**: reopened — GitHub's scheduled runs fire every 4–9 h, not hourly; the Mac job is the real hourly source (AC-3).

## 2026-11-11 — author's go on AC-1 and AC-5

| Decision | Alternatives | Why |
|---|---|---|
| AC-1 rewritten: zero HTTP 429 and a peak ≤ 62 requests per 60 s; first dates by the share rule (≤ 10 % early, median 0…7 d), per-item rule dropped. `cs2 data check` now fails on any 429 | keep 24/min; keep the per-item rule | 27,236 requests at ~1 s gave 0 × 429 (199 × 500 = unknown names, 2 × 502, 9 × 0); no independent first-sale source exists, and the DB release date fails 40 % of items |
| AC-5: crash week is an observation, regime lookback stays 30 days | switch to 7 or 14 days to label 2025-10-23…28 `falling` | `events.csv` has no dated market-wide shocks before 2025-10 (cases, Majors, one update), so an out-of-sample event test was not possible. Without the crash week, fewest label flips/yr favours 30 d (22.7 vs 24.0 for 7 d and 29.9 for 14 d); label shares 2019-01…2025-09: 30 d falling 16.9 / flat 49.9 / rising 33.2 %, 7 d 8.2 / 83.2 / 8.6 %, 14 d 13.3 / 63.0 / 23.6 %. 7 d and 14 d label 10-23…28 `falling` (10-22 is `flat` at both: the drop is not in the data yet). V2/trend read the same index, so switching would change their behaviour |

## 2026-11-11 — local UI added to the plan (author's request)

| Decision | Alternatives | Why |
|---|---|---|
| Plan step 7 + AC-15…18, tasks T-35…T-42: a local web app (`cs2 ui`) on 127.0.0.1 | richer static report only; hosted site | The author wants to see results and change settings; local keeps the 3.8 GB database and secrets on the Mac. The non-goal "A web UI" was narrowed to "hosted or non-local UI, trading from the UI" |
| Views: verdict, run (equity + trades), market + regimes, collector health | — | Author's choice |
| Editable: strategy params, costs and fills, regime and universe, report windows incl. holdout start | — | Author's choice. The verdict thresholds (AC-12) stay read-only so the rule stays fixed in advance; the holdout start can be changed but warns that the holdout is then no longer untouched. Say if the thresholds should be editable too |
| Stdlib server + vanilla JS + inline SVG charts; UI opens DuckDB read-only per request, writes only via `cs2` subprocesses | FastAPI/React | No new runtime dependency, works offline; DuckDB allows one writer, so the UI must not hold the file |
| First task is T-35: re-run the full report | — | The only verdict so far predates the audit fixes |
