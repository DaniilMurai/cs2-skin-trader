import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.sources.marketcsgo import _quarantine

URL = "https://api.waxpeer.com/v1/prices"


def parse_prices(body: Any) -> list[dict[str, Any]]:
    try:
        if not isinstance(body, dict) or body["success"] is not True:
            raise ValueError("success is not true")
        # `min` is in thousandths of a dollar (checked against market.csgo asks, S-12).
        return [{"market_hash_name": it["name"], "min_price": it["min"] / 1000, "quantity": int(it["count"])}
                for it in body["items"] if it["min"] is not None]
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError("waxpeer", f"prices: {type(e).__name__}: {e}") from e


class WaxpeerPublic:
    """Keyless GET /v1/prices (docs.waxpeer.com); one request per run."""

    def __init__(self, quarantine_dir: Path = Path("data/quarantine/waxpeer"), max_retries: int = 2,
                 backoff_s: float = 30.0, client: httpx.Client | None = None) -> None:
        self._quarantine_dir = quarantine_dir
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._client = client or httpx.Client(timeout=60, headers={"User-Agent": "cs2trader"})

    def prices(self) -> tuple[datetime, list[dict[str, Any]]]:
        fetched_at = datetime.now(UTC)
        text = self._get()
        try:
            body = json.loads(text)
        except ValueError as e:
            _quarantine(self._quarantine_dir, text)
            raise SchemaError("waxpeer", "prices: body is not JSON") from e
        try:
            return fetched_at, parse_prices(body)
        except SchemaError:
            _quarantine(self._quarantine_dir, text)
            raise

    def _get(self) -> str:
        last = "no attempt"
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(self._backoff_s * attempt)
            try:
                r = self._client.get(URL, params={"game": "csgo", "minified": 1})
            except httpx.HTTPError as e:
                last = type(e).__name__
                continue
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code != 429 and r.status_code < 500:
                raise SourceUnavailable("waxpeer", last)
        raise SourceUnavailable("waxpeer", f"{last} after {self._max_retries} retries")
