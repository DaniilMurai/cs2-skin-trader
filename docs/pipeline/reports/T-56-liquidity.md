# T-56 — Liquidity filter and position cap

Status: done
Branch: claude/nostalgic-austin-6d355c
Commit: 915dea2

## DoD evidence
- `nice -n 19 uv run pytest tests/test_liquidity.py` → `18 passed in 0.56s`. Hand-computed: band $0.50…$5.00 with floor 5 keeps only the three in-band items (both edges included; $0.40, $30 and median-4 items are out); median 100 × 5% = room for 5: buy 8 → 5 filled, reason `position_cap`, balance 100 → 95; holding 3 → 2 filled, holding 5 or 9 → 0; two orders on one day share the room (3 `ok`, then 4 → 2); look-ahead: bars after the decision day replaced by price $500 / volume 90,000 or deleted → same universe, same liquidity table, same fills; a run decided on a day with median 40 (room 2) and filled on a day where the median would be 220 (room 11) fills 2.
- Run identity: with default settings the toy run equals `PINNED` from `tests/test_forward.py` (`6948fb2a80a57b97`, written by T-55); switching on either limit gives a different run_id.
- `nice -n 19 uv run pytest -q` → `346 passed in 63.12s` (328 before + 18 new).

## What changed
- `market/universe.py`: split into `liquidity(bars, on)` (per item: first_t, last_price, 30-day median volume), `liquid_band(table, min_price, max_price, min_median_volume)` and `members`; `universe()` is the same filter as before plus the band's top.
- Settings, both off by default: `universe.max_price_usd = 0` (0 = no upper bound), `fills.position_cap = 0` (0 = off; AC-19(d) needs 0.05) → `FillModel.position_cap`.
- Engine: a buy is cut to `floor(position_cap × median) − units held`, rest cancelled, `Fill.reason = "position_cap"`. The median is taken on the decision bar and passed to the next bar's fills. Hourly runs use the Steam median of the last finished UTC day.
- `MarketView.liquidity()` (daily and hourly): the table at `now`, so a strategy can apply its own band from params.

## Choices made
- Run identity: a new limit is left out of the hashed spec while it is 0, so old run_ids hold; any non-zero value enters the hash.
- No room for an item with no known median (not in the 30-day window): unknown liquidity is treated as none.
- Sales are never cut by the position cap. When two limits bind equally, the older reason wins (`volume_cap`).
- The market index (regimes) ignores `max_price_usd`: it measures the market, not what we can afford.
- Per-strategy bands go through `market.liquidity()` + `liquid_band`, not through `universe.*` (global: it would change every strategy's universe and run_id in one report).

## Findings
- 5% of the median means an item needs ≥ 20 sales a day to hold one unit, ≥ 40 for two. With `min_median_volume = 5` most of the universe gets room 0, so T-57/T-58 should set their floor to ≥ 20 or expect mostly `position_cap` fills.
- The cap is checked when buying. If the median later falls, a held position can exceed 5% without any new order; T-61 must decide whether AC-19(d) counts that.

## Proposed changes to tasks.md / contracts.md
- contracts.md: `FillModel.position_cap`; `Fill.reason` + `"position_cap"`; `MarketView.liquidity()`; `apply_fills(..., median_volume)`; config rows `universe.max_price_usd`, `fills.position_cap`; run_id note (zero-valued new limits are not hashed).
- tasks.md: T-56 file path is `src/cs2trader/market/universe.py`. T-57/T-58: run with `fills.position_cap = 0.05` and a floor ≥ 20. T-61: define AC-19(d) for a median that falls while holding.
- UI follow-up (not touched): `pages.js` has no label for reason `position_cap`; the two settings are not in the editable list (`ui/settings_api.py`, `ui/plain.py`).
