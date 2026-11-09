import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cs2trader.check import format_results, raise_if_failed, run_checks
from cs2trader.errors import DataCheckFailed
from cs2trader.models import FxRate, ItemInfo, SteamPoint
from cs2trader.sources.items_db import classify
from cs2trader.store import Store

DB = list(json.loads((Path(__file__).parent / "fixtures" / "items_db_sample.json").read_text()).values())
UNIVERSE = ["Recoil Case", "Sticker | 00 Nation (Holo) | Rio 2022"]
FETCH_DAY = datetime.now(UTC).date()


def good_store(tmp_path: Path) -> Store:
    store = Store(tmp_path)
    fx = FxRate(FETCH_DAY, "UAH", 40.0, "nbu", False)
    store.upsert_fx([fx])
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for name in UNIVERSE:
        store.upsert_steam([SteamPoint(name, start + timedelta(days=d), 400.0 + d, "UAH", 3, "day")
                            for d in range(120)], fx)
        store.mark_fetched(name, "ok", 120)
    store.upsert_items(classify(UNIVERSE, DB))
    store.db.execute("UPDATE items SET release_date = DATE '2024-01-01'")
    return store


def run(store, log=None, categories=True, universe=UNIVERSE):
    return {r.name: r for r in run_checks(store, universe, log, categories, items_db=DB)}


def test_good_database_passes(tmp_path):
    results = run_checks(good_store(tmp_path), UNIVERSE, None, True, items_db=DB)
    assert all(r.ok for r in results), format_results(results)
    assert {r.name for r in results} >= {"coverage", "first_date", "duplicates", "fx", "request_rate",
                                         "unknown_share", "ac4_list"}
    raise_if_failed(results)


def test_categories_checks_only_on_flag(tmp_path):
    names = {r.name for r in run_checks(good_store(tmp_path), UNIVERSE, None, False)}
    assert "unknown_share" not in names and "ac4_list" not in names


def test_coverage_fails(tmp_path):
    store = good_store(tmp_path)
    res = run(store, universe=[*UNIVERSE, "Never Fetched"])["coverage"]
    assert not res.ok and "unfetched 1" in res.detail


def test_first_date_fails_before_release_and_too_late(tmp_path):
    store = good_store(tmp_path)
    store.db.execute("UPDATE items SET release_date = DATE '2024-02-01' WHERE name = 'Recoil Case'")
    assert not run(store)["first_date"].ok
    store.db.execute("UPDATE items SET release_date = DATE '2023-12-01' WHERE name = 'Recoil Case'")
    assert not run(store)["first_date"].ok


def test_first_date_skips_unknown_release(tmp_path):
    store = good_store(tmp_path)
    store.db.execute("UPDATE items SET release_date = NULL")
    assert run(store)["first_date"].ok


def test_duplicate_daily_dates_fail(tmp_path):
    store = good_store(tmp_path)
    store.db.execute("INSERT INTO steam_points SELECT name, ts + INTERVAL 1 HOUR, granularity, price_raw, "
                     "currency, volume, fx_per_usd, fx_day, price_usd FROM steam_points WHERE name = 'Recoil Case' "
                     "LIMIT 1")
    assert not run(store)["duplicates"].ok


@pytest.mark.parametrize("sql", [
    "UPDATE steam_points SET fx_per_usd = NULL WHERE name = 'Recoil Case'",
    "UPDATE steam_points SET price_usd = price_usd * 1.001",
    "UPDATE steam_points SET fx_day = fx_day - 1",
    "DELETE FROM fx_rates",
])
def test_fx_fails(tmp_path, sql):
    store = good_store(tmp_path)
    store.db.execute(sql)
    assert not run(store)["fx"].ok


def write_log(path: Path, n: int, spacing_s: float) -> Path:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    path.write_text("".join(f"{(t0 + timedelta(seconds=i * spacing_s)).isoformat()}\t200\n" for i in range(n)))
    return path


def test_request_rate(tmp_path):
    store = good_store(tmp_path / "db")
    assert run(store, write_log(tmp_path / "ok.tsv", 100, 2.6))["request_rate"].ok  # 24 per 60 s
    assert not run(store, write_log(tmp_path / "fast.tsv", 100, 2.0))["request_rate"].ok  # 30 per 60 s
    missing = run(store, tmp_path / "none.tsv")["request_rate"]
    assert missing.ok and missing.detail == "no log"


def test_unknown_share_fails(tmp_path):
    store = good_store(tmp_path)
    store.upsert_items(classify(["Mystery Thing"], DB))
    res = run(store, universe=[*UNIVERSE, "Mystery Thing"])["unknown_share"]
    assert not res.ok


def test_ac4_list_fails_without_db(tmp_path):
    store = good_store(tmp_path)
    res = {r.name: r for r in run_checks(store, UNIVERSE, None, True, items_db=[])}["ac4_list"]
    assert not res.ok


def test_raise_if_failed(tmp_path):
    store = good_store(tmp_path)
    store.db.execute("DELETE FROM fx_rates")
    with pytest.raises(DataCheckFailed) as e:
        raise_if_failed(run_checks(store, UNIVERSE, None, False))
    assert e.value.exit_code == 6 and "fx" in str(e.value)
