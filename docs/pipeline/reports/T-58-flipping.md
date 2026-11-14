# T-58 — Cheap-item flipping (`cheap_flip`)

Status: done
Branch: worktree-agent-aa7b42ea3c9bff6ed
Commit: 02f6374 (code)

## DoD evidence
- `nice -n 19 uv run pytest -q tests/test_cheap_flip.py` → `10 passed in 5.28s`. Only the two sub-$5, ≥ 20-sales-a-day items with a hand-computed margin above the hurdle (27.8%, 19.3%) are ordered; the 11.8% item only at hurdle 0.05; the $12, thin and undipped items never. A sale fills on exactly buy day + 7 and never earlier (daily), and ≥ 168 hours after the buy on hourly quotes. A one-day half-price print is not a dip, a +30% print on the fill day gives `limit`. Trades and equity on truncated bars equal the full run up to the cut (4 seeds).
- `nice -n 19 uv run pytest -q` → `375 passed in 83.16s` (365 before + 10 new).
- Real data, `cs2 report --walk-forward-only --strategies cheap_flip --jobs 2`, `fills.position_cap = 0.05`, capitals 50 and 100, 24.4 min per run: results hash `22cdb73e1444601c` (`min_fee` 0.01) and `d2a0eecc785e8151` (0.001). Output in the scratchpad (`t58/run-0.01/reports/`, `t58/run-0.001/reports/`), not in the clone's `reports/`, so T-57's folder of the same date is intact. No holdout bar was read: every run and the measurement end on 2025-09-30.

## Base rate (measured before writing rules)
7.72 M item-days, 10,825 items, 2020-01-01 … 2025-09-30: 3-day median price $0.03…$5, traded on ≥ 25 of the last 30 days, median ≥ 20 sales a day. Buy at the next bar × 1.03, sell at the first bar ≥ 7 days later × 0.97 less 5%, with the minimum fee and rounding.
- A round trip ends above its cost in **8.3%** of cases (8.0% when the exit is a 3-day median; 9.4% at `min_fee` 0.001). Median net result −12.3%, mean −11.7%. By price: under $0.20 7.0% win / −17.4% median (10.4% / mean −9.0% at `min_fee` 0.001); $0.20…$1 9.3% / −11.0%; $1…$5 8.3% / −10.8%.
- Not predictable from the buy day. Expected margin (30-day median vs 3-day median, after costs) above 15%: win 15–16%, median −12…−17%, mean −12%. Other splits (depth of the dip, 30-day variation, volume surge, falling fast vs settled, year by year, holding 7 / 21 / 53 days longer): the win rate rises to 15–26%, the mean stays between −9% and −16%. Least bad: a fill ≥ 10% under the 3-day median (win 22%, mean −2.9%) and selling only at the old 30-day median within 60 days (reached in 27%, mean +0.3% before the one-day delay of a real order). The grid's `discount` and `max_hold` come from these two.

## Results (24 test folds chained, 2020-01-01 … 2025-09-30, cash-out unit)
| strategy | min_fee | $50 return | $50 max DD | $50 trades | $50 fees | $100 return | $100 max DD | $100 trades | $100 fees |
|---|---|---|---|---|---|---|---|---|---|
| cheap_flip | 0.01 | −93.2% | 92.9% | 653 | $21.75 | −89.6% | 89.2% | 620 | $29.87 |
| cheap_flip | 0.001 | −91.7% | 91.4% | 667 | $18.74 | −89.8% | 89.3% | 633 | $26.05 |
| cash | 0.01 | −5.0% | 0% | 0 | $0 | −5.0% | 0% | 0 | $0 |
| bh_median (of 15 baskets) | 0.01 | −58.0% | 57.6% | 421 | $0 | −50.6% | 56.2% | 439 | $0 |

Better than cash in **5 of 24** folds at $50 and **3 of 24** at $100 (5 and 4 at `min_fee` 0.001). Median 91-day fold −14.6% / −11.4%, worst fold −35.0% / −30.4%, worst fold drawdown 31.6%. Chosen for a later holdout run (`min_fee` 0.01): $50 `hurdle 0.15, discount 0, max_hold 60`; $100 `hurdle 0.30, discount 0.10, max_hold 60`. `bh_median` at `min_fee` 0.001: −57.2% / −49.8%.

## Variants tried (AC-19(e))
- Grid: 12 (`hurdle` 0.05/0.15/0.30 × `discount` 0/0.10 × `max_hold` 7/60), one design, no redesign after the result. 12 × 25 tuning windows × 2 capitals = 600 tuning runs per `min_fee` setting, 1,200 in all (`variants_tried` holds 600 rows: the second setting overwrites the same keys). Plus 2 single diagnostic runs with default params (2024, −91.5% at $50; 2024 H1 for timing).

## Findings
- The premise does not hold on Steam daily prices: cheap liquid items rarely move 12% in a week, and a dip measured on medians does not come back on average. The strategy does worse than cash in 19–21 of 24 folds; AC-19(c) (drawdown ≤ 40%) fails on the chained walk-forward.
- `min_fee` changes little (−93.2% → −91.7% at $50): the universe's $0.10 floor leaves only $0.10…$0.20 for it to bind, and the loss is price moves plus the ~12% round trip, not the minimum fee.
- Tuning picked `max_hold 60` in 23 of 24 folds at $50 and in all 24 at $100: waiting for the usual price loses less than selling at the unlock, but still loses.
- Hourly: the strategy runs through the engine at `freq="1h"` without changes (test `test_runs_unchanged_on_hourly_quotes`, synthetic quotes). No real `cs2 forward` run: 6 days of snapshots, 28 needed; nothing was forced.
- The strategy reads the cost model with `load_settings()` in its constructor, because `decide` is not given the run's `CostModel`; a cost-stress run therefore plans with the unstressed fees.
- Per-fold rows (`wf_fold_00…23`) are already in `results.parquet`; fold counts need no re-run.
- Left alone: six idle `multiprocessing` worker processes from the main checkout's venv, 2 days old (pids 18748…18758), not started by this task.

## Proposed changes to tasks.md / contracts.md
- contracts.md: registry entry `cheap_flip`, family `flip` (added to `StrategyEntry.family` and `plain.FAMILIES`); note that a strategy may read `load_settings().costs`, or pass the cost model to strategies.
- tasks.md: T-58 done, fails on walk-forward. T-62: `cheap_flip` should not spend its one holdout run. A flip test on hourly market.csgo bid/ask (real spread instead of ±3%) is only possible after ≥ 28 days of snapshots (~2026-12-07).
