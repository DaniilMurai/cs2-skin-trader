import time, sys, httpx, numpy as np, polars as pl, duckdb, scipy.stats as st, pyarrow as pa
t=time.time()
def lap(n):
    global t; print(f"{n}: {time.time()-t:.3f}s"); t=time.time()
print(sys.version)
n=1_000_000; rng=np.random.default_rng(0)
df=pl.DataFrame({"item":rng.integers(0,1000,n),"ts":np.arange(n),"price":rng.random(n)*100}); lap("polars build")
df.write_parquet("t.parquet"); lap("write parquet")
r=duckdb.sql("select item, avg(price) a, count(*) c, avg(avg(price)) over (order by item rows between 2 preceding and current row) w from 't.parquet' group by item order by item limit 3").pl(); print(r); lap("duckdb query")
print(st.pearsonr(df["ts"].to_numpy()[:1000], df["price"].to_numpy()[:1000])); lap("scipy")
print("httpx", httpx.__version__, "pyarrow", pa.__version__, "duckdb", duckdb.__version__, "polars", pl.__version__, "numpy", np.__version__)
