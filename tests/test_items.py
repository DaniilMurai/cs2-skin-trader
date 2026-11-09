import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from cs2trader.items import update_items
from cs2trader.models import Category, FxRate, ItemInfo, SteamPoint
from cs2trader.sources import items_db
from cs2trader.sources.items_db import classify, fetch_items_db
from cs2trader.store import Store

DB = list(json.loads((Path(__file__).parent / "fixtures" / "items_db_sample.json").read_text()).values())

# The fixed AC-4 list: (name, category, source)
AC4 = [
    ("★ Karambit | Doppler (Factory New)", Category.KNIFE, "db"),
    ("★ StatTrak™ Bayonet | Autotronic (Field-Tested)", Category.KNIFE, "db"),
    ("★ Sport Gloves | Amphibious (Field-Tested)", Category.GLOVE, "db"),
    ("★ Hand Wraps | Arboreal (Factory New)", Category.GLOVE, "db"),
    ("AK-47 | Redline (Field-Tested)", Category.WEAPON_SKIN, "db"),
    ("StatTrak™ AWP | Asiimov (Field-Tested)", Category.WEAPON_SKIN, "db"),
    ("Souvenir AWP | Acheron (Factory New)", Category.WEAPON_SKIN, "db"),
    ("Sticker | 00 Nation (Holo) | Rio 2022", Category.STICKER, "db"),
    ("Recoil Case", Category.CASE, "db"),
    ("10 Year Birthday Sticker Capsule", Category.CAPSULE, "db"),
    ("Antwerp 2022 Dust II Souvenir Package", Category.SOUVENIR_PACKAGE, "db"),
    ("Sealed Graffiti | 100 Thieves | Boston 2018", Category.GRAFFITI, "db"),
    ("Charm | 8 Ball IGL", Category.CHARM, "db"),
    ("Souvenir Charm | Austin 2025 Highlight | 180 One Tap", Category.CHARM, "rule"),
    ("Patch | Abandon Hope", Category.PATCH, "db"),
    ("Music Kit | Adam Beyer, Red Room", Category.MUSIC_KIT, "db"),
    ("Deluge Music Kit Box", Category.MUSIC_KIT_BOX, "db"),
    ("Bloody Darryl The Strapped | The Professionals", Category.AGENT, "db"),
    ("Austin 2025 Viewer Pass", Category.OTHER, "db"),
    ("Totally New Gadget", Category.UNKNOWN, "none"),
]


def test_ac4_list():
    assert len(AC4) == 20
    got = classify([n for n, _, _ in AC4], DB)
    assert [(i.market_hash_name, i.category, i.source) for i in got] == AC4


def test_flags_and_metadata():
    by = {i.market_hash_name: i for i in classify([n for n, _, _ in AC4], DB)}
    st = by["StatTrak™ AWP | Asiimov (Field-Tested)"]
    assert st.stattrak and not st.souvenir and st.weapon == "AWP" and st.rarity
    assert by["Souvenir AWP | Acheron (Factory New)"].souvenir
    assert not by["AK-47 | Redline (Field-Tested)"].stattrak
    assert by["Recoil Case"].release_date is not None


def test_rules_without_db():
    names = ["★ Specialist Gloves | Foo (Well-Worn)", "Sticker | New (Foil)", "Brand New Case", "Zzz"]
    cats = [i.category for i in classify(names, [])]
    assert cats == [Category.GLOVE, Category.STICKER, Category.CASE, Category.UNKNOWN]


def test_fetch_caches_per_utc_day(tmp_path, monkeypatch):
    calls = []

    def fake_stream(method, url, **kw):
        calls.append(url)
        return httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"sticker-1": {"id": "sticker-1", "name": "Sticker | A"}})
        )).stream(method, url)

    monkeypatch.setattr(items_db.httpx, "stream", fake_stream)
    assert fetch_items_db(tmp_path)[0]["name"] == "Sticker | A"
    assert fetch_items_db(tmp_path)[0]["name"] == "Sticker | A"
    assert len(calls) == 1


def _store_with_point(tmp_path: Path, name: str) -> Store:
    store = Store(tmp_path)
    fx = FxRate(date(2026, 1, 5), "UAH", 40.0, "nbu", False)
    store.upsert_steam([SteamPoint(name, datetime(2026, 1, 5, tzinfo=UTC), 400.0, "UAH", 3, "day")], fx)
    return store


def test_daily_bars_has_category(tmp_path):
    store = _store_with_point(tmp_path, "Recoil Case")
    bars = store.daily_bars(date(2026, 1, 1), date(2026, 1, 31))
    assert "category" in bars.columns and bars["category"].to_list() == [None]
    store.upsert_items(classify(["Recoil Case"], DB))
    bars = store.daily_bars(date(2026, 1, 1), date(2026, 1, 31))
    assert bars["category"].to_list() == ["case"]


def test_upsert_keeps_first_seen_and_changes_version(tmp_path):
    store = Store(tmp_path)
    v0 = store.data_version()
    store.upsert_items(classify(["Recoil Case"], DB))
    store.db.execute("UPDATE items SET first_seen = DATE '2020-01-01'")
    store.upsert_items([ItemInfo("Recoil Case", Category.OTHER, "rule", None, None, False, False, None)])
    row = store.db.execute("SELECT category, first_seen FROM items").fetchone()
    assert (row[0], row[1]) == ("other", date(2020, 1, 1))
    assert store.data_version() != v0


def test_update_items_reports_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("cs2trader.items.fetch_items_db", lambda cache_dir: DB)
    store = Store(tmp_path)
    store.mark_fetched("Mystery Thing", "ok", 1)
    res = update_items(store, ["Recoil Case", "Sticker | 00 Nation (Holo) | Rio 2022"], tmp_path / "cache")
    assert res["n"] == 3 and res["counts"] == {"case": 1, "sticker": 1, "unknown": 1}
    assert res["unknown_share"] == pytest.approx(1 / 3) and res["unknown_names"] == ["Mystery Thing"]
