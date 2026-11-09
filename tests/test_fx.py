import re
from datetime import date
from pathlib import Path

import pytest

from cs2trader.errors import SchemaError
from cs2trader.sources.fx import parse_cbr

XML = (Path(__file__).parent / "fixtures" / "cbr.xml").read_bytes()


def by_day(rates):
    return {r.day: r for r in rates}


def test_gap_free_and_forward_filled():
    rates = parse_cbr(XML, date(2024, 9, 14), date(2024, 9, 24))
    assert [r.day.day for r in rates] == list(range(14, 25))
    d = by_day(rates)
    assert d[date(2024, 9, 14)].filled is False
    assert d[date(2024, 9, 15)].filled and d[date(2024, 9, 15)].per_usd == pytest.approx(90.9345)
    assert d[date(2024, 9, 22)].per_usd == pytest.approx(92.5848)
    assert d[date(2024, 9, 24)].filled is False
    assert {r.base for r in rates} == {"RUB"} and {r.source for r in rates} == {"cbr"}


def test_saturday_gets_friday_rate():
    xml = re.sub(rb'<Record Date="14\.09\.2024".*?</Record>', b"", XML)
    d = by_day(parse_cbr(xml, date(2024, 9, 14), date(2024, 9, 17)))
    assert d[date(2024, 9, 14)].filled is True
    assert d[date(2024, 9, 14)].per_usd == pytest.approx(91.1096)


def test_range_starting_on_gap_day_uses_earlier_record():
    d = by_day(parse_cbr(XML, date(2024, 9, 15), date(2024, 9, 16)))
    assert list(d) == [date(2024, 9, 15), date(2024, 9, 16)]
    assert all(r.filled and r.per_usd == pytest.approx(90.9345) for r in d.values())


def test_nominal_divides_value():
    xml = XML.replace(b"<Nominal>1</Nominal><Value>91,1096", b"<Nominal>10</Nominal><Value>911,096", 1)
    d = by_day(parse_cbr(xml, date(2024, 9, 13), date(2024, 9, 13)))
    assert d[date(2024, 9, 13)].per_usd == pytest.approx(91.1096)


def test_no_earlier_record_is_schema_error():
    with pytest.raises(SchemaError):
        parse_cbr(XML, date(2024, 9, 1), date(2024, 9, 14))


@pytest.mark.parametrize("payload", [b"<html>not xml", b"<ValCurs><Record Date='x'/></ValCurs>",
                                     b'<ValCurs><Record Date="14.09.2024"><Nominal>1</Nominal><Value>abc</Value></Record></ValCurs>'])
def test_malformed_is_schema_error(payload):
    with pytest.raises(SchemaError) as e:
        parse_cbr(payload, date(2024, 9, 14), date(2024, 9, 14))
    assert e.value.source == "cbr"
