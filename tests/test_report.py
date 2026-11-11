from datetime import timedelta

import polars as pl
import pytest
from test_lookahead import D0, synthetic_bars
from test_walk_forward import Store, Toy

from cs2trader.config import Settings, WfSettings
from cs2trader.report import (
    KEY,
    build_report,
    results_hash,
    results_table,
    summary_line,
)
from cs2trader.strategies import registry

WF = WfSettings(start=D0, end=D0 + timedelta(149), train_days=60, test_days=30,
                holdout_start=D0 + timedelta(150))
SETTINGS = Settings(wf=WF, capitals=[100.0, 1000.0])
NAMES = ["cash", "bh_universe", "toy", "mean_reversion"]


@pytest.fixture(autouse=True)
def toy(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "toy", registry.StrategyEntry(
        "toy", "momentum", Toy, {"lookback": [3, 10]}, "Buy the **best** item.\n\nSecond <paragraph>."))


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(7, 200)


def test_hash_is_reproducible_and_html_has_ac11_columns(bars, tmp_path):
    p1 = build_report(Store(bars), tmp_path / "a", SETTINGS, NAMES)
    p2 = build_report(Store(bars), tmp_path / "b", SETTINGS, NAMES)
    t1 = pl.read_parquet(p1.parent / "results.parquet")
    t2 = pl.read_parquet(p2.parent / "results.parquet")
    assert results_hash(t1) == results_hash(t2) == results_hash(t2.sample(fraction=1.0, shuffle=True))
    assert results_hash(t1) in p1.read_text()
    assert t1.equals(results_table(Store(bars), SETTINGS, NAMES))

    page = p1.read_text()
    first = page.split("\n", 1)[0]
    assert "holds up" in first
    for col in ("net return cash", "net return steam", "max DD cash", "max DD steam", "trades", "fees paid",
                "first broken rule", "deflated Sharpe", "n_variants"):
        assert col in page
    assert {"walk_forward", "holdout", "holdout_stress"} <= set(t1["period"])
    assert {"cash", "steam"} == set(t1["unit"])
    assert {"all"} <= set(t1["regime"])
    for c in ("net_return", "max_drawdown", "n_trades", "fees_paid", "sharpe"):
        assert c in t1.columns
    assert t1.select(KEY).is_unique().all()
    for name in NAMES:
        assert name in page
    assert "<b>best</b>" in page and "&lt;paragraph&gt;" in page
    assert "Data version: fake" in page and "ratio_fallback" in page


def test_summary_line():
    assert summary_line([], [100.0, 1000.0]) == "No strategy holds up at $100 / $1,000"


def test_worker_processes_give_the_same_results(bars):
    # The toy strategy lives only in this process's registry, so the workers get registry strategies only.
    names = ["cash", "bh_universe", "mean_reversion"]
    one = results_table(Store(bars), SETTINGS, names, jobs=1)
    two = results_table(Store(bars), SETTINGS, names, jobs=2)
    assert results_hash(one) == results_hash(two)
    assert one.equals(two)


def test_chained_walk_forward_applies_the_withdrawal_fee_once(bars):
    t = results_table(Store(bars), SETTINGS, ["cash"])
    wf = t.filter((pl.col("strategy") == "cash") & (pl.col("period") == "walk_forward")
                  & (pl.col("regime") == "all") & (pl.col("capital") == 1000.0))
    ho = t.filter((pl.col("strategy") == "cash") & (pl.col("period") == "holdout")
                  & (pl.col("regime") == "all") & (pl.col("capital") == 1000.0))
    # Holding cash over any span costs the one withdrawal haircut, however many folds it is cut into.
    assert wf.filter(pl.col("unit") == "cash")["net_return"][0] == pytest.approx(
        ho.filter(pl.col("unit") == "cash")["net_return"][0])
    assert wf.filter(pl.col("unit") == "steam")["net_return"][0] == pytest.approx(
        ho.filter(pl.col("unit") == "steam")["net_return"][0])


def test_benchmark_for_the_verdict_is_the_median_of_many_baskets(bars):
    t = results_table(Store(bars), SETTINGS, ["cash", "bh_universe"])
    med = t.filter((pl.col("strategy") == "bh_median") & (pl.col("period") == "holdout")
                   & (pl.col("regime") == "all") & (pl.col("unit") == "cash"))
    assert sorted(med["capital"]) == [100.0, 1000.0]
