"""S-01 throwaway probe of Steam pricehistory. Never prints the cookie."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

cookie = next(
    line.split("=", 1)[1].strip()
    for line in (ROOT / ".env").read_text().splitlines()
    if line.startswith("STEAM_LOGIN_SECURE=")
)
UA = "Mozilla/5.0 (research script)"


def fetch(name: str, with_cookie: bool = True):
    url = "https://steamcommunity.com/market/pricehistory/?" + urllib.parse.urlencode(
        {"appid": 730, "market_hash_name": name, "currency": 1}
    )
    headers = {"User-Agent": UA}
    if with_cookie:
        headers["Cookie"] = f"steamLoginSecure={cookie}"
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = r.read()
            return r.status, body, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - t0


def describe(name: str, body: bytes):
    data = json.loads(body)
    prices = data.get("prices") or []
    if not prices:
        return {"name": name, "points": 0, "raw_keys": list(data)}
    dates = [p[0] for p in prices]
    hours = Counter(d.split(": ")[1].split(" ")[0] if ": " in d else d[-6:] for d in dates)
    return {
        "name": name,
        "points": len(prices),
        "first": dates[0],
        "last": dates[-1],
        "sample_first": prices[:2],
        "sample_last": prices[-3:],
        "hour_tokens_top": hours.most_common(5),
        "keys": list(data),
        "price_prefix": data.get("price_prefix"),
    }


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "basic":
        name = "AK-47 | Redline (Field-Tested)"
        for wc in (False, True):
            status, body, dt = fetch(name, wc)
            print("cookie" if wc else "no-cookie", status, f"{dt:.2f}s", len(body))
            if status == 200:
                (OUT / f"redline_{'c' if wc else 'nc'}.json").write_bytes(body)
                print(json.dumps(describe(name, body), ensure_ascii=False))
    elif mode == "rate":
        names = json.loads(Path(sys.argv[2]).read_text())
        gap = float(sys.argv[3])
        log = []
        for i, n in enumerate(names):
            status, body, dt = fetch(n)
            row = {"i": i, "t": round(time.time(), 1), "status": status, "dt": round(dt, 2)}
            if status == 200:
                d = describe(n, body)
                row.update(points=d["points"], first=d.get("first"))
                (OUT / f"item_{i}.json").write_bytes(body)
            log.append(row | {"name": n})
            print(json.dumps(row | {"name": n}, ensure_ascii=False), flush=True)
            if status == 429:
                break
            time.sleep(gap)
        (OUT / f"rate_{gap}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
