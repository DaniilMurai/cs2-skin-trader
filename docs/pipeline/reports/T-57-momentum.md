# T-57 — Filtered momentum (`xs_momentum_liquid`) and `cs2 report --walk-forward-only`

Status: done
Branch: claude/inspiring-goldwasser-1bfc71
Commit: a66ff8e (code), 07b0fe8 (merge of the orchestrator branch), 98467ab (UI label)

## DoD evidence
- `nice -n 19 uv run pytest -q tests/test_xs_momentum_liquid.py tests/test_report.py` → `14 passed`. Strategy: only the two in-band items are ordered (penny, thin, too-dear and under-hurdle items never); after the stop triggers, units locked for 7 days are sold on exactly the day they unlock, nothing is held or bought during the cool-down, buying resumes after it; a 20× one-day print neither ranks an item nor fills a buy (`limit`); trades and equity of a run on truncated bars equal the full run up to the cut (4 seeds).
- Walk-forward only: a store that raises on any `daily_bars` request reaching `wf.holdout_start`, and on `last_day()`, completes `build_report(..., walk_forward_only=True)` with 1 and 2 workers; fold rows equal the full report's.
- `nice -n 19 uv run pytest -q` → `365 passed in 73.61s`.
- Real data, walk-forward only, `fills.position_cap = 0.05`, `--jobs 2`, 6.7 min: `reports/2026-11-14-walk-forward/` in the clone, results hash `14c037043e2eabb7`. No holdout bar was read in this task (every diagnostic run ends 2025-09-30 or earlier).

## Results (24 test folds chained, 2020-01-01 … 2025-09-30, cash-out unit)
| strategy | $50 return | $50 max DD | $50 trades | $100 return | $100 max DD | $100 trades |
|---|---|---|---|---|---|---|
| xs_momentum_liquid | −99.3% | 99.5% | 727 | −96.8% | 98.3% | 731 |
| cash | −5.0% | 0% | 0 | −5.0% | 0% | 0 |
| bh_median (median of 15 baskets) | −58.0% | 57.6% | 421 | −50.6% | 56.2% | 439 |

Per 91-day fold: median return −30.5% ($50) and −15.3% ($100); better than cash in 4 of 24 folds at each capital; worst fold drawdown 65.8% / 57.8%. Fees paid over all folds: $75 on $50, $105 on $100. Chosen for a later holdout run: $50 `lookback 30, rebalance 28, stop 0.30`; $100 `lookback 90, rebalance 28, stop 0.15`.

## Why the old drawdown happened
- Measured on 2024-10-01 … 2025-09-30 at $100 (−99.8%): not a crash but a bleed. Median buy price $0.11; 80% of the 100 items traded both ways were sold below their buy price, median −13% per round trip; $437 bought, $374 sold, $37 fees.
- The rank is one day's price against one day's price, so the top-k is whatever printed an odd price that day; it is bought a day later and sold when the print is gone, paying ~11% per round trip (more under $0.20, where the minimum fee binds).

## Variants tried (AC-19(e))
- Grid: 8 (`lookback` 30/90 × `rebalance` 14/28 × `stop` 0.15/0.30). Two designs were run: 16 distinct variants; 8 × 25 tuning windows × 2 capitals × 2 designs = 800 tuning runs, all in `variants_tried`. Plus 2 single diagnostic runs of the new strategy.
- Design 1 (band on the last price, single-day rank): −99.8% at $50, −99.5% at $100 (`reports/2026-11-13-walk-forward/`). One fold lost 87%: an item ranked on a $19 print was filled at $41.49 the next day and sold at $0.074. Design 2 (final) ranks on 7-day median prices and puts a limit on buys. No further changes were made after its result.

## Findings
- The filter and the limit removed the penny-item and odd-print losses; the strategy still loses in 20 of 24 folds. Liquid momentum on Steam daily prices does not cover an 11% round trip.
- The stop does not keep the drawdown near its setting: units are locked for 7 days after a buy, and each re-entry starts a new peak, so stops add up. AC-19(c) (≤ 40%) fails on walk-forward already.
- `MarketData.median_volumes` now computes medians for the ordered names only when the day's table is not cached (same values, test added): with `position_cap` on, the engine was caching a ~1 MB table per day per worker.
- Data: `Souvenir MAG-7 | Navy Sheen (Battle-Scarred)` shows $41.49 on 2023-12-28 and $0.07 a week later; `cap_price_jumps` lets it through.

## Proposed changes to tasks.md / contracts.md
- contracts.md: registry entry `xs_momentum_liquid`; `cs2 report --walk-forward-only` (`evaluate_all` / `build_report(..., walk_forward_only=True)`), output in `reports/<date>-walk-forward/` with `walk_forward.json` (chosen params, `n_variants`, per-fold params), `bh_median` = median basket by chained walk-forward return; no `verdicts.json` content.
- tasks.md: T-62 — decide whether `xs_momentum_liquid` uses its one holdout run at all (it fails on walk-forward). T-58/T-60: run with a scratch `cs2.toml` whose `data_dir` points at the clone; a run of one strategy at two capitals takes ~7 min with `--jobs 2`.
- Follow-up: a check for one-day price prints in `store.cap_price_jumps`.
- Every newly registered strategy needs an entry in `src/cs2trader/ui/plain.py` (`STRATEGIES`, and `PARAMS` for its grid keys), or `tests/test_ui.py::test_strategies_and_settings_carry_plain_labels` fails: T-58 and T-60 must add theirs. Added here with the orchestrator's permission.
