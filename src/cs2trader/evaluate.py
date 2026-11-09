import math
from collections.abc import Sequence
from itertools import accumulate, pairwise

import polars as pl

from cs2trader.models import Metrics, Regime, RunResult

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
