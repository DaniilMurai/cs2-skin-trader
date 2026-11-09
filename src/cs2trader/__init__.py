import os

# Measured 2026-11-09 on `cs2 report`: polars' thread pool on many small per-bar frames costs 6x CPU
# in system time; one thread ran 2m50s → 83 s with an identical results hash. Override per run if needed.
os.environ.setdefault("POLARS_MAX_THREADS", "1")
