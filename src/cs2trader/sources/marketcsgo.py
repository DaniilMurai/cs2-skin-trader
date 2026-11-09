import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.models import AskRow, BidRow, Snapshot

BASE = "https://market.csgo.com/api/v2/prices"


def _quarantine(directory: Path, raw: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    path.write_text(raw, encoding="utf-8")
    return path


def parse_feed(
    kind: Literal["asks", "bids"], body: Any
) -> tuple[datetime, list[AskRow] | list[BidRow]]:
    try:
        if not isinstance(body, dict) or body["success"] is not True:
            raise ValueError("success is not true")
        if body["currency"] != "USD":
            raise ValueError(f"currency {body['currency']!r}")
        feed_time = datetime.fromtimestamp(int(body["time"]), UTC)
        rows: list[Any] = []
        for it in body["items"]:
            name, volume, price = it["market_hash_name"], int(it["volume"]), it["price"]
            if kind == "asks":
                if not isinstance(price, str):
                    raise ValueError("ask price is not a string")
                rows.append(AskRow(name, float(price), volume))
            else:
                if isinstance(price, bool) or not isinstance(price, (int, float)):
                    raise ValueError("bid price is not a number")
                rows.append(BidRow(name, float(price), volume))
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError("mc_public", f"{kind}: {type(e).__name__}: {e}") from e
    return feed_time, rows


class MarketCsgoPublic:
    def __init__(
        self,
        host: Literal["gha", "mac"] = "mac",
        quarantine_dir: Path = Path("data/quarantine/mc_public"),
        max_retries: int = 3,
        backoff_s: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._host = host
        self._quarantine_dir = quarantine_dir
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._client = client or httpx.Client(timeout=60, headers={"User-Agent": "cs2trader"})

    def asks(self) -> Snapshot:
        return self._snapshot("asks", f"{BASE}/USD.json")

    def bids(self) -> Snapshot:
        return self._snapshot("bids", f"{BASE}/orders/USD.json")

    def _snapshot(self, kind: Literal["asks", "bids"], url: str) -> Snapshot:
        fetched_at = datetime.now(UTC)
        text = self._get(url)
        try:
            body = json.loads(text)
        except ValueError as e:
            _quarantine(self._quarantine_dir, text)
            raise SchemaError("mc_public", f"{kind}: body is not JSON") from e
        try:
            feed_time, rows = parse_feed(kind, body)
        except SchemaError:
            _quarantine(self._quarantine_dir, text)
            raise
        return Snapshot(kind, feed_time, fetched_at, self._host, rows)

    def _get(self, url: str) -> str:
        last = "no attempt"
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(self._backoff_s * 2 ** (attempt - 1))
            try:
                r = self._client.get(url)
            except httpx.HTTPError as e:
                last = type(e).__name__
                continue
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code != 429 and r.status_code < 500:
                raise SourceUnavailable("mc_public", last)
        # 429 exhausted: RateLimited per the Errors table would escalate to SourceUnavailable
        raise SourceUnavailable("mc_public", f"{last} after {self._max_retries} retries")
