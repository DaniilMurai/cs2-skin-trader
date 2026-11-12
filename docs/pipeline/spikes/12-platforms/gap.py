"""S-12: gross price gap Skinport min_price vs market.csgo ask, one snapshot (throwaway).
Run: uv run --with brotli python gap.py <skinport.br> <collector_dir> <YYYY-MM-DD>"""
import json
import sys

import brotli
import polars as pl

SKINPORT_FEE = 0.08
MARKET_FEE = 0.05

raw = json.loads(brotli.decompress(open(sys.argv[1], "rb").read()))
sp = pl.DataFrame([{"name": r["market_hash_name"], "sp": r["min_price"], "sp_qty": r["quantity"]}
                   for r in raw if r["min_price"] is not None and r.get("version") is None])
asks = (pl.read_parquet(f"{sys.argv[2]}/asks/day={sys.argv[3]}/*.parquet")
        .group_by("market_hash_name").agg(pl.col("ask").median().alias("ask"), pl.col("listings").median().alias("n")))
j = asks.join(sp, left_on="market_hash_name", right_on="name").filter(
    (pl.col("ask") >= 2) & (pl.col("n") >= 3) & (pl.col("sp_qty") >= 3))
j = j.with_columns(
    (pl.col("sp") * (1 - SKINPORT_FEE) / pl.col("ask") - 1).alias("buy_market_sell_skinport"),
    (pl.col("ask") * (1 - MARKET_FEE) / pl.col("sp") - 1).alias("buy_skinport_sell_market"))
q = [0.05, 0.25, 0.5, 0.75, 0.95]
print("skinport items", sp.height, "market items", asks.height, "joined+filtered", j.height)
for c in ("buy_market_sell_skinport", "buy_skinport_sell_market"):
    print(c, [round(j[c].quantile(x), 3) for x in q], "share>0:", round((j[c] > 0).mean(), 3),
          "share>5%:", round((j[c] > 0.05).mean(), 3))
print(j.sort("buy_market_sell_skinport", descending=True).head(8))
