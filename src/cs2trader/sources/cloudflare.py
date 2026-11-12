"""Imports the raw price feeds a Cloudflare Worker buffers in R2 (ops/cloudflare/) into the local
snapshot store, then deletes them from R2 so the bucket stays far below its free quota."""
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cs2trader.errors import SchemaError, SourceUnavailable
from cs2trader.models import Snapshot
from cs2trader.sources.marketcsgo import parse_feed
from cs2trader.sources.skinport import parse_items
from cs2trader.sources.waxpeer import parse_prices
from cs2trader.store import Store

KEY = re.compile(r"^raw/(asks|bids|waxpeer|skinport)/day=\d{4}-\d{2}-\d{2}/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z)\.json$")
DELETE_BATCH = 50


@dataclass(frozen=True)
class ImportReport:
    imported: int
    bad: int
    deleted: int


class CfBuffer:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=60)
        self._headers = {"X-CS2-Token": token}

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, self._base + path, headers=self._headers, **kw)
        except httpx.HTTPError as e:
            raise SourceUnavailable("cf_worker", type(e).__name__) from e
        if r.status_code != 200:
            raise SourceUnavailable("cf_worker", f"HTTP {r.status_code} on {path.split('?')[0]}")
        return r

    def keys(self) -> Iterator[str]:
        cursor = None
        while True:
            q = "?cursor=" + cursor if cursor else ""
            page = self._request("GET", "/list" + q).json()
            yield from (o["key"] for o in page["objects"])
            cursor = page["cursor"]
            if not cursor:
                return

    def get(self, key: str) -> bytes:
        return self._request("GET", "/get", params={"key": key}).content

    def delete(self, keys: list[str]) -> int:
        return self._request("POST", "/delete", json={"keys": keys}).json()["deleted"]


def _fetched_at(stamp: str) -> datetime:
    day, time = stamp.split("T")
    h, m, s, ms = time.rstrip("Z").split("-")
    return datetime.fromisoformat(f"{day}T{h}:{m}:{s}.{ms}+00:00").astimezone(UTC)


def import_raw(store: Store, buf: CfBuffer, quarantine: Path) -> ImportReport:
    """A file is deleted from R2 only after its Parquet snapshot exists locally (or after its body was
    saved to `quarantine` because it cannot be parsed, which no retry would fix)."""
    imported = bad = 0
    done: list[str] = []
    for key in sorted(buf.keys()):
        m = KEY.match(key)
        if not m:
            continue
        kind, stamp = m.group(1), m.group(2)
        raw = buf.get(key)
        try:
            body = json.loads(raw)
            if kind in ("asks", "bids"):
                feed_time, rows = parse_feed(kind, body)
                path = store.write_snapshot(Snapshot(kind, feed_time, _fetched_at(stamp), "cf", rows))
            elif kind == "waxpeer":
                path = store.write_waxpeer(_fetched_at(stamp), "cf", parse_prices(body))
            else:
                path = store.write_skinport(_fetched_at(stamp), "cf", parse_items(body, True))
        except (ValueError, SchemaError):
            quarantine.mkdir(parents=True, exist_ok=True)
            (quarantine / f"{stamp}-{kind}.json").write_bytes(raw)
            bad += 1
            done.append(key)
            continue
        if not path.exists():
            continue
        imported += 1
        done.append(key)
    deleted = sum(buf.delete(done[i:i + DELETE_BATCH]) for i in range(0, len(done), DELETE_BATCH))
    return ImportReport(imported, bad, deleted)
