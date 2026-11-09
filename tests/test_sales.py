from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cs2trader.collect import sales_due
from cs2trader.config import McSettings
from cs2trader.errors import MarketCsgoKeyError, SchemaError, SourceUnavailable
from cs2trader.models import AskRow, Sale, Snapshot
from cs2trader.sources.marketcsgo import MarketCsgoKeyed
from cs2trader.store import Store

KEY = "SECRETKEY123"
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def keyed(tmp_path, handler, sleeps=None, **kw) -> MarketCsgoKeyed:
    clock_t = [0.0]
    sl = sleeps if sleeps is not None else []

    def sleep(x):
        sl.append(x)
        clock_t[0] += x

    return MarketCsgoKeyed(KEY, 2.5, 20, tmp_path / "q", backoff_s=1.0, sleep=sleep,
                           clock=lambda: clock_t[0],
                           client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def ok_handler(requests):
    def h(request: httpx.Request) -> httpx.Response:
        names = request.url.params.get_list("list_hash_name[]")
        requests.append(names)
        data = {n: {"max": 1, "min": 1, "average": 1, "history": [[1794232665, 32.85], [1794232414, 32.34]]}
                for n in names if n != "missing"}
        return httpx.Response(200, json={"success": True, "currency": "RUB", "data": data})
    return h


def test_45_names_three_requests_spaced(tmp_path):
    reqs, sleeps = [], []
    names = [f"item {i}" for i in range(45)]
    out = keyed(tmp_path, ok_handler(reqs), sleeps).sales(names)
    assert [len(r) for r in reqs] == [20, 20, 5]
    assert sleeps == [2.5, 2.5]
    assert len(out) == 45
    s = out["item 0"][0]
    assert s == Sale("item 0", datetime.fromtimestamp(1794232665, UTC), 32.85, "RUB")


def test_missing_name_gives_empty_list(tmp_path):
    out = keyed(tmp_path, ok_handler([])).sales(["a", "missing"])
    assert out["missing"] == [] and len(out["a"]) == 2


def test_401_key_error_without_key(tmp_path):
    def h(request):
        return httpx.Response(401, json={"error": "Bad KEY"})
    with pytest.raises(MarketCsgoKeyError) as e:
        keyed(tmp_path, h).sales(["a"])
    assert KEY not in str(e.value)


def test_bad_key_body_200(tmp_path):
    def h(request):
        return httpx.Response(200, json={"error": "Bad KEY"})
    with pytest.raises(MarketCsgoKeyError):
        keyed(tmp_path, h).sales(["a"])


def test_503_retries_then_unavailable(tmp_path):
    calls, sleeps = [], []

    def h(request):
        calls.append(1)
        return httpx.Response(503)
    with pytest.raises(SourceUnavailable) as e:
        keyed(tmp_path, h, sleeps, max_retries=2).sales(["a"])
    assert len(calls) == 3 and KEY not in str(e.value)


def test_network_error_message_has_no_key(tmp_path):
    def h(request):
        raise httpx.ConnectError("boom " + str(request.url))
    with pytest.raises(SourceUnavailable) as e:
        keyed(tmp_path, h, max_retries=1).sales(["a"])
    assert KEY not in str(e.value) and "key=" not in str(e.value)


def test_unknown_currency_schema_error(tmp_path):
    def h(request):
        return httpx.Response(200, json={"success": True, "currency": "EUR", "data": {}})
    with pytest.raises(SchemaError) as e:
        keyed(tmp_path, h).sales(["a"])
    assert KEY not in str(e.value)
    assert list((tmp_path / "q").glob("*.json"))


def test_store_dedup_and_empty_view(tmp_path):
    store = Store(tmp_path)
    assert store.db.execute("SELECT count(*) FROM sales").fetchone()[0] == 0
    s = Sale("a", T0, 10.0, "RUB")
    store.write_sales({"a": [s, Sale("a", T0 - timedelta(hours=1), 11.0, "RUB")]}, "mac")
    path = store.write_sales({"a": [s]}, "gha")
    assert path.parent.name.startswith("day=")
    assert store.db.execute("SELECT count(*), min(currency) FROM sales").fetchone() == (2, "RUB")


def seed(store, name, span_h, fetched_at, n=100):
    # n sales spread evenly over span_h hours, last one at fetched_at
    sales = [Sale(name, fetched_at - timedelta(hours=span_h * i / (n - 1)), 10.0 + i, "RUB") for i in range(n)]
    import cs2trader.store as st

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fetched_at
    orig, st.datetime = st.datetime, Frozen
    try:
        store.write_sales({name: sales}, name.replace(" ", "_"))
    finally:
        st.datetime = orig


def due_at(store, offset_h, s=McSettings()):
    return sales_due(store, T0 + timedelta(hours=offset_h), s)


def test_tiers(tmp_path):
    store = Store(tmp_path)
    seed(store, "ten_hours", 10, T0)
    seed(store, "thirty_days", 30 * 24, T0)
    # 10 h x 0.5 = 5 h -> rounds down to the 1 h tier (tiers 1/6/24)
    assert due_at(store, 0.99) == []
    assert due_at(store, 1) == ["ten_hours"]
    assert "ten_hours" in due_at(store, 5)
    # 720 h x 0.5 = 360 h -> 24 h tier
    assert due_at(store, 23.9) == ["ten_hours"]
    assert due_at(store, 24) == ["ten_hours", "thirty_days"] or due_at(store, 24) == ["thirty_days", "ten_hours"]
    # with tiers [1, 5, 24] the 10 h item is due exactly after 5 h
    s5 = McSettings(sales_tiers_h=[1, 5, 24])
    assert due_at(store, 4.9, s5) == []
    assert due_at(store, 5, s5) == ["ten_hours"]


def test_never_fetched_from_latest_asks(tmp_path):
    store = Store(tmp_path)
    seed(store, "have", 10, T0)
    store.write_snapshot(Snapshot("asks", T0, T0, "mac", [AskRow("have", 1.0, 1), AskRow("new", 1.0, 1)]))
    assert due_at(store, 0) == ["new"]


def test_no_data_no_due(tmp_path):
    assert due_at(Store(tmp_path), 0) == []


def test_zero_sale_item_waits_for_largest_tier(tmp_path):
    from datetime import UTC, datetime, timedelta

    from cs2trader.collect import sales_due
    from cs2trader.config import McSettings
    from cs2trader.models import AskRow, Snapshot
    from cs2trader.store import Store

    store = Store(tmp_path)
    now = datetime.now(UTC)
    store.write_snapshot(Snapshot("asks", now, now, "mac", [AskRow("Quiet Item", 1.0, 3)]))
    assert sales_due(store, now, McSettings()) == ["Quiet Item"]
    store.write_sales({"Quiet Item": []}, "mac")
    store = Store(tmp_path)
    assert sales_due(store, now + timedelta(hours=23), McSettings()) == []
    assert sales_due(store, now + timedelta(hours=25), McSettings()) == ["Quiet Item"]
