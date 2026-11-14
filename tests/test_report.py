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


class NoHoldoutStore(Store):
    """Fails the run if anything asks for a bar on or after the holdout start."""

    def daily_bars(self, start, end):
        assert end < WF.holdout_start, f"holdout bars requested: {start} .. {end}"
        return super().daily_bars(start, end)

    def last_day(self):
        raise AssertionError("the last stored day is a holdout day")


@pytest.mark.parametrize("jobs,names", [(1, NAMES), (2, ["cash", "bh_universe", "mean_reversion"])])
def test_walk_forward_only_never_loads_a_holdout_bar(bars, tmp_path, jobs, names):
    import json

    store = NoHoldoutStore(bars)
    path = build_report(store, tmp_path, SETTINGS, names, jobs=jobs, walk_forward_only=True)
    assert path.parent.name.endswith("-walk-forward")
    assert "Walk-forward only" in path.read_text().split("\n", 1)[0]
    t = pl.read_parquet(path.parent / "results.parquet")
    assert not t.filter(pl.col("period").str.starts_with("holdout")).height
    assert {"walk_forward", "wf_fold_00", "wf_fold_02"} <= set(t["period"])
    assert set(t["strategy"]) == {*names, "bh_median"}
    assert set(t.filter(pl.col("strategy") == "bh_median")["period"]) == {"walk_forward"}

    # Same folds and params as the full report, which does read the holdout.
    full = results_table(Store(bars), SETTINGS, names)
    on = (pl.col("regime") == "all") & (pl.col("strategy") != "bh_median") & ~pl.col("period").str.starts_with("holdout")
    assert t.filter(on).equals(full.filter(on))

    chosen = json.loads((path.parent / "walk_forward.json").read_text())
    assert {(c["strategy"], c["capital"]) for c in chosen} == {(n, c) for n in names for c in SETTINGS.capitals}
    grid = {"cash": 1, "bh_universe": 1, "toy": 2, "mean_reversion": None}
    for c in chosen:
        assert len(c["fold_params"]) == 3
        if grid[c["strategy"]]:
            assert c["n_variants"] == grid[c["strategy"]] * 4
    assert len(store.variants) == sum(c["n_variants"] for c in chosen)


def test_walk_forward_median_benchmark_is_the_middle_basket(bars):
    from cs2trader.report import evaluate_all

    t = evaluate_all(NoHoldoutStore(bars), SETTINGS, ["cash", "bh_universe"], walk_forward_only=True).results
    row = lambda name: t.filter((pl.col("strategy") == name) & (pl.col("period") == "walk_forward")
                                & (pl.col("regime") == "all") & (pl.col("unit") == "cash") & (pl.col("capital") == 100.0))
    assert row("bh_median").height == 1 and row("bh_universe").height == 1
