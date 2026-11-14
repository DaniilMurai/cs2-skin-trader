import hashlib
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from cs2trader.backtest.engine import WARMUP_DAYS, load_market
from cs2trader.config import Settings, load_settings
from cs2trader.evaluate import (
    WalkForwardResult,
    holdout_spec,
    make_spec,
    metrics,
    walk_forward,
)
from cs2trader.forward import CHOICES
from cs2trader.market.index import market_index
from cs2trader.market.regimes import label_regimes
from cs2trader.models import Metrics, RunResult, Verdict
from cs2trader.parallel import LocalRunner, PoolRunner
from cs2trader.store import Store
from cs2trader.strategies.registry import REGISTRY

KEY = ["strategy", "capital", "period", "regime", "unit"]
FOLD_PREFIX = "wf_fold_"
REGIME_ORDER = ["all", "rising", "falling", "flat"]
PERIODS = ["walk_forward", "holdout", "holdout_stress"]
HASH_DECIMALS = 9
REQUIRED = ("cash", "bh_universe")
BASKETS = 15
MEDIAN_NAME = "bh_median"
SCHEMA = {"strategy": pl.String, "capital": pl.Float64, "period": pl.String, "regime": pl.String,
          "unit": pl.String, "net_return": pl.Float64, "max_drawdown": pl.Float64,
          "n_trades": pl.Int64, "fees_paid": pl.Float64, "sharpe": pl.Float64}


@dataclass(frozen=True)
class Evaluation:
    results: pl.DataFrame
    verdicts: list[Verdict]
    walk_forward: list[dict] = field(default_factory=list)   # walk-forward-only: params and variant counts


def _rows(strategy: str, capital: float, period: str, regime: str, m: Metrics) -> list[dict]:
    base = {"strategy": strategy, "capital": capital, "period": period, "regime": regime,
            "n_trades": m.n_trades, "fees_paid": m.fees_paid}
    # Metrics carries a Sharpe for cash only.
    return [
        {**base, "unit": "cash", "net_return": m.net_return_cash, "max_drawdown": m.max_drawdown_cash,
         "sharpe": m.sharpe_cash},
        {**base, "unit": "steam", "net_return": m.net_return_steam, "max_drawdown": m.max_drawdown_steam,
         "sharpe": None},
    ]


def _period_rows(strategy: str, capital: float, period: str, res: RunResult,
                 regimes: pl.DataFrame) -> list[dict]:
    out = _rows(strategy, capital, period, "all", metrics(res))
    per_regime = metrics(res, regimes)
    for regime in REGIME_ORDER[1:]:
        if regime in per_regime:
            out += _rows(strategy, capital, period, regime, per_regime[regime])
    return out


def _chain_folds(runs: list[RunResult], capital: float) -> RunResult:
    """Walk-forward as one run: every fold starts with the full capital, so fold k's equity is
    rescaled to continue from where fold k-1 ended (fold returns are compounded). Each unit is
    rescaled by its own factor against the fold's first bar. The first bar is struck before any
    trade, so it already carries the withdrawal haircut: scaling by the first bar, not by the
    capital, applies that fee once to the whole path instead of once per fold. Fees stay in
    dollars of their own fold and are accumulated without rescaling."""
    eq_parts, fees = [], 0.0
    level_cash, level_steam = runs[0].equity["equity_cash"][0], runs[0].equity["equity_steam"][0]
    for r in runs:
        k_cash = level_cash / r.equity["equity_cash"][0]
        k_steam = level_steam / r.equity["equity_steam"][0]
        eq = r.equity.with_columns(pl.col("equity_cash") * k_cash, pl.col("equity_steam") * k_steam,
                                   pl.col("fees_paid") + fees)
        eq_parts.append(eq)
        level_cash, level_steam, fees = eq["equity_cash"][-1], eq["equity_steam"][-1], eq["fees_paid"][-1]
    trades = pl.concat([r.trades for r in runs if r.trades.height], how="diagonal_relaxed") \
        if any(r.trades.height for r in runs) else runs[0].trades
    return RunResult("walk_forward", runs[0].spec, runs[0].data_version, trades,
                     pl.concat(eq_parts, how="vertical_relaxed"), None)


def _regime_labels(store: Store, settings: Settings, end) -> pl.DataFrame:
    db = getattr(store, "db", None)
    if db is not None:
        stored = db.sql("SELECT t, regime FROM regimes").pl()
        if not stored.is_empty():
            return stored.filter(pl.col("t") <= end)
    bars = store.daily_bars(settings.wf.start - timedelta(days=WARMUP_DAYS), end)
    labels = label_regimes(market_index(bars, store.items_frame(), "all", settings.universe), settings.regimes)
    return labels.select("t", "regime")


def evaluate_all(store: Store, settings: Settings, strategies: list[str] | None = None,
                 jobs: int = 1, progress: bool = False, walk_forward_only: bool = False) -> Evaluation:
    """`jobs` > 1 runs the backtests in that many worker processes; the results are identical to
    `jobs=1` (each tuning window is independent), only the wall time changes.
    `walk_forward_only` stops the day before the holdout: no bar or regime label from
    `wf.holdout_start` on is loaded, there are no holdout rows and no verdicts (AC-21), and
    `bh_median` is the basket with the median chained walk-forward return instead."""
    names = list(REGISTRY) if strategies is None else strategies
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(f"unknown strategies: {unknown}")
    # Benchmarks first: the verdict of every other strategy needs their holdout runs.
    names = [*REQUIRED, *[n for n in names if n not in REQUIRED]]
    wf_s = settings.wf
    end = wf_s.holdout_start - timedelta(days=1) if walk_forward_only else store.last_day()
    data = load_market(store, wf_s.start, end, settings)
    regimes = _regime_labels(store, settings, end)
    combos = [(capital, name) for capital in settings.capitals for name in names]
    started, done = time.monotonic(), []

    def _tick(name: str, capital: float) -> None:
        done.append(name)
        if progress:
            print(f"[{len(done)}/{len(combos)}] {name} ${capital:,.0f} done, "
                  f"{(time.monotonic() - started) / 60:.1f} min elapsed", file=sys.stderr, flush=True)

    def one(runner, capital: float, name: str):
        entry = REGISTRY[name]
        wf = walk_forward(entry, capital, store, wf_s, settings, runner=runner)
        if walk_forward_only:
            seeds = range(1, BASKETS) if name == "bh_universe" else ()
            extra = [[runner.submit_run(make_spec(name, {"seed": k}, capital, te0, te1, settings, store=store))
                      for te0, te1, _, _ in wf.folds] for k in seeds]
            out = wf, None, None, [[f.result() for f in basket] for basket in extra]
            _tick(name, capital)
            return out
        ho = runner.submit_run(holdout_spec(entry, wf.chosen_params, capital, wf_s, end, False, settings, store))
        ho_s = runner.submit_run(holdout_spec(entry, wf.chosen_params, capital, wf_s, end, True, settings, store))
        extra = []
        if name == "bh_universe":
            extra = [runner.submit_run(make_spec(name, {"seed": k}, capital, wf_s.holdout_start, end, settings, store=store))
                     for k in range(1, BASKETS)]
        out = wf, ho.result(), ho_s.result(), [f.result() for f in extra]
        _tick(name, capital)
        return out

    if jobs > 1:
        with PoolRunner(store, settings, data, jobs) as runner, ThreadPoolExecutor(len(combos)) as threads:
            del data
            results = list(threads.map(lambda c: one(runner, *c), combos))
    else:
        runner = LocalRunner(store, settings, data)
        results = [one(runner, *c) for c in combos]

    rows: list[dict] = []
    verdicts: list[Verdict] = []
    chosen: list[dict] = []
    ho_runs: dict[float, dict[str, RunResult]] = {c: {} for c in settings.capitals}
    for (capital, name), (wf, ho, ho_s, baskets) in zip(combos, results):
        entry = REGISTRY[name]
        for i, res in enumerate(wf.test_runs):
            rows += _rows(name, capital, f"{FOLD_PREFIX}{i:02d}", "all", metrics(res))
        if wf.test_runs:
            rows += _period_rows(name, capital, "walk_forward", _chain_folds(list(wf.test_runs), capital), regimes)
        if walk_forward_only:
            if baskets:
                chained = [_chain_folds(runs, capital) for runs in ([*wf.test_runs], *baskets)]
                ranked = sorted(enumerate(chained), key=lambda kv: (kv[1].equity["equity_cash"][-1], kv[0]))
                rows += _period_rows(MEDIAN_NAME, capital, "walk_forward", ranked[len(ranked) // 2][1], regimes)
            chosen.append({"strategy": name, "capital": capital, "chosen_params": dict(wf.chosen_params),
                           "n_variants": wf.n_variants,
                           "fold_params": [dict(params) for _, _, params, _ in wf.folds]})
            continue
        rows += _period_rows(name, capital, "holdout", ho, regimes)
        rows += _period_rows(name, capital, "holdout_stress", ho_s, regimes)
        ho_runs[capital][name] = ho
        if baskets:
            # One random 25-item basket is a lottery ticket (T-49): the benchmark is the basket with the
            # median holdout return of BASKETS baskets; ties go to the lower seed.
            ranked = sorted(enumerate([ho, *baskets]), key=lambda kv: (metrics(kv[1]).net_return_cash, kv[0]))
            median = ranked[len(ranked) // 2][1]
            rows += _period_rows(MEDIAN_NAME, capital, "holdout", median, regimes)
            ho_runs[capital][MEDIAN_NAME] = median
        if entry.family != "benchmark":
            verdicts.append(_verdict(name, capital, wf, ho, ho_s, ho_runs[capital]))
    return Evaluation(pl.DataFrame(rows, schema=SCHEMA).sort(KEY), verdicts, chosen)


def _verdict(name: str, capital: float, wf: WalkForwardResult, ho: RunResult, ho_s: RunResult,
             ho_runs: dict[str, RunResult]) -> Verdict:
    from cs2trader.evaluate import verdict

    return verdict(name, capital, wf, ho, ho_s, ho_runs[MEDIAN_NAME], ho_runs["cash"])


def results_table(store: Store, settings: Settings | None = None,
                  strategies: list[str] | None = None, jobs: int = 1) -> pl.DataFrame:
    return evaluate_all(store, settings or load_settings(), strategies, jobs).results


def results_hash(results: pl.DataFrame) -> str:
    df = results.sort(KEY).with_columns(pl.col(pl.Float64).round(HASH_DECIMALS))
    return hashlib.sha256(df.write_csv().encode()).hexdigest()[:16]


def _money(c: float) -> str:
    return f"${c:,.0f}"


def summary_line(verdicts: list[Verdict], capitals: list[float]) -> str:
    parts = []
    for c in capitals:
        holds = [v.strategy for v in verdicts if v.capital == c and v.status == "holds_up"]
        parts.append((c, holds))
    if not any(h for _, h in parts):
        return f"No strategy holds up at {' / '.join(_money(c) for c in capitals)}"
    return "; ".join(f"{', '.join(h)} holds up at {_money(c)}" if h else f"no strategy holds up at {_money(c)}"
                     for c, h in parts).capitalize()


def _md(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(p))}</p>" for p in paras)


def _fmt(v, kind: str) -> str:
    if v is None:
        return "-"
    if kind == "pct":
        return f"{v:+.2%}"
    if kind == "dd":
        return f"{v:.2%}"
    if kind == "int":
        return str(int(v))
    return f"{v:.2f}"


def _table(header: list[str], body: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in header)
    trs = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><tr>{th}</tr>{trs}</table>"


def _results_tables(results: pl.DataFrame, capitals: list[float]) -> str:
    out = []
    header = ["strategy", "period", "regime", "net return cash", "net return steam", "max DD cash",
              "max DD steam", "trades", "fees paid", "Sharpe (cash)"]
    for c in capitals:
        body = []
        sub = results.filter((pl.col("capital") == c) & pl.col("period").is_in(PERIODS))
        wide = sub.filter(pl.col("unit") == "cash").join(
            sub.filter(pl.col("unit") == "steam").select(
                "strategy", "period", "regime", pl.col("net_return").alias("nr_s"),
                pl.col("max_drawdown").alias("dd_s")), on=["strategy", "period", "regime"])
        order = {n: i for i, n in enumerate(REGISTRY)}
        rows = sorted(wide.iter_rows(named=True), key=lambda r: (
            order.get(r["strategy"], 99), PERIODS.index(r["period"]), REGIME_ORDER.index(r["regime"])))
        for r in rows:
            body.append([r["strategy"], r["period"], r["regime"], _fmt(r["net_return"], "pct"),
                         _fmt(r["nr_s"], "pct"), _fmt(r["max_drawdown"], "dd"), _fmt(r["dd_s"], "dd"),
                         _fmt(r["n_trades"], "int"), _fmt(r["fees_paid"], "usd"), _fmt(r["sharpe"], "num")])
        out.append(f"<h3>Results at {_money(c)}</h3>" + _table(header, body))
    return "".join(out)


def _sensitivity(results: pl.DataFrame, capitals: list[float], settings: Settings) -> str:
    v = settings.verdict
    out = [(f"<p>Cost / spread sensitivity: holdout with normal costs vs stressed (costs x{v.cost_stress}, "
            f"spread penalty x{v.spread_stress}); net return, cash-out unit.</p>")]
    for c in capitals:
        sub = results.filter((pl.col("capital") == c) & (pl.col("regime") == "all")
                             & (pl.col("unit") == "cash"))
        body = []
        for name in REGISTRY:
            n = sub.filter((pl.col("strategy") == name) & (pl.col("period") == "holdout"))
            s = sub.filter((pl.col("strategy") == name) & (pl.col("period") == "holdout_stress"))
            if n.height and s.height:
                body.append([name, _fmt(n["net_return"][0], "pct"), _fmt(s["net_return"][0], "pct")])
        out.append(f"<h3>{_money(c)}</h3>" + _table(["strategy", "holdout", "holdout stressed"], body))
    return "".join(out)


def _assumptions(store: Store, settings: Settings, end) -> str:
    """`end` None: the report was built without the holdout."""
    f, c = settings.fills, settings.costs
    if f.ratio_source == "fixed":
        ratio = f"fixed, {f.ratio_fallback} for every category (fills.ratio_fallback)"
    else:
        ratio = (f"measured per category from table steam_to_market_ratio (mid of ask and bid medians), "
                 f"{f.ratio_fallback} (fills.ratio_fallback) for categories without a row")
    items = [
        f"Steam wallet to market.csgo price ratio: {ratio}",
        f"Spread penalty: {f.spread_penalty:.2%} per fill; participation cap: {f.participation_cap:.0%} of daily volume",
        (f"Costs: seller fee {c.seller_fee:.2%}, buyer fee {c.buyer_fee:.2%}, cash-out withdraw fee "
         f"{c.withdraw_fee_cash:.2%}, Steam-wallet withdraw fee {c.withdraw_fee_steam:.2%}, "
         f"trade lock {c.lock_days} d, cash-out hold {c.cashout_hold_days} d"),
        f"Stress: costs x{settings.verdict.cost_stress}, spread penalty x{settings.verdict.spread_stress}",
        (f"Walk-forward: {settings.wf.start} .. {settings.wf.end}, train {settings.wf.train_days} d, "
         f"test {settings.wf.test_days} d; walk_forward rows chain the fold test runs "
         "(each fold starts with the full capital, fold returns are compounded)"),
        f"Holdout: {settings.wf.holdout_start} .. {end}" if end else "Holdout: not run (walk-forward only)",
        f"Data version: {store.data_version()}",
    ]
    return "<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>"


def _forward_rows(settings: Settings) -> pl.DataFrame | None:
    path = settings.data_dir / "forward" / "results.parquet"
    return pl.read_parquet(path) if path.exists() else None


def forward_section(rows: pl.DataFrame | None) -> str:
    """The forward paper test next to the daily backtest of the same strategy, params and dates.
    Rendered from `cs2 forward`'s summary; it is not part of the results table or its hash."""
    if rows is None or rows.is_empty():
        return ""
    header = ["strategy", "capital", "dates (UTC)", "params", "backtest", "forward", "backtest max DD",
              "forward max DD", "backtest trades", "forward trades", "forward fees paid"]
    body = [[r["strategy"], _money(r["capital"]), f"{r['start'][:13]}h .. {r['end'][:13]}h", r["params_json"],
             _fmt(r["bt_net_return_cash"], "pct"), _fmt(r["net_return_cash"], "pct"),
             _fmt(r["bt_max_drawdown_cash"], "dd"), _fmt(r["max_drawdown_cash"], "dd"),
             _fmt(r["bt_n_trades"], "int"), _fmt(r["n_trades"], "int"), _fmt(r["fees_paid"], "usd")]
            for r in rows.sort("strategy", "capital").iter_rows(named=True)]
    notes = "".join(f"<li>{html.escape(c)}</li>" for c in CHOICES)
    return ("<h2>Forward paper test</h2><p>Net return, cash-out unit. backtest = the daily engine on Steam bars, "
            "forward = hourly on market.csgo snapshots, same strategy, parameters and dates.</p>"
            + _table(header, body) + f"<ul>{notes}</ul>")


def build_report(store: Store, out_dir: Path, settings: Settings | None = None,
                 strategies: list[str] | None = None, jobs: int = 1, progress: bool = False,
                 walk_forward_only: bool = False) -> Path:
    """`walk_forward_only`: reports/<YYYY-MM-DD>-walk-forward/, so the day's full report is not
    overwritten; `walk_forward.json` there holds the chosen params and the variant counts."""
    settings = settings or load_settings()
    ev = evaluate_all(store, settings, strategies, jobs, progress, walk_forward_only)
    digest = results_hash(ev.results)
    caps = settings.capitals
    vrows = [[v.strategy, _money(v.capital), v.status, v.first_broken_rule or "-",
              _fmt(v.deflated_sharpe, "num"), str(v.n_variants)] for v in ev.verdicts]
    shown = [n for n in REGISTRY if strategies is None or n in strategies or n in REQUIRED]
    appendix = "".join(f"<h3>{html.escape(n)}</h3>{_md(REGISTRY[n].spec_md)}" for n in shown)
    summary = ("Walk-forward only: the holdout was not run, no verdicts" if walk_forward_only
               else summary_line(ev.verdicts, caps))
    body = (
        f"<p>Results hash: <code>{digest}</code></p>"
        f"<h2>Verdicts</h2>"
        + _table(["strategy", "capital", "status", "first broken rule", "deflated Sharpe", "n_variants"], vrows)
        + "<h2>Results</h2>" + _results_tables(ev.results, caps)
        + forward_section(_forward_rows(settings))
        + "<h2>Sensitivity</h2>" + _sensitivity(ev.results, caps, settings)
        + "<h2>Assumptions</h2>" + _assumptions(store, settings, None if walk_forward_only else store.last_day())
        + "<h2>Appendix: strategy specs</h2>" + appendix
    )
    css = ("body{font-family:sans-serif;max-width:1100px;margin:2em auto;padding:0 1em}"
           "table{border-collapse:collapse;margin:1em 0;font-size:13px}"
           "td,th{border:1px solid #ccc;padding:2px 8px;text-align:right}"
           "td:first-child,th:first-child{text-align:left}")
    # The verdict summary must be the file's first line.
    page = (f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(summary)}</title>"
            f"<style>{css}</style></head><body><h1>{html.escape(summary)}</h1>\n{body}\n</body></html>\n")
    day = datetime.now(UTC).date()
    out = Path(out_dir) / (day.isoformat() + ("-walk-forward" if walk_forward_only else ""))
    out.mkdir(parents=True, exist_ok=True)
    ev.results.write_parquet(out / "results.parquet")
    if walk_forward_only:
        (out / "walk_forward.json").write_text(json.dumps(ev.walk_forward, indent=2, sort_keys=True) + "\n")
    # The UI reads the verdicts from here; the HTML stays the human-readable copy.
    (out / "verdicts.json").write_text(json.dumps({
        "summary": summary, "results_hash": digest, "capitals": caps,
        "verdicts": [asdict(v) for v in ev.verdicts]}, indent=2, sort_keys=True) + "\n")
    (out / "report.html").write_text(page)
    return out / "report.html"
