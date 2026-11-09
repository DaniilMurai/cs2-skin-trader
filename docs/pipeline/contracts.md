# Contracts

Updated: 2026-11-09. Shapes only — no behaviour. Python 3.14, package `cs2trader` in `src/`,
console script `cs2`. Dataframes are polars; tables live in DuckDB + Parquet under `data/`.

## Conventions
- **Money.** `float` (float64). Prices in USD unless the field name says `_raw`. No rounding
  inside the engine; reports round to cents. Tests compare money with `abs(a - b) < 0.005`.
- **Time.** Every timestamp is UTC, tz-aware `datetime`. A daily bar is a `date` (UTC day).
  A "bar" is the engine's step: one day (backtest) or one hour (forward test).
- **Decision timing.** A strategy decides at the **close** of bar `t` using data ≤ `t`;
  its orders fill on bar `t+1`. Within one bar, sells fill before buys (so same-bar proceeds
  can be spent, Terms §3.7).
- **Item identity.** `market_hash_name: str` everywhere — the same string on Steam and
  market.csgo. No surrogate ids.
- **Currency.** `Currency = Literal["USD", "UAH", "RUB"]`.

## Data models

```python
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Mapping, Any

Currency = Literal["USD", "UAH", "RUB"]

class Category(StrEnum):
    # values from spike S-04; `unknown` must stay ≤ 1% (AC-4)
    KNIFE = "knife"; GLOVE = "glove"; WEAPON_SKIN = "weapon_skin"; STICKER = "sticker"
    CASE = "case"; CAPSULE = "capsule"; SOUVENIR_PACKAGE = "souvenir_package"
    GRAFFITI = "graffiti"; CHARM = "charm"; PATCH = "patch"; MUSIC_KIT = "music_kit"
    MUSIC_KIT_BOX = "music_kit_box"; AGENT = "agent"; OTHER = "other"; UNKNOWN = "unknown"

@dataclass(frozen=True)
class ItemInfo:
    market_hash_name: str        # "AK-47 | Redline (Field-Tested)"
    category: Category           # Category.WEAPON_SKIN
    source: Literal["db", "rule", "none"]  # how the category was found
    weapon: str | None           # "AK-47"; None for non-weapons
    rarity: str | None           # "Classified"
    stattrak: bool
    souvenir: bool
    release_date: date | None    # collection/crate date if known (S-04: partial)

@dataclass(frozen=True)
class SteamPoint:
    market_hash_name: str
    ts: datetime                 # point time, UTC; daily points are stamped 01:00
    price_raw: float             # wallet currency, e.g. 1549.954 (UAH)
    currency: Currency           # "UAH" — from `price_suffix`, never assumed
    volume: int                  # sales in that day/hour, e.g. 198
    granularity: Literal["day", "hour"]

@dataclass(frozen=True)
class FxRate:
    day: date
    base: Currency               # "UAH" or "RUB"
    per_usd: float               # units of `base` per 1 USD, e.g. 45.3206
    source: Literal["nbu", "cbr"]
    filled: bool                 # True = forward-filled (CBR weekend/holiday)

@dataclass(frozen=True)
class AskRow:                    # one item in prices/USD.json
    market_hash_name: str
    ask: float                   # lowest active listing, USD
    listings: int                # active listing count (NOT sales; S-02)

@dataclass(frozen=True)
class BidRow:                    # one item in prices/orders/USD.json
    market_hash_name: str
    bid: float                   # best buy order, USD
    orders: int                  # buy-order count

@dataclass(frozen=True)
class Snapshot:
    kind: Literal["asks", "bids"]
    feed_time: datetime          # the feed's own `time` field — the snapshot's identity
    fetched_at: datetime
    host: Literal["gha", "mac"]
    rows: list[AskRow] | list[BidRow]

@dataclass(frozen=True)
class Sale:                      # one element of get-list-items-info `history`
    market_hash_name: str
    ts: datetime
    price_raw: float             # RUB (account currency, S-07), e.g. 2166.0
    currency: Currency

@dataclass(frozen=True)
class Bar:                       # what the engine sees per item per bar
    market_hash_name: str
    t: date | datetime           # date for daily, datetime (hour start) for hourly
    price: float                 # USD on the source venue (Steam proxy or market.csgo)
    volume: int                  # sales in the bar (Steam) or observed sales (market.csgo)
    ask: float | None            # market.csgo only (forward); None in Steam backtests
    bid: float | None

class Regime(StrEnum):
    RISING = "rising"; FALLING = "falling"; FLAT = "flat"

@dataclass(frozen=True)
class Order:
    market_hash_name: str
    side: Literal["buy", "sell"]
    qty: int                     # > 0
    limit: float | None = None   # USD per unit; None = take the bar's fill price

@dataclass(frozen=True)
class Fill:
    t: date | datetime
    market_hash_name: str
    side: Literal["buy", "sell"]
    qty_requested: int
    qty_filled: int              # 0..qty_requested; the rest is cancelled, not carried (AC-8)
    price: float                 # USD per unit after the spread penalty
    fee: float                   # USD, total for the fill
    reason: Literal["ok", "volume_cap", "locked", "no_cash", "no_position", "limit", "no_data"]

@dataclass
class Lot:
    market_hash_name: str
    qty: int
    unit_cost: float             # USD incl. buyer fee
    bought: date | datetime
    unlocks: date | datetime     # bought + lock_days; may be sold on this bar (AC-7)

@dataclass(frozen=True)
class CostModel:
    valid_from: date
    seller_fee: float = 0.05         # [3P] unverified
    buyer_fee: float = 0.0           # [3P]
    withdraw_fee_cash: float = 0.05  # [3P] card, worst case
    withdraw_fee_steam: float = 0.02 # official, changelog 2026-06-02
    lock_days: int = 7               # Terms §3.2.2: locked items can't be listed
    cashout_hold_days: int = 8       # Terms §3.7

@dataclass(frozen=True)
class FillModel:
    participation_cap: float = 0.20  # share of the bar's volume we may take
    spread_penalty: float = 0.03     # buy at price*(1+p), sell at price*(1-p); placeholder until calibrated
    ratio: Mapping[Category, float] = field(default_factory=dict)  # Steam→market.csgo; missing = ratio_fallback
    ratio_fallback: float = 1.0

@dataclass(frozen=True)
class EquityPoint:
    t: date | datetime
    balance: float               # market.csgo balance, USD
    holdings_value: float        # Σ qty × sell price after spread and seller fee
    equity_cash: float           # (balance + holdings_value) × (1 − withdraw_fee_cash)
    equity_steam: float          # (balance + holdings_value) × (1 − withdraw_fee_steam)
    fees_paid: float             # cumulative

@dataclass(frozen=True)
class RunSpec:
    strategy: str                # registry name, "v1_dip_knives"
    params: Mapping[str, Any]    # {"drop_7d": 0.10, "stop_loss": None}
    capital: float               # 100.0 | 1000.0
    start: date
    end: date
    freq: Literal["1d", "1h"] = "1d"
    costs: tuple[CostModel, ...] = (CostModel(valid_from=date(2013, 1, 1)),)
    fills: FillModel = FillModel()

@dataclass(frozen=True)
class RunResult:
    run_id: str                  # sha256(spec + data_version)[:16]
    spec: RunSpec
    data_version: str            # see Storage → data version
    trades: "pl.DataFrame"       # Fill rows
    equity: "pl.DataFrame"       # EquityPoint rows
    cashout_available: date | datetime  # last sale + cashout_hold_days

@dataclass(frozen=True)
class Metrics:
    net_return_cash: float; net_return_steam: float
    max_drawdown_cash: float; max_drawdown_steam: float
    sharpe_cash: float
    n_trades: int
    fees_paid: float

@dataclass(frozen=True)
class Verdict:
    strategy: str
    capital: float
    status: Literal["holds_up", "fails"]
    first_broken_rule: Literal["beats_cash", "beats_bh", "drawdown", "cost_stress",
                               "deflated_sharpe"] | None
    holdout: Metrics
    deflated_sharpe: float
    n_variants: int
```

## Public signatures

Bodies are `...`. Module path in the heading of each block.

```python
# cs2trader/config.py
@dataclass(frozen=True)
class Settings: ...                       # all fields of the Configuration table
def load_settings(path: Path = Path("cs2.toml"), **cli_overrides: Any) -> Settings: ...
def require_secret(name: Literal["STEAM_LOGIN_SECURE", "MARKETCSGO_API_KEY"]) -> str: ...  # ConfigError if unset

# cs2trader/sources/steam.py
class SteamClient:
    def __init__(self, cookie: str, interval_s: float, max_retries: int) -> None: ...
    def price_history(self, market_hash_name: str) -> list[SteamPoint]: ...  # whole history, one request

# cs2trader/sources/marketcsgo.py
class MarketCsgoPublic:
    def asks(self) -> Snapshot: ...
    def bids(self) -> Snapshot: ...
class MarketCsgoKeyed:
    def __init__(self, key: str, interval_s: float, batch_size: int) -> None: ...
    def sales(self, names: Sequence[str]) -> dict[str, list[Sale]]: ...  # batches of ≤ batch_size

# cs2trader/sources/fx.py
def fetch_rates(base: Literal["UAH", "RUB"], start: date, end: date) -> list[FxRate]: ...  # gap-free, forward-filled

# cs2trader/sources/items_db.py
def fetch_items_db(cache_dir: Path) -> list[dict[str, Any]]: ...  # ByMykel all.json, cached raw, re-downloaded once per UTC day
def classify(names: Sequence[str], db: Sequence[Mapping[str, Any]]) -> list[ItemInfo]: ...

# cs2trader/store.py
class Store:
    def __init__(self, data_dir: Path) -> None: ...
    def upsert_steam(self, points: Sequence[SteamPoint], fx: FxRate) -> int: ...  # rows written
    def mark_fetched(self, name: str, status: Literal["ok", "empty", "error"], n_points: int) -> None: ...
    def fetched_names(self) -> set[str]: ...       # status == "ok"; resume key for AC-2
    def write_snapshot(self, snap: Snapshot) -> Path: ...
    def write_sales(self, sales: Mapping[str, Sequence[Sale]], host: str) -> Path: ...
    def upsert_fx(self, rates: Sequence[FxRate]) -> int: ...
    def upsert_items(self, items: Sequence[ItemInfo]) -> int: ...
    def daily_bars(self, start: date, end: date) -> pl.DataFrame: ...   # Bar columns, Steam, USD
    def hourly_bars(self, start: datetime, end: datetime) -> pl.DataFrame: ...  # market.csgo
    def data_version(self) -> str: ...

# cs2trader/collect.py
def collect_once(store: Store, kinds: Sequence[Literal["asks", "bids", "sales"]],
                 host: Literal["gha", "mac"], now: datetime) -> CollectReport: ...
def sales_due(store: Store, now: datetime) -> list[str]: ...  # tiered by observed sales/day
def collect_status(store: Store, window_h: int = 48) -> CollectStatus: ...  # AC-3
def pull_remote(store: Store, repo_url: str) -> int: ...  # git clone/pull of `mc.data_repo` into data/collector; files added

@dataclass(frozen=True)
class CollectReport:
    kinds_ok: tuple[str, ...]; kinds_failed: tuple[str, ...]; files: tuple[Path, ...]
@dataclass(frozen=True)
class CollectStatus:
    snapshots: int; min_item_share: float; gaps: tuple[tuple[datetime, datetime], ...]

# cs2trader/market/universe.py
def universe(bars: pl.DataFrame, on: date, s: UniverseSettings) -> list[str]: ...  # data ≤ on only

# cs2trader/market/index.py
def market_index(bars: pl.DataFrame, items: pl.DataFrame,
                 by: Literal["all", "category"]) -> pl.DataFrame: ...  # columns: t, group, level

# cs2trader/market/regimes.py
def label_regimes(index: pl.DataFrame, s: RegimeSettings) -> pl.DataFrame: ...  # t, regime; point-in-time (AC-5)

# cs2trader/market/ratio.py
def steam_to_market_ratio(store: Store, s: RatioSettings) -> pl.DataFrame: ...
# columns: category, side ("ask"|"bid"), median, q25, q75, n_items, fallback: bool  (AC-6)

# cs2trader/backtest/view.py
class MarketView(Protocol):                 # what a strategy may see; raises LookAheadError past `now`
    now: date | datetime
    def history(self, lookback: int) -> pl.DataFrame: ...       # Bar rows, bars (now-lookback, now]
    def universe(self) -> list[str]: ...
    def items(self) -> pl.DataFrame: ...                          # ItemInfo rows
    def index(self, by: Literal["all", "category"], lookback: int) -> pl.DataFrame: ...
    def regime(self) -> Regime: ...
class PortfolioView(Protocol):
    balance: float
    def lots(self) -> list[Lot]: ...
    def sellable(self, name: str) -> int: ...                    # unlocked qty on the next bar

# cs2trader/strategies/base.py
class Strategy(Protocol):
    name: str
    params: Mapping[str, Any]
    def decide(self, market: MarketView, book: PortfolioView) -> list[Order]: ...

# cs2trader/strategies/registry.py
@dataclass(frozen=True)
class StrategyEntry:
    name: str                                      # "v1_dip_knives"
    family: Literal["video", "trend", "momentum", "mean_reversion", "event", "benchmark"]
    factory: Callable[[Mapping[str, Any]], Strategy]
    grid: Mapping[str, Sequence[Any]]              # walk-forward search space
    spec_md: str                                   # written rule spec, report appendix (AC-10)
REGISTRY: Mapping[str, StrategyEntry]
# required entries: v1_dip_knives, v2_category_momentum, cash, bh_universe, bh_knives_gloves

# cs2trader/backtest/engine.py
def run(spec: RunSpec, store: Store) -> RunResult: ...
def apply_fills(orders: Sequence[Order], bar: pl.DataFrame, book: "Book",
                cost: CostModel, fills: FillModel) -> list[Fill]: ...  # pure; AC-7, AC-8 unit tests

# cs2trader/evaluate.py
def walk_forward(entry: StrategyEntry, capital: float, store: Store, s: WalkForwardSettings) -> WalkForwardResult: ...
def holdout(entry: StrategyEntry, params: Mapping[str, Any], capital: float, store: Store,
            s: WalkForwardSettings, stress: bool = False) -> RunResult: ...
def metrics(result: RunResult, regimes: pl.DataFrame | None = None) -> Metrics | dict[Regime, Metrics]: ...
def verdict(strategy: str, capital: float, wf: WalkForwardResult, ho: RunResult,
            ho_stress: RunResult, bh: RunResult, cash: RunResult) -> Verdict: ...

@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[tuple[date, date, Mapping[str, Any], Metrics], ...]  # test start, end, chosen params, metrics
    chosen_params: Mapping[str, Any]          # params used on the holdout
    n_variants: int                           # grid size × folds, for the Sharpe deflation

# cs2trader/forward.py
def forward(entry: StrategyEntry, params: Mapping[str, Any], capital: float, store: Store,
            start: datetime, end: datetime) -> RunResult: ...    # freq="1h", AC-14

# cs2trader/report.py
def build_report(store: Store, out_dir: Path) -> Path: ...        # reports/<YYYY-MM-DD>/report.html
def results_hash(results: pl.DataFrame) -> str: ...              # AC-13
```

### CLI (`cs2`, argparse)

| Command | Does | AC |
|---|---|---|
| `cs2 fx update` | fetch NBU (UAH) + CBR (RUB) rates up to today | AC-1 |
| `cs2 items update` | fetch ByMykel DB, classify every known name, write `items` | AC-4 |
| `cs2 backfill steam [--limit N] [--names FILE] [--refresh]` | fetch Steam history, resumable | AC-1, AC-2 |
| `cs2 collect run --kinds asks,bids,sales --host gha\|mac` | one collector run | AC-3 |
| `cs2 collect pull` | merge snapshots from the remote data store into `data/` | AC-3 |
| `cs2 collect status [--hours 48]` | count snapshots, list gaps > 2 h | AC-3 |
| `cs2 data check [--categories]` | coverage, duplicates, FX, unknown share, fixed 20-name test | AC-1, AC-4 |
| `cs2 regimes` | write and print regime labels | AC-5 |
| `cs2 ratio` | Steam→market.csgo ratio per category | AC-6 |
| `cs2 strategies` | list the registry | — |
| `cs2 backtest --strategy S --capital C --from D --to D [--params JSON]` | one run | AC-10 |
| `cs2 report` | walk-forward + holdout + stress + verdicts → HTML | AC-11…13 |
| `cs2 forward --strategy S [--from DT]` | paper run on collected snapshots | AC-14 |

Exit codes: 0 ok; 1 unexpected bug; 2 `ConfigError`; 3 auth (`SteamAuthError`,
`MarketCsgoKeyError`); 4 `SourceUnavailable` / `RateLimited` exhausted; 5 `SchemaError`;
6 data check failed.

## Storage

```
data/                                   # gitignored
  cs2.duckdb                            # tables below + views over the Parquet trees
  collector/
    asks/day=YYYY-MM-DD/<feed_time>-<host>.parquet   # one file per snapshot, zstd
    bids/day=YYYY-MM-DD/<feed_time>-<host>.parquet
    sales/day=YYYY-MM-DD/<fetched_at>-<host>.parquet
  quarantine/<source>/<utc>.json        # raw bodies that failed schema checks
  runs/<run_id>/{trades,equity}.parquet, spec.json
reports/<YYYY-MM-DD>/report.html        # gitignored (derived)
```

DuckDB tables (identity key in **bold**; writes are upserts on it):

| Table | Columns | Key — why |
|---|---|---|
| `steam_points` | name, ts, granularity, price_raw, currency, volume, fx_per_usd, fx_day, price_usd | **(name, ts)** — Steam returns one point per hour/day; re-fetch overwrites |
| `steam_fetch_log` | name, fetched_at, status, n_points, http_status | **name** (latest) — resume = names with status `ok` (AC-2) |
| `fx_rates` | day, base, per_usd, source, filled | **(day, base)** |
| `items` | ItemInfo fields, first_seen (date) | **name** |
| `regimes` | t, index_level, regime | **t** |
| `variants_tried` | strategy, params_json, fold, run_id | **(strategy, params_json, fold)** — n for Sharpe deflation |

Views over Parquet: `asks`, `bids` (dedup on **(feed_time, name)**: GHA and the Mac may record
the same feed state; same `feed_time` = same file content), `sales` (dedup on
**(name, ts, price_raw)** — two real sales with identical second and price collapse into one;
accepted, S-07), `sales_polls` (polled_at, host, name — one row per name per sales poll, so an
item with zero sales isn't re-polled every run; such items wait for the largest tier), `daily_bars` (Steam daily points + hourly tail aggregated to days:
volume-weighted mean price, summed volume).

**Steam USD conversion (S-08).** `price_usd = price_raw / fx_per_usd` where `fx_per_usd` is the
NBU rate on the **fetch day**, the same for the whole item. Not the point's own day.

**Data version.** `data_version = sha256(max(steam_fetch_log.fetched_at), row counts of every
table/view, max feed_time per kind)[:16]`. Same data → same version → same `run_id` (AC-13).

**Remote data store for the GHA collector** (decided by the author 2026-11-09): a separate
private repo `cs2-skin-data`; the workflow pushes Parquet files to it (same tree as
`data/collector/`); `cs2 collect pull` = `git pull` of that repo into `data/collector/`.

## Source response schemas

**Steam** `GET steamcommunity.com/market/pricehistory/?appid=730&market_hash_name=<n>&currency=1`,
header `Cookie: steamLoginSecure=…`:
```json
{"success": true, "price_prefix": "", "price_suffix": "₴",
 "prices": [["Feb 21 2014 01: +0", 1875.962, "198"], ["Nov 09 2026 13: +0", 1549.954, "5"]]}
```
Taken: date-hour (format `"%b %d %Y %H: +0"`, UTC), price → `price_raw`, volume (string → int).
`price_suffix` → currency (`"₴"` → UAH; any other suffix → `SchemaError`, never guessed).
Granularity: hourly when a day has more than one point, else daily. Whole history in one call,
converted at today's rate (S-08). No cookie → 429; expired cookie → 200 with `[]`/`success:false`.

**market.csgo asks** `GET market.csgo.com/api/v2/prices/USD.json` (no key):
`{"success":true,"time":1794232161,"currency":"USD","items":[{"market_hash_name":"…","volume":416,"price":"25.665"}]}`
— `price` is a **string** → float `ask`; `volume` → `listings`; `time` → `feed_time`.

**market.csgo bids** `GET market.csgo.com/api/v2/prices/orders/USD.json` (no key): same envelope,
`price` is a **number** → `bid`; `volume` → `orders`.

**market.csgo sales** `GET market.csgo.com/api/v2/get-list-items-info?key=…&list_hash_name[]=…` (≤ 20 names):
```json
{"success": true, "currency": "RUB",
 "data": {"Clutch Case": {"max": 33.12, "min": 29.36, "average": 31.246,
          "history": [[1794232665, 32.85], [1794232414, 32.34]]}}}
```
Taken: `history` only (unix ts, price in `currency`). `max/min/average` ignored. 401
`{"error":"Bad KEY"}` → `MarketCsgoKeyError`. 500/503 on large batches → back off (S-07).
`bid-ask` is not used by the collector.

**NBU** `GET bank.gov.ua/NBU_Exchange/exchange_site?start=YYYYMMDD&end=YYYYMMDD&valcode=usd&sort=exchangedate&order=asc&json`
→ `[{"exchangedate":"01.01.2014","rate_per_unit":7.993,…}]`, every calendar day.

**CBR** `GET www.cbr.ru/scripts/XML_dynamic.asp?date_req1=dd/mm/yyyy&date_req2=dd/mm/yyyy&VAL_NM_RQ=R01235`
→ XML windows-1251, `<Record Date="dd.mm.yyyy"><Nominal>1</Nominal><Value>82,7536</Value>`;
business days only → forward-filled (`filled=True`); value = `Value / Nominal`, comma decimal.

**ByMykel** `GET raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/all.json` (MIT, 78 MB):
dict of records keyed by `<type>-<id>`; fields used per S-04 (`market_hash_name` or `name`,
`category`, `weapon.name`, `rarity.name`, `stattrak`, `souvenir`; crates: `type`,
`first_sale_date` `"YYYY/MM/DD"`, `contains`; collections: `release_date`, `contains`).
Fetched at most daily; the raw file is cached in `data/cache/` (gitignored) and the compact `items` table is stored.

## Configuration

Source: `cs2.toml` at the repo root (committed, no secrets) < CLI flags. Missing file = defaults.

| Parameter | Type | Default | Meaning / origin |
|---|---|---|---|
| `data_dir` | path | `data` | |
| `reports_dir` | path | `reports` | |
| `steam.interval_s` | float | 2.5 | ≈ 24 req/min, S-01 |
| `steam.max_retries` | int | 3 | per item, on 429/5xx with valid auth |
| `steam.backoff_s` | float | 60 | doubles per retry |
| `steam.stop_after_errors` | int | 10 | consecutive item errors → stop backfill |
| `backfill.universe` | `"feed"` \| path | `feed` | all names in today's asks feed (~27.2k, ≈ 19 h) |
| `mc.interval_s` | float | 2.5 | keyed API; docs: > 5 req/s deletes the key |
| `mc.sales_batch` | int | 20 | 50 → HTTP 500, S-07 |
| `mc.sales_max_names_per_run` | int | 4800 | cap per run: 240 batches × 2.5 s ≈ 10 min; overdue names first, then never-fetched |
| `mc.sales_refill_share` | float | 0.5 | re-poll an item when half its 100-sale window has elapsed |
| `mc.data_repo` | str | `""` | git URL of `cs2-skin-data`, for `cs2 collect pull` |
| `mc.sales_tiers_h` | list[int] | [1, 6, 24] | poll intervals; an item gets the largest one ≤ its due time |
| `universe.min_history_days` | int | 30 | days of Steam history before the item can enter |
| `universe.min_median_volume` | int | 5 | trailing 30-day median daily sales |
| `universe.min_price_usd` | float | 0.10 | |
| `regimes.lookback_days` | int | 30 | index return window |
| `regimes.threshold` | float | 0.05 | > +5% rising, < −5% falling, else flat |
| `ratio.min_days` | int | 14 | AC-6 |
| `ratio.min_items` | int | 20 | per category, else all-items fallback |
| `fills.participation_cap` | float | 0.20 | AC-8 |
| `fills.spread_penalty` | float | 0.03 | placeholder until calibrated from collector asks/bids |
| `fills.ratio_source` | `"measured"` \| `"fixed"` | `fixed` | `measured` once `cs2 ratio` has ≥ 14 days |
| `fills.ratio_fallback` | float | 1.0 | a constant ratio scales buy and sell alike; matters only for affordability |
| `costs.*` | | see `CostModel` | one schedule entry from 2013-01-01: today's rules applied to all history |
| `wf.start` / `wf.end` | date | 2019-01-01 / 2025-09-30 | AC-10 |
| `wf.train_days` / `wf.test_days` | int | 365 / 91 | step = test_days |
| `wf.holdout_start` | date | 2025-10-01 | never used for tuning |
| `verdict.cost_stress` | float | 1.5 | fees × 1.5 (AC-12) |
| `verdict.spread_stress` | float | 2.0 | spread × 2 (AC-12) |
| `capitals` | list[float] | [100, 1000] | judged separately |

## Secrets and access

| Variable | What it is | Where to get it | When it's missing |
|---|---|---|---|
| `STEAM_LOGIN_SECURE` | value of the `steamLoginSecure` cookie of the author's Steam session | browser dev tools on steamcommunity.com, logged in; expires (~daily per community reports) | `cs2 backfill steam` fails at start with `ConfigError`; an expired one → `SteamAuthError` mid-run, progress kept |
| `MARKETCSGO_API_KEY` | market.csgo API key (read-only use) | market.csgo account → API page (needs verified email) | `collect run --kinds sales` fails with `ConfigError`; `asks`/`bids` still run (keyless) |
| GHA secret `MARKETCSGO_API_KEY` | same key for the workflow | repo Settings → Secrets → Actions | the workflow's sales step fails, asks/bids still recorded |
| GHA secret `DATA_REPO_TOKEN` | fine-grained PAT, contents:write on `cs2-skin-data` only | GitHub → Settings → Developer settings | push step fails, run marked failed |

Values are never written here. `.env` is in `.gitignore`; `.env.example` lists the two names
without values. Secrets are read only via `require_secret` and never logged; URLs containing
the key are never logged (they carry `key=`).

## Errors

```python
class Cs2Error(Exception): ...
class ConfigError(Cs2Error): ...           # missing secret, bad cs2.toml value
class SourceError(Cs2Error):
    source: Literal["steam", "mc_public", "mc_keyed", "nbu", "cbr", "items_db"]
class SteamAuthError(SourceError): ...     # 429 without auth, `[]`, success:false
class MarketCsgoKeyError(SourceError): ... # 401 Bad KEY
class RateLimited(SourceError): ...        # 429 with valid auth, 500/503 on keyed API
class SourceUnavailable(SourceError): ...  # network error / 5xx after retries
class SchemaError(SourceError): ...        # unexpected shape or currency
class FxMissing(Cs2Error): ...             # no rate for a needed day
class LookAheadError(Cs2Error): ...        # MarketView asked for data after `now`
class InvariantViolation(Cs2Error): ...    # negative qty, balance < 0 — always a bug
```

| Exception | When | What the caller does |
|---|---|---|
| `ConfigError` | startup | CLI prints the variable/key name, exit 2 |
| `SteamAuthError` | backfill | stop, keep everything stored, print "refresh STEAM_LOGIN_SECURE", exit 3 |
| `MarketCsgoKeyError` | collector sales | skip `sales`, record kind failed, exit 3 after asks/bids are saved |
| `RateLimited` | any source | back off `backoff_s × 2^n`, up to `max_retries`, then `SourceUnavailable` |
| `SourceUnavailable` | backfill item | log item `error`, continue; stop after `stop_after_errors` in a row, exit 4 |
| `SourceUnavailable` | collector kind | other kinds still run; the gap shows in `collect status`; exit 4 |
| `SchemaError` | any | save raw body to `data/quarantine/`, stop that source, exit 5 |
| `FxMissing` | storing prices | refuse to store; run `cs2 fx update` first |
| `LookAheadError` | engine | propagates: a strategy bug; fails AC-9 tests |
| `InvariantViolation` | engine | propagates: an engine bug; fails AC-9 property test |

## Changes this stage forces on earlier artifacts
- **AC-1** (plan.md): "USD converted with that day's official UAH→USD rate, no day lacks a rate"
  → "USD = raw UAH / the NBU rate on the item's fetch day; every item has a rate" (S-08).
- **Cost schedule**: one entry for all history (today's 7-day lock and fees); the brief's
  "date-dependent cost model" keeps the shape (`tuple[CostModel, ...]`) but no historical rules
  are known → draft decision.
