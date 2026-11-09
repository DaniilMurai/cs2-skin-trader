import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx

from cs2trader.errors import SchemaError, SteamAuthError, SourceUnavailable
from cs2trader.models import SteamPoint

URL = "https://steamcommunity.com/market/pricehistory/"
SUFFIX_CURRENCY = {"₴": "UAH"}


def parse_history(name: str, body: dict[str, Any]) -> list[SteamPoint]:
    if not body.get("success") or not body.get("prices"):
        raise SteamAuthError("steam", f"empty history for {name!r}: cookie expired or item unknown")
    currency = SUFFIX_CURRENCY.get(body.get("price_suffix", ""))
    if currency is None:
        raise SchemaError("steam", f"unexpected price_suffix {body.get('price_suffix')!r}")
    rows = []
    for stamp, price, volume in body["prices"]:
        # "Feb 21 2014 01: +0" — the trailing "+0" is the UTC offset
        ts = datetime.strptime(stamp[:14], "%b %d %Y %H").replace(tzinfo=UTC)
        rows.append((ts, float(price), int(volume)))
    per_day = Counter(ts.date() for ts, _, _ in rows)
    return [
        SteamPoint(name, ts, price, currency, volume,
                   "hour" if per_day[ts.date()] > 1 else "day")
        for ts, price, volume in rows
    ]


class SteamClient:
    def __init__(self, cookie: str, interval_s: float, max_retries: int) -> None:
        self._cookie = cookie
        self._interval_s = interval_s
        self._max_retries = max_retries
        self._last = 0.0

    def price_history(self, market_hash_name: str) -> list[SteamPoint]:
        wait = self._interval_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            r = httpx.get(
                URL,
                params={"appid": 730, "market_hash_name": market_hash_name, "currency": 1},
                headers={"User-Agent": "Mozilla/5.0 (research script)",
                         "Cookie": f"steamLoginSecure={self._cookie}"},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise SourceUnavailable("steam", type(e).__name__) from e
        if r.status_code == 429:
            raise SteamAuthError("steam", "HTTP 429: cookie missing or rejected")
        if r.status_code != 200:
            raise SourceUnavailable("steam", f"HTTP {r.status_code}")
        body = r.json()
        if body in ([], {}):
            raise SteamAuthError("steam", "empty body: cookie expired")
        return parse_history(market_hash_name, body)
