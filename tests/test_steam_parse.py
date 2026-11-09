import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cs2trader.errors import SchemaError, SteamAuthError
from cs2trader.sources.steam import parse_history

FIXTURE = Path(__file__).parent / "fixtures" / "steam_pricehistory.json"


def test_parses_saved_response():
    points = parse_history("x", json.loads(FIXTURE.read_text()))
    assert points[0].ts == datetime(2025, 4, 1, 1, tzinfo=UTC)
    assert points[0].currency == "UAH"
    assert points[0].granularity == "day"
    assert points[-1].granularity == "hour"
    assert all(isinstance(p.volume, int) for p in points)


def test_unknown_currency_is_schema_error():
    with pytest.raises(SchemaError):
        parse_history("x", {"success": True, "price_suffix": "$", "prices": [["Feb 21 2014 01: +0", 1.0, "1"]]})


def test_empty_history_is_auth_error():
    with pytest.raises(SteamAuthError):
        parse_history("x", {"success": True, "prices": []})
