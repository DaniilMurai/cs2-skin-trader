"""S-11: how far below Steam is market.csgo, in Steam-wallet terms? Read-only; run: uv run python edge.py <data_dir>"""
import sys
import duckdb

D = sys.argv[1]
c = duckdb.connect(f"{D}/cs2.duckdb", read_only=True)
A = f"read_parquet('{D}/collector/asks/*/*.parquet')"
# Steam price / 1.15 is what a Steam seller keeps; the edge is that over the market.csgo ask.
q = f"""
with a as (select market_hash_name n, day, median(ask) ask from {A} group by 1, 2),
sl as (select name n, median(price_usd) p, avg(volume) v, count(*) k from steam_points
       where ts >= (select max(ts) from steam_points) - interval 14 day group by 1),
j as (select a.n, (p / 1.15) / ask - 1 edge from a join sl on a.n = sl.n where p >= 2 and v >= 10 and k >= 7)
select count(*), count(distinct n), quantile_cont(edge, [0.05, 0.25, 0.5, 0.75, 0.95]) from j
"""
print(c.execute(q).fetchall())
