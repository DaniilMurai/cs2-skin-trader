# T-55 — Small-budget cost model

Status: done
Branch: claude/ecstatic-lumiere-bae8e4
Commit: 9058f13

## DoD evidence
- `nice -n 19 uv run pytest tests/test_costs_small.py` → `13 passed in 0.96s`. Hand-computed cases: minimum fee binds (3 × $0.12 pays $0.03, not $0.018); $0.2504 buys at $0.251 and sells at $0.250; $0.004 is not traded (reason `min_price`, holding valued at 0); `cs2 backtest --capital 50` as a subprocess on a seeded store: `qty_filled` is Int64, every fill price is on the $0.001 grid, capital − Σ(qty × price + fee) = final balance.
- `nice -n 19 uv run pytest -q` → `328 passed in 65.37s`.

## What changed
- `backtest/costs.py`: `round_price` (buy up, sell down to the step), `unit_fee` (rate × price rounded up to the step, at least `min_fee`, per item), `tradable`, `sale_proceeds`. The engine uses them for fills and for valuing holdings.
- `CostModel` gains `price_step = 0.001`, `min_price = 0.005`, `min_fee = 0.01`; settable as `costs.*` in `cs2.toml`. Cost stress (×1.5) also scales `min_fee`.
- Whole items: the engine already filled integer quantities only (`math.floor` of balance / unit cost); proved by tests. Added one guard: a non-integer `Order.qty` now raises `InvariantViolation`.

## Compatibility
- **Every run_id changes** (the spec hash includes the new `CostModel` fields). Old `spec.json` files still load, with the new defaults.
- **Results change, so the report's results hash will change** on the next `cs2 report`. Example (synthetic, toy strategy, $100, 19 trades): final cash-out equity 63.182566 → 63.1617. No flag hides it; setting the three fields to 0 gives the old number back (pinned in `tests/test_forward.py`).

## Assumptions (settings, not facts)
- `min_fee = 0.01` per item sold: market.csgo publishes no minimum fee (S-03). Pessimistic placeholder; it binds below $0.20.
- Rounding direction (buy up, sell down, fee up) is the worst case, not a documented rule.
- The fee and its minimum are charged per item, not per order.

## Findings
- Measured, asks feed 2026-11-13 20:44 UTC, 27,157 items: every USD price is a whole $0.001; lowest ask $0.005; 864 asks under $0.01. The bids feed contains bids of $0.000 (now never a sale).
- One snapshot only: the step and smallest price may come from RUB→USD conversion and move with the rate.
- The official fee pages are still unreadable without a browser session (S-03), nothing new confirmed.

## Proposed changes to tasks.md / contracts.md
- contracts.md: `CostModel` + 3 fields; `Fill.reason` + `"min_price"`; Conventions "No rounding inside the engine" → "fill prices and fees are rounded to `price_step`"; config table rows for the 3 keys.
- tasks.md: `capitals` default is still [100, 1000]; adding 50 belongs to T-61 (AC-19) — it makes the report ~1.5× longer.
- UI follow-up (not touched here): `pages.js` has no label for reason `min_price`; the three settings are not in the UI's editable list.
- T-58 should treat `min_fee` as a sensitivity parameter (0.001 / 0.01), since it decides sub-$0.20 flipping.
