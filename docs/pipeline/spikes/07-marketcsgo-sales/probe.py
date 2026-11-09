"""S-07 throwaway probe: market.csgo keyed read-only endpoints. Never prints the key or full URLs.

Docs warn: more than 5 requests/s deletes the key, so requests are spaced >= 2 s.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
KEY = next(
    l.split("=", 1)[1].strip()
    for l in (ROOT / ".env").read_text().splitlines()
    if l.startswith("MARKETCSGO_API_KEY=")
)
BASE = "https://market.csgo.com/api/v2/"


def call(method, params):
    q = urllib.parse.urlencode([("key", KEY), *params])
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(BASE + method + "?" + q, timeout=60) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    time.sleep(2.2)
    return status, body, time.monotonic() - t0


def ts(x):
    return datetime.fromtimestamp(int(x), timezone.utc).strftime("%Y-%m-%d %H:%M")


def summarize_history(name, d):
    h = d.get("history") or []
    if not h:
        return {"name": name, "n": 0, "keys": list(d)}
    times = sorted(int(t) for t, _ in h)
    prices = [float(p) for _, p in h]
    span_h = (times[-1] - times[0]) / 3600
    return {
        "name": name,
        "n": len(h),
        "oldest": ts(times[0]),
        "newest": ts(times[-1]),
        "span_days": round(span_h / 24, 1),
        "sales_per_day": round(len(h) / max(span_h / 24, 1e-9), 1),
        "median": statistics.median(prices),
        "min": d.get("min"),
        "max": d.get("max"),
        "average": d.get("average"),
    }


if __name__ == "__main__":
    names = json.loads(Path(sys.argv[1]).read_text())
    batch = int(sys.argv[2])
    rows = []
    for i in range(0, len(names), batch):
        chunk = names[i : i + batch]
        status, body, dt = call("get-list-items-info", [("list_hash_name[]", n) for n in chunk])
        print(f"get-list-items-info batch={len(chunk)} status={status} {dt:.1f}s {len(body)}B", flush=True)
        (OUT / f"info_{i}.json").write_bytes(body)
        try:
            data = json.loads(body)
        except ValueError:
            print(body[:200]); break
        if not data.get("success"):
            print(json.dumps(data)[:300]); break
        for n in chunk:
            d = data["data"].get(n)
            row = summarize_history(n, d) if d else {"name": n, "missing": True}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (OUT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    for n in names[:2]:
        status, body, dt = call("bid-ask", [("hash_name", n)])
        d = json.loads(body) if status == 200 else {}
        print("bid-ask", status, n, "bids", len(d.get("bid", [])), "asks", len(d.get("ask", [])),
              "best", (d.get("bid") or [{}])[0].get("price"), (d.get("ask") or [{}])[0].get("price"))
