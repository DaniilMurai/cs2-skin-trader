from datetime import UTC, datetime, timedelta

from cs2trader.collect import collect_status, format_status
from cs2trader.models import AskRow, BidRow, Snapshot
from cs2trader.store import Store

NOW = datetime(2026, 11, 9, 12, 30, tzinfo=UTC)
H0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # the newest hour slot


def put(store, hours_ago, n=10, asks=True, bids=True, minute=5):
    t = H0 - timedelta(hours=hours_ago) + timedelta(minutes=minute)
    if asks:
        store.write_snapshot(Snapshot("asks", t, t, "gha", [AskRow(f"i{k}", 1.0, 1) for k in range(n)]))
    if bids:
        store.write_snapshot(Snapshot("bids", t, t, "gha", [BidRow(f"i{k}", 0.9, 1) for k in range(n)]))


def test_hole_and_low_share(tmp_path):
    s = Store(tmp_path)
    for h in range(0, 10):
        if h not in (4, 5, 6):
            put(s, h, n=7 if h == 8 else 10)
    st = collect_status(s, window_h=10, now=NOW)
    assert st.snapshots == 7
    assert st.gaps == ((H0 - timedelta(hours=6), H0 - timedelta(hours=3)),)
    assert st.low_share == (H0 - timedelta(hours=8),)
    assert abs(st.min_item_share - 0.7) < 1e-9
    text = format_status(st)
    assert "2026-11-09 06:00 -> 2026-11-09 09:00" in text and "low share: 2026-11-09 04:00" in text


def test_short_hole_not_a_gap(tmp_path):
    s = Store(tmp_path)
    for h in (0, 1, 4, 5):
        put(s, h)
    assert collect_status(s, window_h=6, now=NOW).gaps == ()


def test_asks_only_hour_does_not_count(tmp_path):
    s = Store(tmp_path)
    put(s, 0)
    put(s, 1, bids=False)
    put(s, 2, asks=False)
    st = collect_status(s, window_h=6, now=NOW)
    assert st.snapshots == 1
    assert st.gaps == ((H0 - timedelta(hours=5), H0),)


def test_window_boundaries(tmp_path):
    s = Store(tmp_path)
    put(s, 0)
    put(s, 3)
    put(s, 4)
    # now=12:30, window 4 h -> (08:30, 12:30]: the 09:00 slot is in, 08:00 is out
    assert collect_status(s, window_h=4, now=NOW).snapshots == 2
    # exactly on the hour: (08:00, 12:00] keeps 12:00 and drops 08:00
    assert collect_status(s, window_h=4, now=H0).snapshots == 2
    assert collect_status(s, window_h=5, now=NOW).snapshots == 3


def test_larger_asks_file_in_hour_and_empty(tmp_path):
    s = Store(tmp_path)
    assert collect_status(s, window_h=6, now=NOW).snapshots == 0
    put(s, 0, n=5, minute=5)
    put(s, 0, n=10, minute=40, bids=False)
    put(s, 1, n=10)
    st = collect_status(s, window_h=6, now=NOW)
    assert st.snapshots == 2 and st.min_item_share == 1.0
