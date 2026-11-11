import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from cs2trader.backfill import backfill, feed_universe, read_names, request_logger
from cs2trader.backtest.engine import run
from cs2trader.backtest.runs import save_run
from cs2trader.check import format_results, raise_if_failed, run_checks
from cs2trader.collect import (
    collect_status,
    format_status,
    pull_remote,
    run_collect,
    worst_error,
)
from cs2trader.config import load_settings, require_secret
from cs2trader.errors import ConfigError, Cs2Error, SteamAuthError
from cs2trader.items import update_items
from cs2trader.market.index import market_index
from cs2trader.market.regimes import label_regimes
from cs2trader.models import RunSpec
from cs2trader.sources import fx, steam
from cs2trader.store import Store
from cs2trader.strategies.registry import REGISTRY

FX_START = date(2013, 1, 1)
FX_REFETCH_DAYS = 7


def cmd_fx_update(args: argparse.Namespace) -> None:
    today = datetime.now(UTC).date()
    store = Store(load_settings().data_dir)
    for base in ("UAH", "RUB"):
        first, last = store.db.execute("SELECT min(day), max(day) FROM fx_rates WHERE base = ?", [base]).fetchone()
        start = FX_START if first is None or first > FX_START else max(FX_START, last - timedelta(days=FX_REFETCH_DAYS))
        n = store.upsert_fx(fx.fetch_rates(base, start, today))
        print(f"fx: {base} {start}..{today}: {n} rates stored")


def cmd_backfill_steam(args: argparse.Namespace) -> None:
    settings = load_settings()
    store = Store(settings.data_dir)
    universe = args.names or settings.backfill.universe
    names = feed_universe(store) if universe == "feed" else read_names(Path(universe))
    client = steam.SteamClient(
        require_secret("STEAM_LOGIN_SECURE"), interval_s=settings.steam.interval_s,
        max_retries=settings.steam.max_retries, backoff_s=settings.steam.backoff_s,
        on_request=request_logger(settings.data_dir / "logs" / "steam_requests.tsv"))
    try:
        rep = backfill(store, client, names, lambda: store.fx(datetime.now(UTC).date(), "UAH"),
                       refresh=args.refresh, limit=args.limit,
                       stop_after_errors=settings.steam.stop_after_errors)
    except SteamAuthError:
        print("refresh STEAM_LOGIN_SECURE in .env and re-run; stored items are kept", file=sys.stderr)
        raise
    print(f"backfill: {rep.ok} ok, {rep.empty} empty, {rep.errors} errors, {rep.skipped} already stored")


def parse_params(strategy: str, text: str) -> dict:
    if strategy not in REGISTRY:
        raise ConfigError(f"unknown strategy {strategy!r}; known: {', '.join(sorted(REGISTRY))}")
    try:
        params = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"--params is not valid JSON: {e}") from e
    if not isinstance(params, dict):
        raise ConfigError("--params must be a JSON object")
    grid = REGISTRY[strategy].grid
    if unknown := sorted(set(params) - set(grid)):
        raise ConfigError(f"--params: unknown key(s) {unknown} for {strategy}; "
                          f"allowed: {sorted(grid) or 'none (grid is empty)'}")
    return params


def cmd_strategies(args: argparse.Namespace) -> None:
    for name, e in REGISTRY.items():
        grid = ", ".join(f"{k}: {list(v)}" for k, v in e.grid.items()) or "-"
        print(f"{name}  {e.family}  {grid}")


def cmd_backtest(args: argparse.Namespace) -> None:
    settings = load_settings()
    spec = RunSpec(args.strategy, parse_params(args.strategy, args.params), args.capital,
                   date.fromisoformat(args.date_from), date.fromisoformat(args.date_to))
    result = run(spec, Store(settings.data_dir), settings)
    out = save_run(result, settings.data_dir / "runs")
    eq = result.equity
    print(f"run {result.run_id}  data {result.data_version}  bars {eq.height}")
    print(result.trades.select("t", "market_hash_name", "side", "qty_requested", "qty_filled",
                               "price", "fee", "reason"))
    first, last = eq.row(0, named=True), eq.row(-1, named=True)
    for unit in ("cash", "steam"):
        col = f"equity_{unit}"
        print(f"{unit:>5}: {spec.capital:.2f} → {last[col]:.2f} "
              f"({last[col] / spec.capital - 1:+.1%}); first bar {first[col]:.2f}")
    print(f"fees paid {last['fees_paid']:.2f}; out: {out}")


def cmd_items_update(args: argparse.Namespace) -> None:
    settings = load_settings()
    rep = update_items(Store(settings.data_dir), None, settings.data_dir / "cache")
    counts = ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items(), key=lambda kv: -kv[1]))
    print(f"items: {rep['n']} classified; unknown {rep['unknown']} ({rep['unknown_share']:.2%})")
    print(f"  {counts}")
    for name in rep["unknown_names"]:
        print(f"  unknown: {name}")


def cmd_regimes(args: argparse.Namespace) -> None:
    settings = load_settings()
    store = Store(settings.data_dir)
    bars = store.daily_bars(date(2013, 1, 1), datetime.now(UTC).date())
    labels = label_regimes(market_index(bars, store.items_frame(), "all", settings.universe), settings.regimes)
    store.write_regimes(labels)
    if labels.is_empty():
        print("regimes: no index days (too few items with history)")
        return
    runs = labels.with_columns((pl.col("regime") != pl.col("regime").shift(1)).cum_sum().alias("run")).group_by(
        "run", maintain_order=True).agg(pl.col("t").first().alias("start"), pl.col("t").last().alias("end"),
                                        pl.col("regime").first())
    print(f"regimes: {labels.height} days {labels['t'].min()}..{labels['t'].max()}, "
          + ", ".join(f"{r} {n}" for r, n in labels.group_by("regime").len().sort("regime").iter_rows()))
    for row in runs.tail(args.last).iter_rows(named=True):
        print(f"  {row['start']} → {row['end']}  {row['regime']}")


def cmd_report(args: argparse.Namespace) -> None:
    from cs2trader.report import build_report

    settings = load_settings()
    names = [n.strip() for n in args.strategies.split(",") if n.strip()] if args.strategies else None
    path = build_report(Store(settings.data_dir), settings.reports_dir, settings, names, args.jobs, progress=True)
    first = path.read_text().split("\n", 1)[0]
    digest = re.search(r"Results hash: <code>(\w+)</code>", path.read_text()).group(1)
    print(f"report: {path}\nresults hash: {digest}\n{re.search('<h1>(.*?)</h1>', first).group(1)}")


def cmd_data_check(args: argparse.Namespace) -> None:
    settings = load_settings()
    store = Store(settings.data_dir)
    universe = settings.backfill.universe
    names = feed_universe(store) if universe == "feed" else read_names(Path(universe))
    results = run_checks(store, names, settings.data_dir / "logs" / "steam_requests.tsv", args.categories)
    print(format_results(results))
    raise_if_failed(results)


def cmd_collect_status(args: argparse.Namespace) -> None:
    print(format_status(collect_status(Store(load_settings().data_dir), args.hours)))


def cmd_collect_pull(args: argparse.Namespace) -> None:
    settings = load_settings()
    print(f"pull: {pull_remote(Store(settings.data_dir), settings.mc.data_repo)} files added")


def cmd_collect_run(args: argparse.Namespace) -> None:
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    bad = sorted(set(kinds) - {"asks", "bids", "sales"})
    if bad or not kinds:
        raise ConfigError(f"--kinds: unknown {bad or 'empty list'}; use asks,bids,sales")
    overrides = {"mc.sales_max_names_per_run": args.sales_max} if args.sales_max is not None else {}
    settings = load_settings(**overrides)
    _report, failed, rows = run_collect(Store(settings.data_dir), kinds, args.host, datetime.now(UTC), settings)
    for kind in kinds:
        if kind in failed:
            saved = f", {rows_saved} rows saved" if (rows_saved := getattr(failed[kind], "rows_saved", 0)) else ""
            print(f"{kind}: failed ({type(failed[kind]).__name__}: {failed[kind]}){saved}")
        else:
            print(f"{kind}: ok, {rows[kind]} rows")
    worst = worst_error(list(failed.values()))
    if worst is not None:
        sys.exit(worst.exit_code)


def main() -> None:
    p = argparse.ArgumentParser(prog="cs2")
    sub = p.add_subparsers(required=True)

    fx_p = sub.add_parser("fx").add_subparsers(required=True)
    fx_p.add_parser("update").set_defaults(func=cmd_fx_update)

    bf = sub.add_parser("backfill").add_subparsers(required=True)
    st = bf.add_parser("steam")
    st.add_argument("--names", help="file with one market_hash_name per line; default: backfill.universe")
    st.add_argument("--limit", type=int)
    st.add_argument("--refresh", action="store_true")
    st.set_defaults(func=cmd_backfill_steam)

    it = sub.add_parser("items").add_subparsers(required=True)
    it.add_parser("update").set_defaults(func=cmd_items_update)

    dc = sub.add_parser("data").add_subparsers(required=True)
    chk = dc.add_parser("check")
    chk.add_argument("--categories", action="store_true")
    chk.set_defaults(func=cmd_data_check)

    rg = sub.add_parser("regimes")
    rg.add_argument("--last", type=int, default=15, help="print the last N regime runs")
    rg.set_defaults(func=cmd_regimes)

    co = sub.add_parser("collect").add_subparsers(required=True)
    cr = co.add_parser("run")
    cr.add_argument("--kinds", default="asks,bids,sales")
    cr.add_argument("--host", choices=["gha", "mac"], required=True)
    cr.add_argument("--sales-max", type=int, help="override mc.sales_max_names_per_run")
    cr.set_defaults(func=cmd_collect_run)
    co.add_parser("pull").set_defaults(func=cmd_collect_pull)
    cs = co.add_parser("status")
    cs.add_argument("--hours", type=int, default=48)
    cs.set_defaults(func=cmd_collect_status)

    bt = sub.add_parser("backtest")
    bt.add_argument("--strategy", required=True)
    bt.add_argument("--capital", type=float, required=True)
    bt.add_argument("--from", dest="date_from", required=True)
    bt.add_argument("--to", dest="date_to", required=True)
    bt.add_argument("--params", default="{}")
    bt.set_defaults(func=cmd_backtest)

    rp = sub.add_parser("report")
    rp.add_argument("--strategies", help="comma-separated subset; cash and bh_universe are always included")
    rp.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2),
                    help="worker processes (1 = in this process); results are identical either way")
    rp.set_defaults(func=cmd_report)

    sub.add_parser("strategies").set_defaults(func=cmd_strategies)

    args = p.parse_args()
    try:
        args.func(args)
    except Cs2Error as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
