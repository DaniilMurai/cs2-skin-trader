# Spike results

## S-06 — hourly collector host
**Script:** `spikes/06-collector-host/` (`collect.py`, `com.example.cs2collector.plist` — written, NOT installed/loaded; nothing persistent changed)
**Answer:** partly. The Mac alone as it is now: no. With a changed setup or an extra host: yes.
**Facts:**
- Machine: MacBook Pro M3 Pro (Mac15,7), now on AC (80%, not charging). `pmset -g`: `sleep 1` (1 min idle sleep), `womp 1` on AC and `womp 0` on battery, `powernap 1`, `hibernatemode 3`. Battery profile: `displaysleep 2`. Today it is kept awake only by assertions from Comet (audio/video), coreaudiod and Claude ("Electron", 88 h), not by settings.
- `pmset -g sched`: only system wake events (calaccessd, acmd), no user wake schedule.
- Sleep history (`pmset -g log`, 2026-11-03 16:11 to 2026-11-09 12:53, ~141 h): 461 sleep/wake transitions since boot 2026-11-04. When idle the Mac does a DarkWake (maintenance, ~0.2 s, about every 15-20 min, mDNSResponder asks for 2 h maintenance wakes) then sleeps again. The longest wake-to-wake gaps were 5.4 h, 5.3 h, 4.8 h, 4.1 h; 12 gaps > 1 h in 6 days.
- Hours with at least one wake or DarkWake: 120 of 141. Worst 48 h window: 36 hours covered, so < 44 needed by AC-3. Counting DarkWakes is optimistic: a DarkWake lasts 2-5 s and it is not proven that network is up in it (networkoversleep 0).
- launchd.plist man page (this Mac): "Unlike cron which skips job invocations when the computer is asleep, launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event upon wake." So StartCalendarInterval does catch up, but only ONE run per sleep: a missed hour gives no snapshot for that hour (the feed has no history, so a gap is lost data). The run at wake gives a snapshot with timestamp of wake time, not of the missed hour.
- cron: skips jobs while asleep, no catch-up (same man page); also needs Full Disk Access on modern macOS. Worse than launchd.
- `caffeinate -i` loop: keeps the Mac awake only while the process runs and the lid is open (or AC + external display for clamshell); dies on reboot/logout, no restart unless wrapped in launchd KeepAlive. Reliable only if the laptop never leaves its desk; battery drain if unplugged.
- Wake by schedule: `sudo pmset repeat wakeorpoweron ...` only supports ONE repeating event per day-pattern (not hourly); `pmset schedule wake` can add many one-off events (needs sudo, wakes full, but the Mac must be on AC / lid open). Not applied here.
- GitHub Actions `schedule` (documented behaviour, not tested here): min interval 5 min; runs are often delayed by 10-60+ min at high load (especially at the top of the hour; use e.g. minute 17) and may be dropped; in public repos schedules are auto-disabled after 60 days without repo activity. Free: unlimited minutes for public repos, 2000 min/month for private (hourly 1-min job ~ 730 min/month, fits). Committing snapshots to the repo grows it: GitHub recommends repos < 1 GB (hard warnings ~5 GB); better to push to Releases/artifacts (artifact retention max 90 days) or a separate data repo.
- Free VPS (e.g. Oracle Cloud Always Free, or any 1 vCPU box) + cron: reliable, but needs account/card, setup and upkeep. Not tested.
- Feed download test: HTTP 200, 0.91 s, 2 510 421 bytes, 27 230 items (name, volume, price as string).
- Size per snapshot: raw JSON 2.51 MB; gzip -9 275 KB; zstd -19 222 KB; xz -9 209 KB; volume+price columns only (names stored once), xz: 91 KB. (pyarrow not installed, Parquet not measured; columnar xz figure is the proxy.)
- Extrapolation hourly (24/day): gzip JSON 6.6 MB/day, ~200 MB/month, ~2.4 GB/year. xz full 5.0 MB/day, 150 MB/month, 1.8 GB/year. Columnar values only ~2.2 MB/day, ~66 MB/month, ~0.8 GB/year. Fine for local disk; too big for a git repo of raw JSON after ~half a year.
**Consequence for the plan:**
- AC-3 (>= 44/48) cannot be promised with the laptop running launchd on defaults: measured worst 48 h window had only 36 hours with any wake, and launchd coalesces missed runs into one.
- Recommended host: GitHub Actions scheduled workflow (cron `17 * * * *`) uploading each snapshot compressed to a separate data store (Release asset / separate repo / artifact), plus the launchd agent on the Mac as a second source; merge and deduplicate by feed `time` field. Delays in Actions are OK as long as the snapshot carries the feed's own `time`. Actions skips/delays are the main risk and are untested here, so AC-3 should be measured over the first 48-72 h after starting.
- Alternative if the author wants everything local: Mac permanently on AC with `sudo pmset -c sleep 0` (or caffeinate under launchd KeepAlive), lid open or external display. Costs power and the laptop cannot be moved.
- Author must decide: (1) GitHub vs local vs VPS; (2) public or private repo (private minutes are enough); (3) where to keep data (storage format: store names once, volume+price per snapshot, xz/zstd); (4) whether to accept AC-3 measured in "snapshots with timestamp", tolerating late snapshots; (5) whether to touch pmset (needs sudo, not done in this spike).

## S-05 — Python libraries on 3.14
**Script:** `spikes/05-python-libs.py` (+ `spikes/05-python-libs/test_h.py`, venv in `spikes/05-python-libs/.venv`, ignored by `.venv/` in .gitignore; the repo is not a git repo yet so `git check-ignore` could not be run)
**Answer:** yes. All eight install and work on Python 3.14; 3.13 was not needed and not tried.
**Facts:**
- uv 0.13.7, CPython 3.14.2 (uv-selected). Installed with `uv pip install --only-binary :all:`, so every package is a wheel (a source build would have failed). 44 s download, 20 packages total.
- Versions: duckdb 2.0.0, polars 2.1.0 (+ polars-runtime-32 2.1.0), pyarrow 26.0.1, httpx 0.28.1, numpy 2.5.4, scipy 1.18.1, pytest 9.2.0, hypothesis 6.170.15 (also httpcore 1.0.9, anyio 4.16.0).
- Smoke test: polars 1M rows built 0.011 s, write_parquet 0.016 s, duckdb group by + window (avg over rows between 2 preceding) on the parquet 0.029 s, returned polars frame via `.pl()`; scipy pearsonr OK (0.001 s); hypothesis-based pytest: 1 passed in 0.44 s.
**Consequence for the plan:** Pin Python 3.14 (e.g. `requires-python = ">=3.14"`, `.python-version` 3.14); no fallback to 3.13 required. Pin exact versions via uv lockfile, since duckdb 2.0 and polars 2.x are new majors.

## S-04 — item categories
**Script:** `spikes/04-categories/classify.py` (+ `bonus.py`); outputs in `spikes/04-categories/out/` (`run.txt`, `bonus.txt`, `categories.json`, `prices.json`; `all.json` is 78 MB, re-downloadable)
**Answer:** yes (0 unknown out of 27 230; a few coarse buckets still need hand mapping)
**Facts:**
- Feed `market.csgo.com/api/v2/prices/USD.json`: HTTP 200, 2.51 MB, 27 230 items.
- DB: ByMykel/CSGO-API, `public/api/en/all.json` (HTTP 200, 78.6 MB, 47 568 records). Licence MIT, repo not archived, last commit 2026-11-05 (4 days before today). Keyed by prefix (skin, sticker, sticker_slab, graffiti, crate, agent, patch, keychain, music_kit, collectible, ...).
- Fields: skins have weapon (id/name), category (Rifles/Pistols/SMGs/Heavy/Equipment/Knives/Gloves), pattern, wear, stattrak, souvenir, rarity (id/name/colour), min/max float, paint_index, market_hash_name. Crates have type (Case 42, Souvenir 150, Sticker Capsule 100, Autograph Capsule 139, Patch Capsule 7, Music Kit Box 13, Pins 4, Graffiti 3, Souvenir Highlight 14, None 9), first_sale_date, contains, contains_rare.
- Gotcha: `market_hash_name` is null for sticker (701 of 11 134), music_kit, collectible, and 14 of 481 crates. Matching these requires the `name` field (stickers) and `"Music Kit | "+name`. Wear suffix / StatTrak™ / Souvenir prefix must be stripped for some lookups.
- Result (share of 27 230): weapon_skin 45.85% (12 484, all have rarity and weapon type), sticker 31.06%, knife 11.27%, graffiti 4.76%, charm 2.21% (incl. Souvenir Charm highlights), glove 1.66%, capsule 0.89%, music_kit 0.68%, patch 0.41%, souvenir_package 0.39%, other 0.37%, agent 0.23%, case 0.15%, music_kit_box 0.05%, crates with type None 0.02% (6: Anubis Collection Package, Gift Package, Pallet of Presents, Sealed Dead Hand Terminal, Sealed Genesis Terminal, X-Ray P250 Package).
- Source: 26 705 by DB lookup, 525 by name rules (all of those were `Souvenir Charm | ... Highlight | ...` names missing from the DB; one pass of the rule fixed it). First pass without that rule: 524 unknown = 1.92% (20 examples were all "Souvenir Charm | Austin 2025 Highlight | ..."). Final: unknown = 0 (0.00%).
- Bonus: skin records have no crate/collection/date fields. The link is reverse: `crate.contains`/`contains_rare` and `collection.contains` list skin ids. Of 2 106 distinct skins, 1 661 are in some crate, 1 455 in some collection, 2 105 in either. `collection.release_date` exists for 109 of 110 collections (e.g. Huntsman Collection 2014-05-02); `crate.first_sale_date` for 261 of 481 crates (Huntsman Weapon Case 2014/06/11, different date format with slashes). Stickers, agents, patches, music kits, graffiti have no date fields.
**Consequence for the plan:**
- Classification from a free MIT source is feasible; AC-4 (<=1% unknown) is met. Keep the name-rule fallback and re-run the unknown check on every new feed snapshot (new item types such as the Souvenir Charm appear within weeks); a threshold alert is cheap.
- The DB is large (78 MB); the collector should fetch it rarely (e.g. daily/weekly), and store a compact name->category/weapon/rarity table instead of the raw JSON.
- Skin -> case/collection and release dates need a join through `contains` lists; dates are partial (crates 54%, collections 99%), so event strategies for sticker/agent/patch releases have no date from this source and must take it from the first-seen date in our own price history.

## S-03 — market.csgo trading rules
**Script/sources:** `spikes/03-marketcsgo-rules/` (notes.md, fetch.sh). Official FAQ/Terms/API docs on market.csgo.com are a JS SPA (HTTP 200, ~300 KB shell, no text via curl/WebFetch; `/docs-v2` 404; `/api/v2/ping` -> `Bad VERSION`, "scheme changed", see /en/api). Official evidence therefore only from the changelog (feedback.market.csgo.com/changelog, = marketcsgo.featurebase.app). Third-party labelled [3P].
**Answer:** partly

**Facts:**
1. Fees.
   - Seller fee 5%: [3P] csdb.gg/trading-site/market-csgo/ ("fixed for all users", cannot be reduced); [3P] sih.app/reviews/marketcsgo also 5%. Official: not found (changelog 2026-06-02 only says item list shows "the Market fee" immediately; no %). One search snippet claimed 2%, unverified, likely confuses with Steam withdrawal fee.
   - Buyer fee: [3P] sih.app 0%. Official: not found. Top-up (deposit) fee exists, shown per payment provider (changelog 2026-09-23: "displaying the top-up fee directly on payment provider cards").
   - Min fee per sale: not found. Dependence on price/account level: not found officially; [3P] says flat for all users. Changelog 2026-10-15: withdrawal page shows "the threshold where the fee rate changes" -> withdrawal fee is probably tiered by amount, thresholds not published in text.
2. Withdrawal.
   - Steam-balance withdrawal fee 2% (official, https://feedback.market.csgo.com/en/changelog/sunny-june-website-update, 2026-06-02: "Reduced the Steam withdrawal fee to 2%").
   - Card: [3P] csdb.gg 5% + processor fixed fee; [3P] sih.app: crypto network fee only, PayPal 2-4%. The two 3P sources contradict each other and are undated. Official per-method fees: not found.
   - Minimum: exists, amount not found; request blocked below it for all methods (2026-07-22 changelog, "below the minimum, the Market will immediately block").
   - Hold of sale proceeds: official text not found. Changelog 2026-06-02 mentions a "frozen balance" excluded from MAX withdrawal -> a frozen/hold concept exists, duration unknown. [3P] csdb: no hold; [3P] search snippets: 7-8 days. Contradictory, unverified. Trade-reversal changelog (2026-06-05) does not mention hold or trade protection at all.
   - Withdrawal page now shows exact net amount incl. all fees (2026-04-03) - cannot be read without login.
3. Trade protection / reversal.
   - Listing a still-locked item: not found. Changelog shows a "trade protection" status exists for active trades (button to refresh status, 2026-06-02) and that buyers can cancel during trade protection (2026-04-03 "compensation ... when a buyer cancels a trade during trade protection") -> platform sells items that are protected on delivery, i.e. the seller sends item and buyer can reverse within Steam's window. Whether the seller's own protected inventory item can be listed: not found.
   - Reversal rules (official, https://feedback.market.csgo.com/en/changelog/trade-reversal-rules-update, 2026-06-05): buyer-caused reversal: "penalty has been reduced to 25%" of transaction amount: 15% to seller, 10% to Market.CSGO. Seller-caused: buyer fully refunded at the price at transaction time, item returns to seller; no % penalty stated. Buy-for reversal by recipient: same 25%, buyer gets 75% back. Reason given: Steam change lets seller relist right after buyer-caused reversal.
   - Community idea post (Nov 2025, 8 votes, not policy, text looks copy-pasted from BUFF): says buyer cancel = 30% penalty, seller cancel = 30-day marketplace ban (pre-June-2026 rules).
4. Buy orders: yes. "Buy in Bulk" (orders), beta, 2026-10-27 (https://feedback.market.csgo.com/changelog/shopping-buy-in-bulk-on-marketcsgo): "Specify the required quantity of items and the maximum price you are willing to pay per skin." Order fee: not found (presumably the same as buyer fee, unverified). Also exists: "Quick sale" (left beta 2026-10-15) - mechanism/fee not found.
5. Bot-relevant: ping/online requirement, delivery-time limits, seller penalties (other than 30-day ban [3P-idea above]): not found in readable sources. Seen: API-key creation needs verified email (2026-10-15); MarketApp must be running to speed up trade processing (2026-09-23); API scheme changed (response "Bad VERSION", old /api/v2 methods need a new version per /en/api, unreadable w/o JS). Public `GET /api/v2/prices/USD.json` works without key (returns items with price, volume).

**Consequence for the plan:**
- Cost model default numbers: sell fee 5% (3P, flagged unverified); buy fee 0% (3P); withdrawal fee to Steam balance 2% (official), to cash 5% worst-case (3P, card) with a parameter per method; reversal penalty 25% (15% back to seller, 10% to Market) only as a risk scenario, not as base cost.
- Unknown and must be parameters: min sale fee, fee tiers by price/withdrawal amount, per-method cash fees and minimums, hold duration (model sensitivity 0 / 7 / 8 days), order fee, delivery deadlines, ping rules.
- To close the gaps, a human must read the logged-in withdrawal/sell pages or the JS-rendered FAQ/Terms/API doc (market.csgo.com/en/faq, /en/static/terms, /en/api) in a real browser; this spike was forbidden to log in and has no headless browser.
- Backtester should not assume locked items are listable; model holding a purchased item for >= 7 days before resale as the conservative case.

## S-01 — Steam price history with the author's cookie
**Script:** `spikes/01-steam-history/probe.py` (`basic`, `rate <names.json> <gap_s>`); outputs in `out/`.
**Answer:** yes.
**Facts:**
- Without the cookie: HTTP 429 (24-byte body). With `steamLoginSecure`: HTTP 200, 0.3–2.1 s.
- One request returns the item's **whole history**: `AK-47 | Redline (Field-Tested)` → 5,287 points, Feb 21 2014 → Nov 09 2026, 197 KB.
- Point format: `["Feb 21 2014 01: +0", 1875.962, "198"]` = date-hour (UTC), price (float), volume (string).
- Granularity: one point per day (stamped 01:00 UTC) up to 2026-10-09, hourly after that (last ~30 days;
  hourly gaps of 2–3 h = hours with no sales). No missing days in the daily part for this item.
- **Currency is the account's wallet currency, not USD**: `price_suffix` = `₴` (UAH) although the request
  had `currency=1`. Prices must be converted with a daily UAH→USD rate.
- Rate: 60 random liquid items (market.csgo volume ≥ 20) at one request per ~2.5 s (~24/min):
  60/60 HTTP 200, no 429. The real limit wasn't pushed further.
- Depth across the 60: first dates from 2013-09-20 to 2026-07-16; median ~2,600 points.
- Price meaning (median vs mean of sales) is not stated by the response; community sources say median.
**Consequence for the plan:**
- AC-1 is feasible. At 24 req/min: 9,302 liquid items ≈ 6.5 h, all 27,230 ≈ 19 h; resumable backfill required (AC-2).
- Add a UAH→USD daily FX series (free central-bank source) to step 1; store raw UAH and converted USD.
- Store daily and hourly parts separately; backtests on daily bars aggregate the hourly tail to days.
- The cookie expires (community reports ~daily refresh): backfill must detect 429/empty and stop with a clear error.

## S-03 addendum — market.csgo Terms of Use, read in the browser (2026-11-09)
**Source:** https://market.csgo.com/en/static/terms ("Last updated: 10 October 2025"), sections 3, 5.
**Facts (official):**
- §3.7: sale proceeds are under an **8-day mandatory retention** from trade confirmation; during it the
  money **can be used to buy items on the platform**, but can't be withdrawn. Release can take up to 12 h more for review.
- §3.2.2: if the user's Steam items are under "temporary locks", transactions on the platform are prohibited →
  an item still under the 7-day trade protection **can't be listed**; resale is possible from day 7 at the earliest.
- §3.9: a buyer reversing a trade loses 25% (15% to seller, 10% platform) and is blocked 30 days; a seller
  reversing → buyer refunded, seller blocked 30 days.
- §5.1: "The sales commission is displayed to the User before confirming a transaction"; no number is
  published there. "Withdrawal fees are dynamic and depend on the withdrawal amount, method, and user's country."
- §5.7: external withdrawals may take up to 8 more calendar days.
**Consequence for the plan:**
- Cost model: buy → earliest sell on day 7 (lock); proceeds reusable for buying immediately; cash-out
  available on sale day + 8; withdrawal fee is a parameter (base 5% card, 2% Steam-balance route per S-03),
  seller fee a parameter (base 5%, third-party sources), both flagged unverified in reports.
- AC-7 changes: "available no earlier than the proceeds-hold date" applies to cash-out only, not to rebuying.

## S-02 — market.csgo price feed
**Script:** `spikes/02-marketcsgo-feed/poll.py` (8 fetches every 150 s; other probes were ad-hoc curl, raw files in `02-marketcsgo-feed/out/`)
**Answer:** partly. Usable as a keyless hourly snapshot of lowest ask + listing count. It is not a sales feed, has no history, and has no sales/volume-traded field.
**Facts:**
- `GET /api/v2/prices/USD.json`: HTTP 200 with no key and no special headers (plain urllib/curl). Body is `{"success":true,"time":<unix>,"currency":"USD","items":[{"market_hash_name","volume":int,"price":"25.665"}]}`. `price` is a string with 3 decimals. Served via Cloudflare (`cf-cache-status: DYNAMIC`), has `last-modified`/`etag`.
- Size 2.51 MB (uncompressed; `vary: Accept-Encoding`), about 27.2k items (27230 to 27266 across fetches), response 0.54 to 1.38 s. No rate-limit headers seen and no 429 in about 15 requests at 1 per 10 s or slower. Limits are not documented anywhere I could read.
- Meaning of `price`: it is the lowest active listing (ask), not a sale price. Cross-check: `prices/class_instance/USD.json` (190 MB, 330,424 per-instance rows, 28 s) lists 385 AK-47 | Redline (Field-Tested) listings. Their minimum is 25.665, equal to the feed `price` 25.665. The class_instance snapshot is about 14 min older, so this is a close match, not a same-moment one.
- Meaning of `volume`: the count of active listings (a stock level), not sales. The same Redline has 416 in the feed and 385 class_instance rows from the older snapshot. Recoil Case has volume 4009 and 0.199 USD. Volume drifts by a few units between fetches (Redline 416 to 419, Recoil Case 4009 to 4054). That is not what a "sales per period" number would do.
- Sample values (poll0): Redline FT price 25.665, vol 416; AWP | Asiimov FT 99.281, vol 57; Recoil Case 0.199, vol 4009; Karambit Fade FN 1692.971, vol 20; Karambit Doppler FN 1195.396, vol 54. No wear/float or sticker separation: one row per market_hash_name, so a cheap float or stickered outlier only shows if it is the minimum.
- Sibling keyless endpoint `prices/orders/USD.json` (2.3 MB) has the best buy order and the buy-order count: `{market_hash_name, price (float), volume}`. Redline bid 23.341 with 242 orders; Recoil Case bid 0.183 with 1273. Useful for a bid/ask spread.
- `class_instance` rows have `price`, `buy_order`, `avg_price`, `popularity_7d`, `phase`, `bg_color`. `avg_price` and `popularity_7d` are non-null for 33,238 rows and null on the liquid items I checked (Redline, Asiimov, Karambit Fade). Their semantics are undocumented (could be a sales average and a 7-day sales count, but I could not verify this). Not enough to rely on.
- Update frequency: the `time` field and `last-modified` change on every fetch. Gaps between `time` values were 164, 80, 164, 163, 163, 160 and 160 s (no more than about 2.7 min), `time` lagging `last-modified` by 15 to 70 s. The file regenerates about every 1.5 to 3 min. Between consecutive fetches 4260 to 5868 of about 27.2k prices changed (about 16 to 21%) and 2817 to 3734 volumes changed. Liquid items such as Redline and Karambit Fade sometimes did not move between fetches. Item count also moves by about ±30 (items appear and disappear).
- Official docs: `https://market.csgo.com/en/api` returns the HTML shell of an Angular app (329 KB, 200), with no readable API text. I could not read the docs. Third-party aggregators (SteamWebAPI, CSPriceAPI) show up in search, not the official docs.
- Sales history: `get-list-items-info`, `history`, `search-item-by-hash-name` and `get-money` all return HTTP 401 `{"success":false,"error":"Bad KEY"}` without a key. All of `/api/v2/*` other than `prices/*` appears to need a key. I did not create an account or enter a key, so whether `get-list-items-info` returns recent sales (it is documented in the market.csgo API as returning history and min/max prices per item) is not verified. The `/en/history` page is the user's own purchase history.
- The feed has no history: only the current state is served, so backtesting means collecting snapshots ourselves from now on. Anything before the first snapshot is not available from this feed.
**Consequence for the plan:** Hourly polling is more than enough, since the file refreshes every 1.5 to 3 min. Store, per snapshot, `price` as lowest ask and `volume` as listing count. Do not model `price` as a fill price or `volume` as turnover. A realistic fill needs ask minus slippage, and the real sales rate must come from elsewhere (the keyed API, or Steam history from S-01). `prices/orders/USD.json` is a free bid-side companion to fetch with each snapshot (+2.3 MB). Storage is about 2.5 MB per snapshot (about 60 MB/day at hourly cadence uncompressed, much less when compressed or stored as deltas). The collector needs a start date; multi-year backtests must use another price history source. Do not fetch `class_instance` regularly (190 MB). Whether a free key unlocks per-item sales history is an open question (needs one decision from the user: create a market.csgo account/key).

## S-07 — market.csgo sales history with the author's API key
**Script:** `spikes/07-marketcsgo-sales/probe.py <names.json> <batch>`; outputs in `out/`. Read-only
endpoints only (`get-list-items-info`, `bid-ask`); the key is read from `.env` and never printed.
**Answer:** yes, but only a rolling window — the last 100 sales per item.
**Facts:**
- Docs (market.csgo.com/en/api, read in the browser): `get-list-items-info?key=&list_hash_name[]=...`
  returns `max`, `min`, `average`, `history: [[unix_ts, price], ...]` per item. Docs warning:
  "more than 5 requests per second ... your key will be deleted".
- **Prices come in the account currency: `"currency": "RUB"`** (Redline 2,166 RUB vs $25.67 in the USD
  feed). Converting needs a daily RUB→USD rate; the account currency is not changed by us.
- Every item returned exactly 100 sales. The window covers 0.04 days (most liquid, ~2,300 sales/day)
  to 550 days (illiquid stickers). Of the 40 most-listed items: median 27 sales/day, min 5.
- Batches: 10 and 20 names per request → HTTP 200, 2.4–12 s. 50 names → HTTP 500 after 15 s, and the
  next request got 503; after a 45 s pause, 20-name batches worked again.
- `bid-ask`: bid and ask ladders (24 bids / 197 asks for Redline). The first element is not always the
  best price (Dreams & Nightmares Case: first bid 66.68 > first ask 66.51), so the ladder must be sorted.
**Consequence for the plan:**
- Real market.csgo sales (time + price) are available, so the forward test can measure real turnover
  and fill odds instead of guessing from listing counts.
- History only exists from when we start collecting: the collector must poll sales often enough that
  100 sales don't roll over between polls. A full sweep of 27,230 items = ~1,360 requests of 20 names
  ≈ 1 h at one request per 2.5 s. So: tiered polling — the most-traded items every hour, the rest
  every 6–24 h, chosen by observed sales/day; dedup sales by (item, timestamp, price).
- Add a daily RUB→USD rate next to the UAH→USD one (S-01).
- `MARKETCSGO_API_KEY` goes into `.env` locally and into a GitHub Actions secret for the collector.

## S-08 — Steam history: historical or current exchange rate? (found at stage 4, 2026-11-09)
**Script:** `spikes/08-steam-fx/probe.py` (one read-only request with the author's cookie).
**Answer:** current rate. Steam converts the **whole** history into the wallet currency at one
(current) rate, not at each day's rate.
**Facts:**
- `Mann Co. Supply Crate Key` (TF2, priced in USD on Steam for years): UAH history is flat at
  91–114 ₴ from 2013-01-01 to 2026-11-09 (99.17 ₴ on 2026-11-09). The NBU rate moved from
  7.99 UAH/USD (2014-01-01) to 45.32 UAH/USD (2026-11-09); at historical rates the 2013 values
  would be ~5× lower. 99.17 / 45.32 = $2.19.
- NBU range endpoint `bank.gov.ua/NBU_Exchange/exchange_site?start=YYYYMMDD&end=YYYYMMDD&valcode=usd&json`
  returns one row per calendar day incl. weekends (`exchangedate` "dd.mm.yyyy", `rate_per_unit`).
- CBR `www.cbr.ru/scripts/XML_dynamic.asp?date_req1=dd/mm/yyyy&date_req2=dd/mm/yyyy&VAL_NM_RQ=R01235`
  returns XML (windows-1251), business days only, decimal comma (`<Value>82,7536</Value>` on 2026-11-09).
- Cross-check: market.csgo Redline FT 2,166 RUB / 82.75 = $26.17 vs 25.665 ask in the USD feed.
**Consequence for the plan:**
- AC-1's "converted with that day's official UAH→USD rate" is wrong for Steam history: a USD price
  = raw UAH / the rate on the **fetch** date. Each stored item records the rate it was converted with.
  Re-fetching later at another rate rescales the whole item; returns are unaffected.
- market.csgo sales (RUB) are converted at the CBR rate of the sale day (forward-filled over
  weekends/holidays); they are real-time prices, not re-converted history.
