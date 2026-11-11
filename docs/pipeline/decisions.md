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
