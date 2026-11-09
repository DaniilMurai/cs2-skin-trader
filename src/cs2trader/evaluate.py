import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from itertools import accumulate, pairwise, product
from statistics import NormalDist
from typing import Any

import polars as pl

from cs2trader.backtest.engine import load_market, run
from cs2trader.backtest.view import MarketData
from cs2trader.config import Settings, WfSettings, load_settings
from cs2trader.models import FillModel, Metrics, Regime, RunResult, RunSpec, Verdict
from cs2trader.store import Store
from cs2trader.strategies.registry import StrategyEntry

ANNUALISATION = math.sqrt(365)


def _returns(curve: Sequence[float]) -> list[float]:
    return [b / a - 1.0 for a, b in pairwise(curve)]


def _max_drawdown(curve: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak)
    return worst


def _sharpe(returns: Sequence[float]) -> float:
    # Sample std (ddof=1), so at least two daily returns are needed.
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    return 0.0 if std == 0 else mean / std * ANNUALISATION


def _compute(cash: list[float], steam: list[float], cash_returns: list[float],
             steam_returns: list[float], capital: float | None, n_trades: int,
             fees: float) -> Metrics:
    if capital is not None:
        net_cash, net_steam = cash[-1] / capital - 1.0, steam[-1] / capital - 1.0
    else:
        net_cash = math.prod(1.0 + r for r in cash_returns) - 1.0
        net_steam = math.prod(1.0 + r for r in steam_returns) - 1.0
    return Metrics(net_cash, net_steam, _max_drawdown(cash), _max_drawdown(steam),
                   _sharpe(cash_returns), n_trades, fees)


def _as_date(df: pl.DataFrame) -> pl.DataFrame:
    if isinstance(df.schema["t"], pl.Datetime):
        return df.with_columns(pl.col("t").dt.date())
    return df


def metrics(result: RunResult,
            regimes: pl.DataFrame | None = None) -> Metrics | dict[Regime, Metrics]:
    """Performance of one run, in cash-out and Steam-wallet units.

    Total (regimes=None):
    - net return = last equity / spec.capital - 1. It is measured against the capital, not
      against day-one equity, so the cash-out cost (already inside day-one equity_*) counts;
    - max drawdown = largest peak-to-trough fall of the equity curve, positive fraction;
    - Sharpe = mean / sample std (ddof=1) of daily simple returns of equity_cash, times
      sqrt(365); 0.0 with fewer than 2 returns or zero std;
    - n_trades = fills with qty_filled > 0; fees_paid = last row's cumulative fees_paid.

    Per regime (regimes has columns t, regime): a day belongs to the regime that labels it,
    and the return of day i (equity[i] / equity[i-1] - 1) belongs to day i's label, so day
    zero has no return. For each regime that has at least one day:
    - net return = the labelled days' returns chained (product of 1 + r, minus 1), no
      capital baseline; a regime with a single day has return 0.0;
    - max drawdown = on the path 1, (1 + r1), (1 + r1)(1 + r2), ... of the labelled days'
      returns, so a single falling day still shows its fall;
    - Sharpe = on the labelled days' returns (non-adjacent days are simply pooled);
    - n_trades = fills dated on labelled days; fees_paid = increments of cumulative
      fees_paid on labelled days (day zero counts its full fees_paid).
    Regimes with no days are omitted; days missing from `regimes` belong to no regime.
    """
    eq = _as_date(result.equity)
    cash = eq["equity_cash"].to_list()
    steam = eq["equity_steam"].to_list()
    cum_fees = eq["fees_paid"].to_list()
    trades = result.trades
    has_trades = trades.height > 0 and "qty_filled" in trades.columns
    if has_trades:
        trades = _as_date(trades).filter(pl.col("qty_filled") > 0)

    if regimes is None:
        return _compute(cash, steam, _returns(cash), _returns(steam), result.spec.capital,
                        trades.height if has_trades else 0, cum_fees[-1] if cum_fees else 0.0)

    labels = dict(zip(_as_date(regimes)["t"].to_list(), regimes["regime"].to_list()))
    days = eq["t"].to_list()
    out: dict[Regime, Metrics] = {}
    for regime in dict.fromkeys(labels.get(d) for d in days):
        if regime is None:
            continue
        idx = [i for i, d in enumerate(days) if labels.get(d) == regime]
        sel_days = {days[i] for i in idx}
        rc = [cash[i] / cash[i - 1] - 1.0 for i in idx if i > 0]
        rs = [steam[i] / steam[i - 1] - 1.0 for i in idx if i > 0]
        fees = sum(cum_fees[i] - (cum_fees[i - 1] if i > 0 else 0.0) for i in idx)
        n = trades.filter(pl.col("t").is_in(list(sel_days))).height if has_trades else 0
        out[Regime(regime)] = _compute(list(accumulate(rc, lambda a, r: a * (1 + r), initial=1.0)),
                                       list(accumulate(rs, lambda a, r: a * (1 + r), initial=1.0)),
                                       rc, rs, None, n, fees)
    return out


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[tuple[date, date, Mapping[str, Any], Metrics], ...]
    chosen_params: Mapping[str, Any]
    n_variants: int
    trial_sharpes: tuple[float, ...] = ()   # daily (not annualised) cash Sharpe of every tuning run
    test_returns: tuple[float, ...] = ()    # daily cash returns of the test windows, chained in order


def grid_variants(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = sorted(grid)
    return [dict(zip(keys, combo)) for combo in product(*(grid[k] for k in keys))] or [{}]


def make_spec(strategy: str, params: Mapping[str, Any], capital: float, start: date, end: date,
              settings: Settings, stress: bool = False) -> RunSpec:
    c, f = settings.costs, settings.fills
    cost_k = settings.verdict.cost_stress if stress else 1.0
    spread_k = settings.verdict.spread_stress if stress else 1.0
    costs = replace(c, seller_fee=c.seller_fee * cost_k, buyer_fee=c.buyer_fee * cost_k,
                    withdraw_fee_cash=c.withdraw_fee_cash * cost_k,
                    withdraw_fee_steam=c.withdraw_fee_steam * cost_k)
    fills = FillModel(participation_cap=f.participation_cap, spread_penalty=f.spread_penalty * spread_k,
                      ratio_fallback=f.ratio_fallback)
    return RunSpec(strategy, dict(params), capital, start, end, costs=(costs,), fills=fills)


def folds(s: WfSettings) -> list[tuple[date, date, date, date]]:
    """(train_start, train_end, test_start, test_end); test windows tile wf.start + train_days …
    wf.end, the last one cut at wf.end."""
    out = []
    test_start = s.start + timedelta(days=s.train_days)
    while test_start <= s.end:
        test_end = min(test_start + timedelta(days=s.test_days - 1), s.end)
        out.append((test_start - timedelta(days=s.train_days), test_start - timedelta(days=1),
                    test_start, test_end))
        test_start = test_end + timedelta(days=1)
    return out


def _choose(entry: StrategyEntry, capital: float, start: date, end: date, store: Store,
            settings: Settings, data: MarketData, fold: int, log: list,
            sharpes: list[float]) -> dict[str, Any]:
    best, best_sharpe = None, -math.inf
    for params in grid_variants(entry.grid):
        res = run(make_spec(entry.name, params, capital, start, end, settings), store, settings, data)
        log.append((entry.name, json.dumps(params, sort_keys=True), fold, capital, res.run_id))
        sharpe = metrics(res).sharpe_cash
        sharpes.append(sharpe / ANNUALISATION)
        # Strictly greater: ties keep the earlier grid point, so the choice is deterministic.
        if sharpe > best_sharpe:
            best, best_sharpe = params, sharpe
    return best


def walk_forward(entry: StrategyEntry, capital: float, store: Store, s: WfSettings,
                 settings: Settings | None = None, data: MarketData | None = None) -> WalkForwardResult:
    """Tune on each train window by cash Sharpe, test the winner on the next window, then tune
    once more on the `train_days` before the holdout for the params the holdout uses. Nothing
    on or after `s.holdout_start` is loaded."""
    settings = settings or load_settings()
    last_tuned = s.holdout_start - timedelta(days=1)
    data = data or load_market(store, s.start, min(s.end, last_tuned), settings)
    log: list = []
    sharpes: list[float] = []
    test_returns: list[float] = []
    out = []
    for i, (tr0, tr1, te0, te1) in enumerate(folds(s)):
        params = _choose(entry, capital, tr0, tr1, store, settings, data, i, log, sharpes)
        res = run(make_spec(entry.name, params, capital, te0, te1, settings), store, settings, data)
        test_returns += _returns(res.equity["equity_cash"].to_list())
        out.append((te0, te1, params, metrics(res)))
    final_start = s.holdout_start - timedelta(days=s.train_days)
    chosen = _choose(entry, capital, final_start, last_tuned, store, settings, data, len(out), log, sharpes)
    store.record_variants(log)
    return WalkForwardResult(tuple(out), chosen, len(log), tuple(sharpes), tuple(test_returns))


def holdout(entry: StrategyEntry, params: Mapping[str, Any], capital: float, store: Store,
            s: WfSettings, stress: bool = False, settings: Settings | None = None,
            end: date | None = None, data: MarketData | None = None) -> RunResult:
    settings = settings or load_settings()
    end = end or store.last_day()
    spec = make_spec(entry.name, params, capital, s.holdout_start, end, settings, stress)
    return run(spec, store, settings, data)


EULER_GAMMA = 0.5772156649015329


def deflated_sharpe(returns: Sequence[float], trial_sharpes: Sequence[float], n_trials: int) -> float:
    """Annualised Sharpe minus the expected maximum Sharpe of `n_trials` unskilled variants
    (Bailey & López de Prado 2014, "The Deflated Sharpe Ratio", eq. for SR0), using the
    variance of the trial Sharpes. > 0 means the observed Sharpe beats what picking the best of
    that many random variants would give. With one trial nothing is deflated."""
    if len(returns) < 2:
        return 0.0
    sr = _sharpe(returns) / ANNUALISATION
    if n_trials <= 1 or len(trial_sharpes) < 2:
        return sr * ANNUALISATION
    mean = sum(trial_sharpes) / len(trial_sharpes)
    var = sum((x - mean) ** 2 for x in trial_sharpes) / (len(trial_sharpes) - 1)
    z = NormalDist().inv_cdf
    sr0 = math.sqrt(var) * ((1 - EULER_GAMMA) * z(1 - 1 / n_trials)
                            + EULER_GAMMA * z(1 - 1 / (n_trials * math.e)))
    return (sr - sr0) * ANNUALISATION


def verdict(strategy: str, capital: float, wf: WalkForwardResult, ho: RunResult,
            ho_stress: RunResult, bh: RunResult, cash: RunResult) -> Verdict:
    """AC-12, rules checked in this order; the first broken one is reported. The stressed run is
    compared with the benchmarks' normal runs — the stricter reading of "stays above both"."""
    m, ms, mb, mc = metrics(ho), metrics(ho_stress), metrics(bh), metrics(cash)
    dsr = deflated_sharpe(wf.test_returns, wf.trial_sharpes, wf.n_variants)
    rules = (
        ("beats_cash", m.net_return_cash > mc.net_return_cash),
        ("beats_bh", m.net_return_cash > mb.net_return_cash),
        ("drawdown", m.max_drawdown_cash <= mb.max_drawdown_cash),
        ("cost_stress", ms.net_return_cash > mc.net_return_cash and ms.net_return_cash > mb.net_return_cash),
        ("deflated_sharpe", dsr > 0),
    )
    broken = next((name for name, ok in rules if not ok), None)
    return Verdict(strategy, capital, "fails" if broken else "holds_up", broken, m, dsr, wf.n_variants)
