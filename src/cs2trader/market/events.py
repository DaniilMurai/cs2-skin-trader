from pathlib import Path

import polars as pl

EVENTS_CSV = Path(__file__).resolve().parents[3] / "docs" / "pipeline" / "events.csv"
TYPES = ("major_start", "major_end", "case_release", "case_to_rare_pool", "case_discontinued", "update")


def load_events(path: Path = EVENTS_CSV) -> pl.DataFrame:
    return pl.read_csv(path, schema={"date": pl.Date, "type": pl.String, "name": pl.String,
                                     "source_url": pl.String}).sort("date", "type", "name")


def majors(events: pl.DataFrame) -> pl.DataFrame:
    s = events.filter(pl.col("type") == "major_start").select("name", pl.col("date").alias("start"))
    e = events.filter(pl.col("type") == "major_end").select("name", pl.col("date").alias("end"))
    return s.join(e, on="name").sort("start")
