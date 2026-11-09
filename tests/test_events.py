import polars as pl

from cs2trader.market.events import TYPES, load_events, majors


def test_event_list_is_well_formed():
    ev = load_events()
    assert set(ev["type"]) <= set(TYPES)
    assert ev.select("date", "type", "name").is_duplicated().sum() == 0
    assert ev["source_url"].str.starts_with("https://").all()


def test_majors_have_start_before_end():
    m = majors(load_events())
    assert m.height >= 25
    assert (m["start"] <= m["end"]).all()
    assert (m["end"] - m["start"]).dt.total_days().max() <= 30


def test_case_lifecycle_is_ordered():
    ev = load_events().filter(pl.col("type").str.starts_with("case_"))
    wide = ev.pivot("type", index="name", values="date")
    has_rare = wide.filter(pl.col("case_to_rare_pool").is_not_null())
    assert (has_rare["case_release"] < has_rare["case_to_rare_pool"]).all()
    ended = wide.filter(pl.col("case_discontinued").is_not_null())
    assert (ended["case_release"] < ended["case_discontinued"]).all()
