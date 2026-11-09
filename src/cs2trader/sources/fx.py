from datetime import date, datetime, timedelta
from xml.etree import ElementTree
from typing import Literal

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.models import FxRate

NBU_URL = "https://bank.gov.ua/NBU_Exchange/exchange_site"
CBR_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp"
CBR_USD = "R01235"
# longest CBR gap is the New Year holidays (~10 days); the first day of a range needs an earlier record
CBR_LOOKBACK_DAYS = 14


def fetch_rates(base: Literal["UAH", "RUB"], start: date, end: date) -> list[FxRate]:
    if base == "RUB":
        return _fetch_cbr(start, end)
    try:
        r = httpx.get(NBU_URL, params={
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "valcode": "usd", "sort": "exchangedate", "order": "asc", "json": "",
        }, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceUnavailable("nbu", type(e).__name__) from e
    try:
        return [
            FxRate(datetime.strptime(row["exchangedate"], "%d.%m.%Y").date(), "UAH",
                   float(row["rate_per_unit"]), "nbu", False)
            for row in r.json()
        ]
    except (KeyError, ValueError, TypeError) as e:
        raise SchemaError("nbu", f"unexpected NBU payload: {e}") from e


def _fetch_cbr(start: date, end: date) -> list[FxRate]:
    first = start - timedelta(days=CBR_LOOKBACK_DAYS)
    try:
        r = httpx.get(CBR_URL, params={
            "date_req1": first.strftime("%d/%m/%Y"), "date_req2": end.strftime("%d/%m/%Y"),
            "VAL_NM_RQ": CBR_USD,
        }, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceUnavailable("cbr", type(e).__name__) from e
    return parse_cbr(r.content, start, end)


def parse_cbr(content: bytes, start: date, end: date) -> list[FxRate]:
    try:
        records = {}
        for rec in ElementTree.fromstring(content).iter("Record"):
            day = datetime.strptime(rec.attrib["Date"], "%d.%m.%Y").date()
            nominal = float(rec.findtext("Nominal").replace(",", "."))
            records[day] = float(rec.findtext("Value").replace(",", ".")) / nominal
    except (ElementTree.ParseError, KeyError, ValueError, TypeError, AttributeError, ZeroDivisionError) as e:
        raise SchemaError("cbr", f"unexpected CBR payload: {e}") from e
    days = sorted(records)
    out: list[FxRate] = []
    last: float | None = None
    d = days[0] if days else start
    d = min(d, start)
    while d <= end:
        if d in records:
            last, filled = records[d], False
        else:
            filled = True
        if d >= start:
            if last is None:
                raise SchemaError("cbr", f"no CBR record on or before {d}")
            out.append(FxRate(d, "RUB", last, "cbr", filled))
        d += timedelta(days=1)
    return out
