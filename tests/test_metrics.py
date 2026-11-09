import math
import statistics
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.evaluate import metrics
from cs2trader.models import Regime, RunResult, RunSpec

D0 = date(2024, 1, 1)
CASH = [98, 100, 102, 96, 96, 99, 105, 100, 104, 98]
STEAM = [120, 121, 125, 118, 118, 119, 127, 119, 125, 118]
FEES = [0.5, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0, 3.0]
FILL_QTY = {0: 1, 1: 1, 3: 1, 4: 0, 7: 2, 8: 1, 9: 0}


def day(i: int) -> date:
    return D0 + timedelta(days=i)


def make_result(trades: bool = True) -> RunResult:
    equity = pl.DataFrame({
        "t": [day(i) for i in range(10)], "balance": [0.0] * 10, "holdings_value": [0.0] * 10,
        "equity_cash": [float(v) for v in CASH], "equity_steam": [float(v) for v in STEAM],
        "fees_paid": FEES,
    })
    fills = pl.DataFrame({
        "t": [day(i) for i in FILL_QTY], "qty_filled": list(FILL_QTY.values()),
        "fee": [0.1] * len(FILL_QTY),
    }) if trades else pl.DataFrame()
    spec = RunSpec(strategy="x", params={}, capital=100.0, start=day(0), end=day(9))
    return RunResult("id", spec, "dv", fills, equity, None)


def test_total_metrics():
    m = metrics(make_result())
    assert m.net_return_cash == pytest.approx(-0.02)
    assert m.net_return_steam == pytest.approx(0.18)
    assert m.max_drawdown_cash == pytest.approx(7 / 105)
    assert m.max_drawdown_steam == pytest.approx(9 / 127)
    rets = [b / a - 1 for a, b in zip(CASH, CASH[1:])]
    assert m.sharpe_cash == pytest.approx(
        statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(365))
    assert m.n_trades == 5
    assert m.fees_paid == 3.0


def test_empty_trades_and_flat_curve():
    assert metrics(make_result(trades=False)).n_trades == 0
    res = make_result(trades=False)
    flat = res.equity.with_columns(equity_cash=pl.lit(100.0), equity_steam=pl.lit(100.0))
    m = metrics(RunResult("id", res.spec, "dv", res.trades, flat, None))
    assert m.sharpe_cash == 0.0 and m.max_drawdown_cash == 0.0


def test_single_point_sharpe_zero():
    res = make_result()
    one = RunResult("id", res.spec, "dv", res.trades, res.equity.head(1), None)
    assert metrics(one).sharpe_cash == 0.0


def test_per_regime_split():
    labels = ["rising"] * 3 + ["falling"] * 3 + ["rising"] * 2 + ["flat"] * 2
    regimes = pl.DataFrame({"t": [day(i) for i in range(10)], "regime": labels})
    out = metrics(make_result(), regimes)
    assert set(out) == {Regime.RISING, Regime.FALLING, Regime.FLAT}

    r = out[Regime.RISING]  # days 0,1,2,6,7; returns of days 1,2,6,7
    assert r.net_return_cash == pytest.approx(102 / 98 * 100 / 99 - 1)
    assert r.net_return_steam == pytest.approx(125 / 120 - 1)
    assert r.max_drawdown_cash == pytest.approx(5 / 105)  # 98,100,102,105,100
    rets = [100 / 98 - 1, 102 / 100 - 1, 105 / 99 - 1, 100 / 105 - 1]
    assert r.sharpe_cash == pytest.approx(
        statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(365))
    assert (r.n_trades, r.fees_paid) == (3, 1.5)

    f = out[Regime.FALLING]  # days 3,4,5: 96, 96, 99
    assert f.net_return_cash == pytest.approx(99 / 102 - 1)
    assert f.max_drawdown_cash == pytest.approx(1 - 96 / 102)  # path 1, 96/102, 96/102, 99/102
    assert (f.n_trades, f.fees_paid) == (1, 1.0)

    fl = out[Regime.FLAT]  # days 8,9: 104, 98; returns 104/100-1 and 98/104-1
    assert fl.net_return_cash == pytest.approx(98 / 100 - 1)
    assert fl.max_drawdown_cash == pytest.approx(6 / 104)
    rets = [104 / 100 - 1, 98 / 104 - 1]
    assert fl.sharpe_cash == pytest.approx(
        statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(365))
    assert (fl.n_trades, fl.fees_paid) == (1, 0.5)


def test_regime_without_days_is_omitted():
    regimes = pl.DataFrame({"t": [day(i) for i in range(10)], "regime": ["flat"] * 10})
    assert set(metrics(make_result(), regimes)) == {Regime.FLAT}
