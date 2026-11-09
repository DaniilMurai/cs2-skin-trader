import random
from datetime import date, timedelta

import polars as pl
import pytest

from cs2trader.evaluate import WalkForwardResult, deflated_sharpe, verdict
from cs2trader.models import RunResult, RunSpec

D0 = date(2025, 10, 1)


def result(curve: list[float], capital: float = 100.0) -> RunResult:
    days = [D0 + timedelta(i) for i in range(len(curve))]
    eq = pl.DataFrame({"t": days, "balance": curve, "holdings_value": [0.0] * len(curve),
                       "equity_cash": curve, "equity_steam": curve, "fees_paid": [0.0] * len(curve)})
    return RunResult("id", RunSpec("s", {}, capital, days[0], days[-1]), "dv", pl.DataFrame(), eq, None)


CASH = result([95.0] * 5)
BH = result([95.0, 90.0, 100.0, 105.0, 104.0])          # +4%, drawdown 5.3%
GOOD = result([95.0, 96.0, 102.0, 108.0, 110.0])         # +10%, drawdown 0
rng = random.Random(1)
SKILL = [0.01 + rng.gauss(0, 0.002) for _ in range(200)]
WF_GOOD = WalkForwardResult((), {}, 10, tuple(rng.gauss(0, 0.01) for _ in range(10)), tuple(SKILL))


def check(ho, stress=None, wf=WF_GOOD):
    return verdict("s", 100.0, wf, ho, stress or ho, BH, CASH)


def test_all_rules_pass():
    v = check(GOOD)
    assert (v.status, v.first_broken_rule) == ("holds_up", None)
    assert v.n_variants == 10 and v.deflated_sharpe > 0


@pytest.mark.parametrize("ho,stress,rule", [
    (result([95.0, 94.0, 93.0, 94.0, 94.0]), None, "beats_cash"),
    (result([95.0, 96.0, 97.0, 98.0, 99.0]), None, "beats_bh"),           # −1% beats cash, not bh
    (result([95.0, 120.0, 100.0, 110.0, 110.0]), None, "drawdown"),       # +10%, drawdown 16.7%
    (GOOD, result([95.0, 95.0, 100.0, 103.0, 103.0]), "cost_stress"),     # stressed +3% < bh +4%
])
def test_each_rule_fails_alone(ho, stress, rule):
    v = check(ho, stress)
    assert (v.status, v.first_broken_rule) == ("fails", rule)


def test_deflated_sharpe_rule():
    r = random.Random(2)
    noise = tuple(r.gauss(0.0002, 0.02) for _ in range(200))
    wf = WalkForwardResult((), {}, 500, tuple(r.gauss(0, 0.05) for _ in range(500)), noise)
    v = check(GOOD, wf=wf)
    assert (v.status, v.first_broken_rule) == ("fails", "deflated_sharpe")


def test_more_trials_deflate_more():
    r = random.Random(4)
    trials = [r.gauss(0, 0.02) for _ in range(50)]
    one = deflated_sharpe(SKILL, trials, 1)
    ten = deflated_sharpe(SKILL, trials, 10)
    thousand = deflated_sharpe(SKILL, trials, 1000)
    assert one > ten > thousand
    assert deflated_sharpe([0.01], trials, 10) == 0.0
