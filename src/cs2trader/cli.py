import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cs2trader.backfill import backfill, feed_universe, read_names, request_logger
from cs2trader.backtest.engine import run
from cs2trader.config import load_settings, require_secret
from cs2trader.errors import Cs2Error, SteamAuthError
from cs2trader.models import RunSpec
from cs2trader.sources import fx, steam
from cs2trader.store import Store

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
    rate = store.fx(datetime.now(UTC).date(), "UAH")
    universe = args.names or settings.backfill.universe
    names = feed_universe(store) if universe == "feed" else read_names(Path(universe))
    client = steam.SteamClient(
        require_secret("STEAM_LOGIN_SECURE"), interval_s=settings.steam.interval_s,
        max_retries=settings.steam.max_retries, backoff_s=settings.steam.backoff_s,
        on_request=request_logger(settings.data_dir / "logs" / "steam_requests.tsv"))
    try:
        rep = backfill(store, client, names, rate, refresh=args.refresh, limit=args.limit,
                       stop_after_errors=settings.steam.stop_after_errors)
    except SteamAuthError:
        print("refresh STEAM_LOGIN_SECURE in .env and re-run; stored items are kept", file=sys.stderr)
        raise
    print(f"backfill: {rep.ok} ok, {rep.empty} empty, {rep.errors} errors, {rep.skipped} already stored")


def cmd_backtest(args: argparse.Namespace) -> None:
    settings = load_settings()
    spec = RunSpec(args.strategy, json.loads(args.params), args.capital,
                   date.fromisoformat(args.date_from), date.fromisoformat(args.date_to))
    result = run(spec, Store(settings.data_dir))
    out = settings.data_dir / "runs" / result.run_id
    out.mkdir(parents=True, exist_ok=True)
    result.trades.write_parquet(out / "trades.parquet")
    result.equity.write_parquet(out / "equity.parquet")
    (out / "spec.json").write_text(json.dumps({"spec": str(spec), "data_version": result.data_version}))
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

    bt = sub.add_parser("backtest")
    bt.add_argument("--strategy", required=True)
    bt.add_argument("--capital", type=float, required=True)
    bt.add_argument("--from", dest="date_from", required=True)
    bt.add_argument("--to", dest="date_to", required=True)
    bt.add_argument("--params", default="{}")
    bt.set_defaults(func=cmd_backtest)

    args = p.parse_args()
    try:
        args.func(args)
    except Cs2Error as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
