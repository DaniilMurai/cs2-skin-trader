# T-60 — Event and release plays on small positions (`event_small`)

Status: done
Branch: worktree-agent-af8e2b90bc6f6f78a
Commit: 1f8c833 (code)

## DoD evidence
- `nice -n 19 uv run pytest -q tests/test_event_small.py` → `10 passed in 13.99s`. At $50 and $100: the first order fills on the bar after the decision day (Major end + `wait`), never earlier; only the two in-band capsules are ever ordered (thin, over-$5, under-$0.10, non-capsule and closed-window items never), sized to equity / 5 and to 5% of the median volume, all `ok`. A window that closes 2 days before the lock ends sells on exactly buy day + 7, no `locked` fill. A one-day $2.40 print on a $12 capsule is not a signal, a +30% print on the fill day gives `limit`. Trades and equity on truncated bars equal the full run up to the cut (3 parameter sets × 4 seeds). `event` and its grid are untouched (asserted).
- `nice -n 19 uv run pytest -q` → `385 passed in 80.97s` (375 before + 10 new).
- Real data, `cs2 report --walk-forward-only --strategies event_small --jobs 2`, `fills.position_cap = 0.05`, `min_fee = 0.01`, capitals 50 and 100, 1.8 min: results hash `3349966260d0510d`, output in the scratchpad (`t60/run/reports/2026-11-14-walk-forward/`). No holdout bar was read: the measurement query and its cache end on 2025-09-30 (asserted), events after that date are filtered out, the run is walk-forward only.

## Measurement (before any rule; events and prices up to 2025-09-30)
Buy at the 3-day median after the decision day × 1.03, sell at the 3-day median after the hold × 0.97 less 5%, minimum fee and rounding (T-55). One unit = one event (for a Major: the median over its capsules). "In band" = $0.10…$5 and ≥ 20 sales a day at entry.

| event type | events (usable) | rule | median net | mean | win |
|---|---|---|---|---|---|
| Major start | 22 (14–16 with capsule prices) | buy at start, hold 14 / 30 d | −61% / −67% | −57% / −61% | 0% |
| Major end | 22 (18–20) | buy at end, hold 30 / 90 / 180 / 365 d | −16% / −28% / −5% / +34% | −21% / −29% / −5% / +64% | 35% / 25% / 47% / 61% |
| Major end + 90 d | 22 (18–19) | hold 80 / 180 / 365 d | −1% / **+33%** / **+93%** | +14% / +49% / +156% | 47% / 79% / 89% |
| Case release (the new case) | 41 (35–38) | buy day 0 / 7 / 30, hold 30 d | −85% / −71% / −46% | −81% / −67% / −45% | 0% / 0% / 3% |
| Case leaves the drop pool | 34 (31–32; 15–19 in band) | buy day 0, hold 30 / 90 / 180 d (in band) | −6% / +14% / −27% | +21% / +96% / +143% | 47% / 53% / 40% |
| Capsule release (first bar) | 233 capsules (many share a release date) | buy day 3 / 14 / 30, hold 30 d | −66% / −17% / −6% | −48% / −18% / −10% | 4% / 16% / 36% |
| Update marker | 1 (2025-07-16) | 3,134 in-band items, hold 7 / 30 / 60 d | −15% / −15% / −9% | −16% / −11% / 0% | one event |

- **Can be concluded:** release plays are not tradable on the long side. A new case trades at 59% of its first-days price after a week, 24% after a month, 5% after half a year (lower than day 1 in 95% of 40 cases); a new capsule at 37% after a month. Buying on any day of the first two months loses in > 90% of cases, the 7-day lock alone costs the median −42…−64%. The only winning side would be selling at release, which needs the item before it exists on the market. Buying capsules while the Major is on loses in 16 of 16 Majors.
- **Cannot be concluded:** that anything else works. Pool exits have a median that changes sign with the hold (15–19 in-band events, mean carried by a few 3–10× cases), 2 Majors have capsule prices before the start, and there is one update marker. Major end + 90 days is the only rule with a positive median and a positive lower quartile (+4% at 180 days), also for the 8 Majors since 2019 (+40%, 6 of 8), but it is 19 overlapping events in a decade when capsules mostly rose, and the gain needs ≥ 180 days: over 80 days the same entry is −1%.

## Results (24 test folds chained, 2020-01-01 … 2025-09-30, cash-out unit)
| strategy | $50 return | $50 max DD | $50 trades | $50 fees | $100 return | $100 max DD | $100 trades | $100 fees |
|---|---|---|---|---|---|---|---|---|
| event_small | −70.0% | 94.1% | 134 | $8.88 | −58.7% | 92.7% | 130 | $18.06 |
| cash | −5.0% | 0% | 0 | $0 | −5.0% | 0% | 0 | $0 |
| bh_median (of 15 baskets) | −58.0% | 57.6% | 421 | $0 | −50.6% | 56.2% | 439 | $0 |

Better than cash in **6 of 24** folds at each capital; traded in **20 of 24** folds (4 folds had no open window). Median 91-day fold −11.8% / −8.8%, best +414% / +418% (2023 Q1, the CS2 announcement), the next fold −68%, worst fold drawdown 68%. Chosen for a later holdout run at both capitals: `wait 90, hold 180`.

## Variants tried (AC-19(e))
- Grid: 4 (`wait` 0/90 × `hold` 180/365), one design, no redesign after the result. 4 × 25 tuning windows × 2 capitals = 200 tuning runs, all in `variants_tried`. No diagnostic runs. The measurement looked at 11 Major offsets, 20 case-release, 20 pool-exit and 15 capsule-release offsets and 6 for the update marker (fixed lists, written before the output was seen).

## Findings
- The strategy fails on walk-forward: not better than cash in 18 of 24 folds (4 of them without a trade), AC-19(c) (drawdown ≤ 40%) fails. The measured edge is a 180–365-day hold, and the harness cannot hold it: each 91-day fold starts in cash, buys again and is valued at its end, so the chain pays the entry cost every fold and holds for ≤ 90 days, where the measurement already shows −1%. The walk-forward result therefore says "no edge inside 91 days", not "the long hold loses"; the long hold itself is untested by the engine.
- AC-19(b) (≥ 30 closed trades on the holdout) cannot be met by this rule: ~2 Majors a year × ≤ 5 positions.
- Sparse events: the two capitals trade the same capsules; at $100 fees double because positions do.
- `event` was not changed; its run_ids hold. `cs2 ui` shows no built-in defaults for `event_small` (`ui/api.py` lists them per strategy; not touched).
- Left alone: the idle `multiprocessing` workers of the main checkout (pids 18748…18758), not started by this task.

## Proposed changes to tasks.md / contracts.md
- contracts.md: registry entry `event_small`, family `event`.
- tasks.md: T-60 done, fails on walk-forward. T-62: `event_small` should not spend its holdout run in the verdict; if the author wants the capsule idea tested, it needs a separate long-horizon check (one run 2015…2025-09 with no fold restarts, or walk-forward with `test_days` ≥ 365), decided before looking.
