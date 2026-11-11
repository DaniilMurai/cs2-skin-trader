import hashlib
from pathlib import Path

import polars as pl

_REL = Path("docs") / "pipeline" / "events.csv"


def _locate() -> Path:
    # A non-editable install has no repo next to the package; the working directory is the repo root there.
    if (Path.cwd() / _REL).exists():
        return Path.cwd() / _REL
    return Path(__file__).resolve().parents[3] / _REL


EVENTS_CSV = _locate()
TYPES = ("major_start", "major_end", "case_release", "case_to_rare_pool", "case_discontinued", "update")


def events_hash(path: Path = EVENTS_CSV) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "none"


def load_events(path: Path = EVENTS_CSV) -> pl.DataFrame:
    return pl.read_csv(path, schema={"date": pl.Date, "type": pl.String, "name": pl.String,
                                     "source_url": pl.String}).sort("date", "type", "name")


def majors(events: pl.DataFrame) -> pl.DataFrame:
    s = events.filter(pl.col("type") == "major_start").select("name", pl.col("date").alias("start"))
    e = events.filter(pl.col("type") == "major_end").select("name", pl.col("date").alias("end"))
    return s.join(e, on="name").sort("start")
