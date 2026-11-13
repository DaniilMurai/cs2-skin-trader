import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from cs2trader.arbitrage import (
    ArbParams,
    format_assumptions,
    format_edges,
    format_result,
    hourly_edges,
    simulate,
    steam_bars,
)
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
from cs2trader.config import load_settings, optional_secret, require_secret
from cs2trader.errors import ConfigError, Cs2Error, DataCheckFailed, SteamAuthError
from cs2trader.evaluate import make_spec
from cs2trader.forward import (
    CHOICES,
    MIN_DAYS,
    forward,
    save_rows,
    snapshot_span,
    summary_row,
)
from cs2trader.items import update_items
from cs2trader.market.index import market_index
from cs2trader.market.ratio import format_ratio, measured_spread, steam_to_market_ratio
from cs2trader.market.regimes import label_regimes
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
        on_request=request_logger(settings.data_dir / "logs" / "steam_requests.tsv"),
        quarantine_dir=settings.data_dir / "quarantine" / "steam")
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
    params = parse_params(args.strategy, args.params)
    # The store is opened before the spec: fills.ratio_source = "measured" reads its ratio table
    store = Store(settings.data_dir)
    spec = make_spec(args.strategy, params, args.capital, date.fromisoformat(args.date_from),
                     date.fromisoformat(args.date_to), settings, store=store)
    result = run(spec, store, settings)
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
    years = labels.height / 365
    print(f"  {runs.height / years:.1f} label changes per year (a 30-day lookback on 2019-2025 data gave 22.7)")
    for row in runs.tail(args.last).iter_rows(named=True):
        print(f"  {row['start']} → {row['end']}  {row['regime']}")


def cmd_ratio(args: argparse.Namespace) -> None:
    settings = load_settings()
    store = Store(settings.data_dir)
    ratio = steam_to_market_ratio(store, settings.ratio)
    print(format_ratio(ratio, measured_spread(store, settings.ratio)))
    print(f"stored {store.write_ratio(ratio)} rows in steam_to_market_ratio")


def cmd_arbitrage(args: argparse.Namespace) -> None:
    settings = load_settings()
    try:
        capitals = [float(c) for c in args.capital.split(",")] if args.capital else settings.capitals
    except ValueError as e:
        raise ConfigError(f"--capital must be comma-separated numbers: {e}") from e
    p = ArbParams(min_days=args.min_days, min_edge=args.min_edge, volume_share=args.volume_share)
    store = Store(settings.data_dir)
    first = store.db.execute("SELECT min(CAST(feed_time AS DATE)) FROM asks").fetchall()[0][0]
    bars = steam_bars(store, first) if first else None
    edges = hourly_edges(store, p, bars)
    out = settings.data_dir / "arbitrage"
    out.mkdir(parents=True, exist_ok=True)
    edges.write_parquet(out / "edges.parquet", compression="zstd")
    print(format_assumptions(p))
    print(format_edges(edges, p))
    for capital in capitals:
        result = simulate(edges, bars, capital, p)
        result.lots.write_parquet(out / f"lots_{capital:g}.parquet", compression="zstd")
        print(format_result(result, p))
    print(f"hourly edge log and bought units: {out}")


def parse_hour(text: str) -> datetime:
    try:
        t = datetime.fromisoformat(text)
    except ValueError as e:
        raise ConfigError(f"--from must be an ISO date or date-time (UTC), got {text!r}") from e
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def cmd_forward(args: argparse.Namespace) -> None:
    settings = load_settings()
    params = parse_params(args.strategy, args.params)
    try:
        capitals = [float(c) for c in args.capital.split(",")] if args.capital else settings.capitals
    except ValueError as e:
        raise ConfigError(f"--capital must be comma-separated numbers: {e}") from e
    store = Store(settings.data_dir)
    _, first, last = snapshot_span(store)
    if first is None:
        raise DataCheckFailed(f"forward needs at least {args.min_days} days of hourly market.csgo snapshots, "
                              "found 0; keep the collector running")
    start = parse_hour(args.date_from) if args.date_from else first.replace(minute=0, second=0, microsecond=0)
    end = last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    rows = []
    for capital in capitals:
        fwd = forward(REGISTRY[args.strategy], params, capital, store, start, end, settings, args.min_days)
        out = save_run(fwd, settings.data_dir / "runs")
        try:
            # The same strategy and dates on Steam daily bars: the report's "backtest" column
            bt = run(make_spec(args.strategy, params, capital, start.date(), end.date(), settings, store=store),
                     store, settings)
        except Cs2Error as e:
            bt = None
            print(f"backtest for the same dates skipped: {e}")
        rows.append(row := summary_row(fwd, bt, start, end))
        print(f"forward {fwd.run_id}  ${capital:,.0f}  {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC  "
              f"hours {fwd.equity.height}  trades {row['n_trades']}  fees {row['fees_paid']:.2f}")
        bt_text = "-" if bt is None else f"{row['bt_net_return_cash']:+.2%}"
        print(f"  net return cash: forward {row['net_return_cash']:+.2%}, backtest {bt_text}; out: {out}")
    save_rows(settings.data_dir / "forward" / "results.parquet", rows)
    print("choices:")
    for line in CHOICES:
        print(f"  {line}")


def cmd_ui(args: argparse.Namespace) -> None:
    from cs2trader.ui.server import serve

    serve(args.port)


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
    print(format_status(collect_status(Store(load_settings().data_dir, in_memory=True), args.hours)))


def cmd_collect_pull(args: argparse.Namespace) -> None:
    settings = load_settings()
    store = Store(settings.data_dir, in_memory=True)
    if not args.cf_only:
        print(f"pull: {pull_remote(store, settings.mc.data_repo)} files added")
    url, token = optional_secret("CF_WORKER_URL"), optional_secret("CF_WORKER_TOKEN")
    if url and token:
        from cs2trader.sources.cloudflare import CfBuffer, import_raw
        rep = import_raw(store, CfBuffer(url, token), settings.data_dir / "quarantine" / "cf")
        print(f"cloudflare: {rep.imported} files imported, {rep.bad} unparseable (quarantined), {rep.deleted} removed from R2")


def cmd_collect_run(args: argparse.Namespace) -> None:
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    bad = sorted(set(kinds) - {"asks", "bids", "sales", "skinport", "waxpeer"})
    if bad or not kinds:
        raise ConfigError(f"--kinds: unknown {bad or 'empty list'}; use asks,bids,sales,skinport,waxpeer")
    overrides = {"mc.sales_max_names_per_run": args.sales_max} if args.sales_max is not None else {}
    settings = load_settings(**overrides)
    _report, failed, rows = run_collect(Store(settings.data_dir, in_memory=True), kinds, args.host, datetime.now(UTC), settings)
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

    sub.add_parser("ratio", help="Steam to market.csgo price ratio and spread per category").set_defaults(func=cmd_ratio)

    ar = sub.add_parser("arbitrage", help="paper test: buy on market.csgo, sell on Steam after the trade lock")
    ar.add_argument("--capital", help="comma-separated; default: capitals from cs2.toml")
    ar.add_argument("--min-days", type=int, default=ArbParams.min_days, help="days of ask snapshots required")
    ar.add_argument("--min-edge", type=float, default=ArbParams.min_edge)
    ar.add_argument("--volume-share", type=float, default=ArbParams.volume_share,
                    help="share of an item's Steam daily volume one may take")
    ar.set_defaults(func=cmd_arbitrage)

    co = sub.add_parser("collect").add_subparsers(required=True)
    cr = co.add_parser("run")
    cr.add_argument("--kinds", default="asks,bids,sales")
    cr.add_argument("--host", choices=["gha", "mac"], required=True)
    cr.add_argument("--sales-max", type=int, help="override mc.sales_max_names_per_run")
    cr.set_defaults(func=cmd_collect_run)
    cp = co.add_parser("pull")
    cp.add_argument("--cf-only", action="store_true", help="skip the git data repo, import only the Cloudflare buffer")
    cp.set_defaults(func=cmd_collect_pull)
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

    fw = sub.add_parser("forward", help="paper test: a strategy hour by hour on the market.csgo snapshots")
    fw.add_argument("--strategy", required=True)
    fw.add_argument("--from", dest="date_from", help="first decision hour, ISO, UTC; default: the first snapshot")
    fw.add_argument("--capital", help="comma-separated; default: capitals from cs2.toml")
    fw.add_argument("--params", default="{}")
    fw.add_argument("--min-days", type=int, default=MIN_DAYS, help="days of snapshots required")
    fw.set_defaults(func=cmd_forward)

    rp = sub.add_parser("report")
    rp.add_argument("--strategies", help="comma-separated subset; cash and bh_universe are always included")
    rp.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2),
                    help="worker processes (1 = in this process); results are identical either way")
    rp.set_defaults(func=cmd_report)

    sub.add_parser("strategies").set_defaults(func=cmd_strategies)

    ui = sub.add_parser("ui", help="local web UI on 127.0.0.1 (verdict, runs, market, collector, settings)")
    ui.add_argument("--port", type=int, default=8765)
    ui.set_defaults(func=cmd_ui)

    args = p.parse_args()
    try:
        args.func(args)
    except Cs2Error as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
