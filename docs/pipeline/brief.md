# Project Brief: CS2 skin trader — research

## Gist
A research tool that tests whether a trading bot on **market.csgo** can make money on CS2
skins after real costs: the ~5% seller fee, the cash-withdrawal fee, the 7-day trade lock,
limited daily volume and no short selling. It replays multi-year Steam price history
(as a stand-in for market.csgo's past, which isn't published) and our own market.csgo
price snapshots collected from now on, and compares strategies against buy-and-hold and
cash. Downturn behaviour matters most, because the market has been falling since the
October 2025 trade-up update. Success = a reproducible report that ranks strategies by
out-of-sample, net-of-cost results per market regime, including a clear
"nothing beats costs" verdict if that is the outcome. No real-money trading.

## User and scenario
The author alone, running CLI commands locally on macOS: a one-off Steam history
backfill, a scheduled market.csgo collector, and backtest runs that write a report.
The idea started from a TikTok video of a market.csgo bot; the project checks whether
that kind of bot survives real costs.

## Scope
**In scope:**
- **Target venue: market.csgo.** Costs, settlement and results are modelled for it.
- Backfill of multi-year daily Steam Community Market history (price + volume) — the only
  free long history, used as a proxy for market.csgo prices.
- A forward collector of market.csgo snapshots (price + volume for all items), so the
  Steam→market.csgo price gap can be measured and strategies forward-tested on real
  market.csgo data.
- A backtester with: market.csgo fees, the 7-day lock after every buy, the seller-proceeds
  hold, participation caps on volume, no shorting.
- **The two strategies from the video, tested first** (from a screenshot the author
  shared; the video gives no rules beyond these lines, so the exact rules are our reading):
  - **V1 "falling market"**: liquid knives and gloves only; buy after a drop over the
    past week (dip buying); "protection on" — read as a stop-loss / exit rule, tested
    with and without it.
  - **V2 "rising market"**: pick categories by their recent growth; buy more actively
    while they rise (category momentum).
  - The video says they were "checked on 2 months of history"; we test them on years of
    history and per regime.
- Other strategy families for rising and falling markets: trend following / go-to-cash,
  cross-sectional momentum, mean reversion after overshoots, event and seasonal effects
  (case drop-pool changes, Majors and sticker capsules).
- **Two horizons:** days to months on the multi-year Steam history (proxy for the past),
  and hours to days on the market.csgo snapshots our collector records from now on
  (hourly collection, forward test only).
- Regime labelling (rising / falling / flat) of a market index, with results per regime.
- Walk-forward evaluation with the October 2025 crash and the 2026 decline held out.
- Two profit units, reported apart: cash withdrawn from market.csgo (main) and
  Steam wallet (for comparison with selling on Steam).
- Starting capital: **$100 and $1,000**, each run and judged separately.

**Out of scope (non-goals):**
- Real-money trading, placing orders on market.csgo or Steam, or any account automation
  beyond reading prices.
- Paid data APIs (decided 2026-11-09: free sources only).
- Float, pattern and sticker premiums on individual items: each `market_hash_name` is
  treated as one fungible item.

## Stack and environment
- macOS 27.2, 12 cores, 18 GB RAM, ~89 GB free disk.
- Python via `uv` (system Python 3.14.7; the project may pin 3.13 if a data library
  lacks 3.14 wheels, checked at stage 3).
- Storage: Parquet + DuckDB (a local analytical database in one file, no server).
  Dataframes: polars. Tests: pytest.
- Our own backtester, not backtrader/vectorbt: the 7-day lock, proceeds hold, volume caps
  and venue fee rules don't fit the stock-market assumptions those libraries make.
- No git repository yet.

## External dependencies
- **market.csgo public price feed** — `https://market.csgo.com/api/v2/prices/USD.json`.
  Checked 2026-11-09: HTTP 200, no key, ~2.5 MB JSON, every item with
  `market_hash_name`, `price` (USD, string) and `volume`. It is a current snapshot only:
  no history. What `price` means (lowest listing vs recent sale) and what period `volume`
  covers are **unverified** → spike. Rate limits unknown.
- **market.csgo costs** (third-party reviews, not official): seller fee 5%, buyer fee 0%;
  a separate withdrawal fee, reported as ~5% or method-dependent; sale proceeds
  withdrawable ~8 days after a sale per one review, immediately per another → check on
  the official site before the cost model is fixed.
- **Spike results (2026-11-09, spikes/FINDINGS.md):** market.csgo `prices/USD.json`
  `price` = lowest active listing (ask), `volume` = number of active listings (not sales);
  `prices/orders/USD.json` = best bid + buy-order count; both keyless, updated every
  ~1–3 min. Sales-history endpoints need an API key (`MARKETCSGO_API_KEY`, the author
  creates it). Official Terms §3.7: sale proceeds held 8 days for withdrawal but usable
  for purchases at once; locked items can't be listed. Steam history comes back in the
  account's wallet currency (UAH), converted by Steam at one current rate for the whole
  history (S-08), so USD = raw / the NBU rate on the fetch day.
- **Collector host (decided 2026-11-09):** a scheduled GitHub Actions workflow as the main
  hourly collector, snapshots stored outside git history; a launchd job on the Mac fills gaps.
- **Steam Community Market price history**
  (`steamcommunity.com/market/pricehistory/?appid=730&market_hash_name=...`).
  Undocumented. Community reports say it returns `[date, price, volume]` points and needs
  a logged-in session (`steamLoginSecure` cookie); on a clean session it returns `[]`.
  Daily for old data, hourly for recent weeks; whether the price is a median is
  **unverified**. The cookie is the author's own, set in `.env` by the author.
  Steam is known to throttle.
- Paid aggregators (CSPriceAPI, SteamWebAPI) sell daily market.csgo history at
  Enterprise tiers; not used.

## Constraints and risks
- **No market.csgo history.** Backtests of the past run on Steam prices shifted by a
  Steam→market.csgo ratio that our collector measures from now on. Until weeks of
  snapshots exist, that ratio is a guess → design; stated as an assumption in every report.
- **Steam history access may not work as reported** (cookie, empty responses, throttling,
  depth, granularity) → spike.
- **Backfill time** at an unknown Steam rate limit could take days → spike measures it;
  the universe may be cut to liquid items.
- **A daily price is not a fillable quote.** We can't buy at yesterday's summary price, so
  the fill model adds a spread penalty and a volume cap; the report shows sensitivity
  to both → design.
- **Lock and settlement on market.csgo are unconfirmed**: whether a locked item can be
  listed, and how long proceeds are held → spike / official docs.
- **Fast flipping can't be backtested on the past.** If the TikTok bot undercuts listings
  or flips within hours, only forward data from our collector can test it, which takes
  weeks to accumulate.
- **$100 capital**: only a few positions at once; cheap items carry a high effective
  fee where fees have a minimum → design (universe and sizing).
- **Survivorship and look-ahead bias**: items that appeared or became popular later must
  not leak into earlier decisions → design (point-in-time universe).
- **Overfitting**: many strategies × parameters on one history → walk-forward, held-out
  downturn, penalty for the number of variants tried.
- **Rules changed over time** (7-day trade protection since July 2025; fees changed) →
  date-dependent cost model.
- **ToS**: automated reading of Steam and market.csgo prices at low rates, own account,
  research use → accepted as is.
- **Claims in the video and in market blogs are unverified**; we rely on our own data.

- **$100 vs knives and gloves**: the cheapest knives cost roughly $40–60 after the crash,
  so V1 at $100 holds one or two items — no diversification, and one bad pick decides
  the result → reported as is; the $1,000 runs show the same strategy diversified.
- **"Checked on 2 months of history" proves little**: two months is one regime; the
  video's results are not evidence → our walk-forward test.

## How to run
Commands that ran on 2026-11-09 (stage 5 slice), from the repo root:

```bash
uv sync                                   # Python 3.14 venv + pinned deps (uv.lock)
uv run pytest -q                          # tests/ only (spikes are excluded)
uv run cs2 fx update                      # NBU UAH→USD, last 30 days → data/cs2.duckdb
uv run cs2 backfill steam --names tests/fixtures/slice_names.txt   # needs STEAM_LOGIN_SECURE in .env
uv run cs2 backtest --strategy bh_universe --capital 1000 --from 2024-01-01 --to 2025-09-30
```

- Secrets: `.env` at the repo root (`STEAM_LOGIN_SECURE`, `MARKETCSGO_API_KEY`), names in `.env.example`.
- Output: `data/cs2.duckdb`; each backtest writes `data/runs/<run_id>/{trades,equity}.parquet, spec.json`.
- Re-running `backfill steam` skips names already fetched with status `ok` (`--refresh` re-fetches).
- Not yet wired: `collect`, `items`, `data check`, `regimes`, `ratio`, `report`, `forward`.

## Glossary
- **market_hash_name** — Steam's unique name of a tradeable item type, e.g.
  `AK-47 | Redline (Field-Tested)`; market.csgo uses the same names.
- **Trade protection / lock** — since July 2025, an item received in any Steam trade
  (including from market.csgo) can't be sold or traded for 7 days, and the sender can
  reverse the trade during that week.
- **Shorting** — selling a borrowed item and buying it back later; impossible with skins.
- **Walk-forward** — tune on one period, test on the next unseen period, roll forward.
- **Regime** — a labelled market state (rising / falling / flat) used to split results.
