import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.sources.marketcsgo import _quarantine

URL = "https://api.skinport.com/v1/items"


def parse_items(body: Any, tradable: bool) -> list[dict[str, Any]]:
    try:
        if not isinstance(body, list):
            raise ValueError("body is not a list")
        rows = []
        for it in body:
            # min_price is null when the item is out of stock; those rows carry no price to compare.
            if it["min_price"] is None:
                continue
            rows.append({
                "market_hash_name": it["market_hash_name"],
                "version": it.get("version"),
                "tradable": tradable,
                "min_price": float(it["min_price"]),
                "median_price": None if it.get("median_price") is None else float(it["median_price"]),
                "quantity": int(it["quantity"]),
            })
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError("skinport", f"items: {type(e).__name__}: {e}") from e
    return rows


class SkinportPublic:
    """Public /v1/items, USD. Limit is 8 requests per 5 min and responses are cached 5 min (docs.skinport.com/items)."""

    def __init__(self, quarantine_dir: Path = Path("data/quarantine/skinport"), max_retries: int = 2,
                 backoff_s: float = 30.0, client: httpx.Client | None = None) -> None:
        self._quarantine_dir = quarantine_dir
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        # The API answers 406 without `br` in Accept-Encoding; httpx decodes it when brotli is installed.
        self._client = client or httpx.Client(
            timeout=60, headers={"User-Agent": "cs2trader", "Accept-Encoding": "br"})

    def items(self, tradable: bool) -> tuple[datetime, list[dict[str, Any]]]:
        fetched_at = datetime.now(UTC)
        text = self._get({"app_id": 730, "currency": "USD", "tradable": str(tradable).lower()})
        try:
            body = json.loads(text)
        except ValueError as e:
            _quarantine(self._quarantine_dir, text)
            raise SchemaError("skinport", "items: body is not JSON") from e
        try:
            return fetched_at, parse_items(body, tradable)
        except SchemaError:
            _quarantine(self._quarantine_dir, text)
            raise

    def _get(self, params: dict[str, Any]) -> str:
        last = "no attempt"
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(self._backoff_s * attempt)
            try:
                r = self._client.get(URL, params=params)
            except httpx.HTTPError as e:
                last = type(e).__name__
                continue
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code != 429 and r.status_code < 500:
                raise SourceUnavailable("skinport", last)
        raise SourceUnavailable("skinport", f"{last} after {self._max_retries} retries")
