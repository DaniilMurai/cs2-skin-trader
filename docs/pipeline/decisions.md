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

## 2026-11-11 — UI rewritten for readability (author: "I don't understand all the things in the UI")

| Decision | Alternatives | Why |
|---|---|---|
| New landing page "Where are we?" (verdict, data freshness, market mood, next steps), glossary of 29 terms with tooltips, plain-language sentences generated in `ui/plain.py` from the report numbers | keep the tables and add a help page | The first UI showed raw jargon and tables |
| Renamed in the UI only: regime → market mood, max drawdown → worst dip, benchmarks → yardsticks, deflated Sharpe → luck check, holdout → final test period (exact term in the tooltip) | keep the quant terms | Author is new to the vocabulary; the research docs keep the exact terms |
| Verdict cards show all five checks; the official first broken rule always wins | show only the first failure | More informative; the note says the official verdict stops at the first |
| "Report out of date" is a heuristic (no `verdicts.json`, hash mismatch, or newer price data); it does not detect changed settings | track settings in the report | Cheap; may be improved later |
| "None hold up" is shown in neutral grey, not red | red | It is a valid research result, not an error |

## 2026-11-11 — Cloudflare Worker as the hourly price-feed collector (author's choice after spike S-10)

| Decision | Alternatives | Why |
|---|---|---|
| A Worker (cron :07) buffers the raw asks/bids feeds in R2; the Mac imports and deletes them | GitHub Actions only; Mac only; paid Worker with gzip | GitHub fires every 4–9 h, the Mac sleeps; Worker cron was on time (delay 0 s in 13 runs) and market.csgo answers Cloudflare (HTTP 200). The author has no budget, so the free plan: raw files, 7–13 ms CPU, borderline against the 10 ms limit |
| The Worker stops adding files at 80 % of R2's free 10 GB (8 GB) | stop at 100 % | The author's request; leaves headroom so a late import can still be stored |
| Sales polling stays on the Mac | polling in the Worker | Free plan allows 50 outgoing requests per run, polling needs ~240 |
| The token travels in a header, not the URL; routes limited to `raw/` | query token | URLs show up in logs (the earlier `wrangler tail` printed it) |
| GitHub collector stays as a backup for now | remove it | Cheap redundancy; revisit after a week of Worker data |

## 2026-11-11 — first verdict after the audit fixes (T-35, hash d3ea18d4859fd53e)

Holdout 2025-10-01 → 2026-11-10, cash-out units. Verdict: **no strategy holds up at $100 or $1,000**; every one fails the first rule (beats cash, which loses 5 % in withdraw fee); deflated Sharpe is negative for all.

| Strategy | $100 | $1,000 |
|---|---|---|
| bh_universe (benchmark) | +61.9 % | +58.5 % |
| cash | −5.0 % | −5.0 % |
| trend_ma | −30.2 % | −30.4 % |
| event | −46.6 % | −21.3 % |
| v1_dip_knives | −65.0 % | −86.7 % |
| bh_knives_gloves (benchmark) | −65.3 % | −51.9 % |
| mean_reversion | −85.7 % | −59.2 % |
| v2_category_momentum | −90.4 % | −76.9 % |
| xs_momentum | −96.1 % | −99.8 % |

Open doubts, not yet checked: (1) bh_universe is a 25-item basket picked by name hash, so +60 % may be luck of the draw (a different hash seed would give a different basket); (2) the backfill universe is today's market.csgo feed, so every item is a survivor, which flatters buy-and-hold; (3) the basket is made of cheap items (≤ budget/25), so it may be dominated by a few cheap-item spikes; (4) the median item's 12-month change is −10 % (Home page), far from +60 %. The second-run hash check is also still to do.

## 2026-11-11 — T-49: the bh_universe +60% was basket luck
30 other hash orders of the 25-item basket, holdout 2025-10-01…2026-11-10, cash-out units:
| capital | median | mean | 10th–90th pct | min / max | share above 0 |
|---|---|---|---|---|---|
| $1,000 | 0.0% | +13.1% | −20.8% … +64.7% | −29.4% / +168.9% | 50% |
| $100 | +3.4% | +6.8% | −21.6% … +45.7% | −29.0% / +111.2% | 57% |
Cash is −5%. The published +58.5% / +61.9% basket sat near the top of this spread, so "buy and hold beats everything" was a lucky pick, not a market return. Consequence: the benchmark in the verdict rule is noisy; a fair comparison needs the median of many baskets. Not checked: survivorship (items that disappeared are not in the data) and cheap-item price spikes; a few large winners drive the mean far above the median.
Open: decide whether the verdict benchmark becomes the median of N baskets.

## 2026-11-11 — The verdict benchmark is the median of 15 baskets
Decision (author, after T-49): AC-12 compares strategies with `bh_median`, the median-return run among
15 baskets (seed 0 = the old `bh_universe` basket, seeds 1..14 the same hash with a seed prefix; ties go to the
lower seed). Alternatives: keep one basket (rejected: a lucky basket decides the verdict), mean (rejected: a few
huge winners dominate it). Cost: 14 extra holdout runs per capital (about 16 s each). The UI shows it as
"Buy and hold (typical basket)". Needs a full report re-run: the old hash d3ea18d4859fd53e no longer applies.

## 2026-11-11 — T-48: three low-turnover strategies, all fail
Holdout 2025-10-01…2026-11-10, cash-out units, walk-forward chose the parameters; benchmark = `bh_median` (new rule).
| strategy | $100 | $1,000 | fees $100 / $1,000 | verdict |
|---|---|---|---|---|
| quality_basket | −54.6% | −53.9% | 2.1 / 19.4 | fails beats_cash |
| case_dip | −64.4% | −58.6% | 6.3 / 66.8 | fails beats_cash |
| cheap_reversion | −32.6% | −19.0% | 43.4 / 30.7 | fails beats_cash |
| bh_median (benchmark) | +3.6% | −7.0% | - | |
| cash | −5.0% | −5.0% | - | |
Deflated Sharpe is negative for all three (−2.7 to −5.2). Fees are small for quality_basket and case_dip, so
their losses are price falls of the items they hold, not trading costs; cheap_reversion loses mostly to fees and
bad entries. Design note: the first quality_basket re-picked 7-10 of 10 items per quarter and paid 46% of capital
in fees on 2023-24; a keep-while-in-top-2n rule (also stated in its spec) cut that to 6%. That change was made
looking at 2023-24 data, not the holdout. Not a full report: the T-50 run will include these strategies.

### 2026-11-12 — T-50: full report with the median benchmark

Run 80 min, results hash fe95acd963df7a5f (T-35 was d3ea18d4859fd53e; the hash differs because three strategies and
the bh_median rows were added). Verdict unchanged: "No strategy holds up at $100 / $1,000". All 9 strategies fail
at both capitals on the first rule, beats_cash. Holdout net return (cash-out) at $100 / $1,000: event -47% / -21%,
trend_ma -30% / -30%, cheap_reversion -33% / -19%, quality_basket -55% / -54%, case_dip -64% / -59%,
v1_dip_knives -65% / -87%, mean_reversion -86% / -59%, v2_category_momentum -90% / -77%, xs_momentum -96% / -100%.
Cash is -5% (withdraw fee). Hash identity across two runs was not re-checked.

### 2026-11-12 — xs_momentum diagnosis: not an engine bug, the strategy buys penny items

Re-ran xs_momentum with default params on the holdout ($100, 905 filled trades, 476 buys). Findings:
- Median buy price is $0.059, 75% of buys are under $0.17; the most-bought items are $0.02-0.30 Souvenir and
  Well-Worn skins and cheap stickers. Ranking by raw lookback return has no price or volume floor, so the top-k
  is always items whose last price jumped on a single thin trade (one item's buy prices span 0.018 to 0.22).
- The strategy buys at close x (1 + 3% spread), fills on the next bar after the spike has reverted, and pays a 5%
  seller fee on exit. Equity went 95 -> 0.35 over the holdout; this is a steady bleed, not one crash.
- The +3.1 Sharpe next to -96% return is volatility drag, not a metric bug: `_sharpe` (evaluate.py) uses the
  arithmetic mean of daily returns, and an equity that shrinks to under $1 has huge swings in both directions.
  The verdict does not use this Sharpe for "beats cash", and the deflated Sharpe is negative, so no verdict changes.
- No engine change needed. A price/volume floor for xs_momentum would be a new strategy variant (tuned only on
  2023-24 data, not the holdout); not built.

### 2026-11-12 — Free data only
Decision (user): the project spends no money. Paid history (CSPriceAPI, from $50/month) is out; T-52 is dropped.
Past data for cross-platform tests comes only from free sources: our own hourly snapshots, Skinport's free
public endpoints, and free tiers of third parties if their terms allow it. Alternative rejected: one paid month
of aggregator history for a 1-year two-sided backtest. Consequence: the arbitrage verdict waits for our own data (T-47).
