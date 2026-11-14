# T-59 (+ T-66) — Cross-platform arbitrage paper test (code-only part)
Status: done
Branch: claude/unruffled-leakey-2eb021  Commit: 8c42da3
## DoD evidence
- `nice -n 19 uv run pytest tests/test_arbitrage_cross.py tests/test_arbitrage.py -q` → 18 passed (9 new, T-47's 9 unchanged). New, hand-computed: Waxpeer 4.00 → market.csgo bid 5.00 at a 10% fee = edge +12.5%, +0.50/day at $50 (the second item does not fit) and +0.90/day at $100; bid falls to 3.00 during the lock → −1.30; default fees, market.csgo 2.00 → Waxpeer 2.50 dipping to 2.25 while listed → 2.051973 back on 2.078 spent; an hour with one venue missing → no candidate, no buy; data after hour h changes neither the candidates nor the buys up to h; 5 two-venue days → "at least 14 days … found 5 (days per venue: …)"; `cs2 arbitrage --mode cross` in an empty dir → exit 6, no traceback
- `nice -n 19 uv run pytest -q` → 355 passed in 103 s (first run)
- Not run: the real `cs2 arbitrage --mode cross` (5 days of market.csgo, 2 of Skinport/Waxpeer; needs ≥ 14)
## Assumptions (also in the `arbitrage_cross.py` docstring and printed by the command)
- market.csgo: sale fee 5% and card withdrawal 5% (third-party, S-03, unconfirmed); payout 16 days after the sale (official terms §3.7 + §5.7: 8-day retention, up to 8 more days); buy fee 0% (third-party); deposit 3.9% (unconfirmed: a top-up fee exists, size not published; Waxpeer's card rate taken); sells into its highest bid
- Waxpeer (official help pages `waxpeer.com/faq/fees-sell`, `fees-withdraw`, `hold-balance`, `fees-deposit`, read 2026-11-13): sale fee 6% (2% only with the mobile app connected, not assumed); 8-day hold; withdrawal 1.5% + network fee, 2% taken; card deposit 3.9% + $0.35, 3.9% taken
- Skinport: sale fee 8% (official blog, S-12); withdrawal 5%, payout 16 days, buyer-side cost 3.9% — all three unconfirmed (the support pages are a JS app), set to the worst value known for the other venues
- Trade lock 7 days on every purchase (S-03 addendum); then the first hour with a quote on venue B within 24 h, else unsold = 0
- Sale as a listing (Waxpeer, Skinport): 1% under B's lowest listing; counted sold only 3 days later, at the lowest price B showed in those 3 days. Nothing measured backs "it sells in 3 days": unconfirmed
- One unit per item per UTC day; both sides ≥ 3 listings, price ≥ $0.20; edge must be 5%..50% (above: treated as a different item, S-12)
- Capital: per UTC day Σ(cost × days in flight) ≤ capital; days in flight 24 (sell on market.csgo), 19 (Waxpeer), 27 (Skinport); no reinvesting
- Excluded from the profit: the $0.35 per deposit, network fees, withdrawal minimums (Waxpeer $5–12 crypto, $150 PayPal: at $50–100 a payout may have to wait), trade reversals
## Findings
- `cs2 arbitrage --mode cross [--capital 50,100] [--min-days 14] [--min-edge 0.05]`; writes `data/arbitrage/cross_edges.parquet` (candidate buys), `cross_summary.parquet` (pair × day), `cross_lots_<capital>.parquet`, `cross_table.md` (one row per ordered pair: days, item-hours, median edge, share tradable, $/day per capital — the table for the report step)
- A separate module, not inside `arbitrage.py`: the wallet mode prices against Steam daily bars, this one against hourly venue quotes with its own sale model; they share only the command
- The money-in-flight rule is what bites at this size: $50 buys about $2.08 of items a day when selling on market.csgo, $100 about $4.17. Daily profit can only be cents even on a good edge
- Selling on market.csgo uses the bid, which S-12 did not (it compared asks): expect the real edge well under S-12's +2%
- Days with fewer than two venues collected are left out of the daily mean (collector gaps, not days without an edge)
- With exactly 14 days only ~2 buy days resolve (lock 7 + wait 3 + 2); ~12 more days resolve all 14
- Fee values can be changed only in code (`CrossParams.venues`); the real run joins tens of millions of quote rows in DuckDB, so run it under `nice -n 19`
## Proposed changes to tasks.md / contracts.md
- tasks.md: T-59 real run ≥ 2026-11-26 (Skinport/Waxpeer started 2026-11-12), re-run ~12 days later; close T-66 as covered by T-59
- tasks.md, new: read Skinport's payout fee/delay and market.csgo's top-up and sale fee in a logged-in browser (user), then set them in `default_venues()`
- contracts.md: CLI row `cs2 arbitrage --mode wallet|cross`; `CrossParams`, `Venue`, `cross_edges(store, p, max_capital)`, `simulate_cross(store, candidates, capital, p)`; the four `cross_*` outputs
