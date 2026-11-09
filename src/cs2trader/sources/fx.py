from datetime import date, datetime
from typing import Literal

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.models import FxRate

NBU_URL = "https://bank.gov.ua/NBU_Exchange/exchange_site"


def fetch_rates(base: Literal["UAH", "RUB"], start: date, end: date) -> list[FxRate]:
    if base != "UAH":
        raise NotImplementedError("CBR (RUB) arrives with the collector task")
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
