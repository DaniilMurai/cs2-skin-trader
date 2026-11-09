"""S-08 throwaway probe: is Steam pricehistory converted at historical or current FX rates?

A TF2 key is priced in USD on Steam (~$2.2-2.5 for a decade). If the UAH history were
converted at historical rates (UAH 8/USD in 2013, 45/USD in 2026) it would rise ~5x.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
cookie = next(l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text().splitlines()
              if l.startswith("STEAM_LOGIN_SECURE="))
url = "https://steamcommunity.com/market/pricehistory/?" + urllib.parse.urlencode(
    {"appid": 440, "market_hash_name": "Mann Co. Supply Crate Key", "currency": 1})
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research script)",
                                           "Cookie": f"steamLoginSecure={cookie}"})
d = json.load(urllib.request.urlopen(req, timeout=30))
print(d["price_suffix"], len(d["prices"]))
for p in d["prices"]:
    if p[0][:6] in ("Jan 01", "Jul 01"):
        print(p)
print(d["prices"][-1])
