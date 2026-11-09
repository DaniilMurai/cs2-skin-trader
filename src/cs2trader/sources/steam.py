import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable, SteamAuthError
from cs2trader.models import SteamPoint

URL = "https://steamcommunity.com/market/pricehistory/"
SUFFIX_CURRENCY = {"₴": "UAH"}


CANARY = "Clutch Case"


class EmptyHistory(Exception):
    pass


def parse_history(name: str, body: dict[str, Any]) -> list[SteamPoint]:
    if not body.get("success") or not body.get("prices"):
        raise SteamAuthError("steam", f"empty history for {name!r}: cookie expired")
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
    def __init__(self, cookie: str, interval_s: float, max_retries: int, backoff_s: float = 60.0,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 on_request: Callable[[datetime, int], None] | None = None) -> None:
        self._cookie = cookie
        self._interval_s = interval_s
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._http = client or httpx.Client(timeout=30)
        self._sleep = sleep
        self._on_request = on_request
        self._last = -float("inf")
        self._authed = False

    def _get(self, market_hash_name: str) -> httpx.Response:
        wait = self._interval_s - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        try:
            r = self._http.get(
                URL,
                params={"appid": 730, "market_hash_name": market_hash_name, "currency": 1},
                headers={"User-Agent": "Mozilla/5.0 (research script)",
                         "Cookie": f"steamLoginSecure={self._cookie}"},
            )
        except httpx.HTTPError as e:
            if self._on_request:
                self._on_request(datetime.now(UTC), 0)
            raise SourceUnavailable("steam", type(e).__name__) from e
        if self._on_request:
            self._on_request(datetime.now(UTC), r.status_code)
        return r

    def price_history(self, market_hash_name: str) -> list[SteamPoint]:
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            try:
                r = self._get(market_hash_name)
            except SourceUnavailable:
                if last_try:
                    raise
                self._sleep(self._backoff_s * 2**attempt)
                continue
            body = _json(r)
            # An unknown name gives HTTP 500 with success:false and prices:false (checked 2026-11-09).
            if r.status_code == 500 and isinstance(body, dict) and body.get("prices") is False:
                raise EmptyHistory(market_hash_name)
            # Without a cookie Steam answers 429 from the first request (S-01); after a success
            # in this session a 429 is throttling instead.
            if r.status_code == 429 and not self._authed:
                raise SteamAuthError("steam", "HTTP 429 on the first request: cookie missing or rejected")
            if r.status_code == 429 or r.status_code >= 500:
                if last_try:
                    raise SourceUnavailable("steam", f"HTTP {r.status_code} after {attempt + 1} tries")
                self._sleep(self._backoff_s * 2**attempt)
                continue
            if r.status_code != 200:
                raise SourceUnavailable("steam", f"HTTP {r.status_code}")
            if not isinstance(body, dict) or not body:
                raise SteamAuthError("steam", "empty body: cookie expired")
            # A never-sold item and an expired cookie both give success with an empty `prices`
            # (checked 2026-11-09); a liquid canary item tells them apart.
            if body.get("success") and body.get("prices") == [] and market_hash_name != CANARY:
                self.price_history(CANARY)
                raise EmptyHistory(market_hash_name)
            points = parse_history(market_hash_name, body)
            self._authed = True
            return points
        raise AssertionError("unreachable")


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None
