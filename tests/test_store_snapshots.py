from datetime import UTC, datetime, timedelta

from cs2trader.models import AskRow, BidRow, Snapshot
from cs2trader.store import Store

T0 = datetime(2026, 11, 9, 13, 0, 5, tzinfo=UTC)
FETCHED = T0 + timedelta(seconds=3)


def asks(host="gha", feed=T0):
    return Snapshot("asks", feed, FETCHED, host, [AskRow("AK", 10.5, 3), AskRow("Case", 0.3, 900)])


def bids(host="gha", feed=T0):
    return Snapshot("bids", feed, FETCHED, host, [BidRow("AK", 9.5, 7)])


def test_views_empty_without_files(tmp_path):
    s = Store(tmp_path)
    assert s.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert s.db.execute("SELECT count(*) FROM bids").fetchone()[0] == 0


def test_same_snapshot_two_hosts(tmp_path):
    s = Store(tmp_path)
    p1, p2 = s.write_snapshot(asks("gha")), s.write_snapshot(asks("mac"))
    assert p1 != p2 and p1.exists() and p2.exists()
    assert p1.parent.name == "day=2026-11-09"
    assert p1.name == "20261109T130005Z-gha.parquet"
    assert len(list((tmp_path / "collector" / "asks").glob("day=*/*.parquet"))) == 2
    rows = s.db.execute("SELECT market_hash_name, ask, listings FROM asks ORDER BY 1").fetchall()
    assert rows == [("AK", 10.5, 3), ("Case", 0.3, 900)]


def test_two_feed_times_kept(tmp_path):
    s = Store(tmp_path)
    s.write_snapshot(asks(feed=T0))
    s.write_snapshot(asks(feed=T0 + timedelta(hours=1)))
    assert s.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 4


def test_rewrite_same_host_overwrites(tmp_path):
    s = Store(tmp_path)
    s.write_snapshot(bids())
    s.write_snapshot(bids())
    assert len(list((tmp_path / "collector" / "bids").glob("day=*/*.parquet"))) == 1
    assert s.db.execute("SELECT count(*) FROM bids").fetchone()[0] == 1


def test_data_version_follows_content_not_snapshots(tmp_path):
    s = Store(tmp_path)
    v0, snap0 = s.data_version(), s.snapshot_version()
    s.write_snapshot(asks())
    assert s.data_version() == v0 and s.snapshot_version() != snap0
    s.write_snapshot(asks(feed=T0 + timedelta(hours=1)))
    assert s.data_version() == v0
    s.db.execute("INSERT INTO items (name, category) VALUES ('a', 'knife')")
    v1 = s.data_version()
    assert v1 != v0
    s.db.execute("UPDATE items SET category = 'glove'")
    assert s.data_version() != v1


def test_data_version_sees_price_rescaling(tmp_path):
    s = Store(tmp_path)
    s.db.execute("INSERT INTO steam_points VALUES ('a', TIMESTAMPTZ '2024-01-01', 'daily', 1.0, 'UAH', 5, 40.0, DATE '2024-01-01', 1.0)")
    v = s.data_version()
    s.db.execute("UPDATE steam_points SET price_usd = price_usd * 1.5")
    assert s.data_version() != v


def test_view_visible_to_reopened_store(tmp_path):
    Store(tmp_path).write_snapshot(bids())
    assert Store(tmp_path).db.execute("SELECT bid, orders FROM bids").fetchall() == [(9.5, 7)]
