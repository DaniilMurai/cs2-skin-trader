from datetime import date
from pathlib import Path

import pytest

from cs2trader.config import Settings, load_settings
from cs2trader.errors import ConfigError
from cs2trader.models import CostModel


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    s = load_settings(tmp_path / "nope.toml")
    assert s == Settings()
    assert s.steam.interval_s == 2.5
    assert s.costs.seller_fee == CostModel(valid_from=date(2013, 1, 1)).seller_fee
    assert s.wf.holdout_start == date(2025, 10, 1)


def test_file_then_cli_override(tmp_path: Path) -> None:
    f = tmp_path / "cs2.toml"
    f.write_text('[steam]\ninterval_s = 4\n[wf]\nstart = 2020-01-01\n')
    s = load_settings(f)
    assert s.steam.interval_s == 4.0 and s.steam.max_retries == 3
    assert s.wf.start == date(2020, 1, 1)
    s = load_settings(f, **{"steam.interval_s": 1.0, "costs.seller_fee": 0.1})
    assert s.steam.interval_s == 1.0
    assert s.costs.seller_fee == 0.1


def test_repo_cs2_toml_parses() -> None:
    load_settings(Path(__file__).parent.parent / "cs2.toml")


@pytest.mark.parametrize("key", ["steam.nope", "nope", "steam.interval_s.x", "steam"])
def test_unknown_key(key: str) -> None:
    with pytest.raises(ConfigError, match=key):
        load_settings(Path("missing.toml"), **{key: 1})


def test_unknown_key_in_file(tmp_path: Path) -> None:
    f = tmp_path / "cs2.toml"
    f.write_text("[mc]\nbogus = 1\n")
    with pytest.raises(ConfigError, match="mc.bogus"):
        load_settings(f)


@pytest.mark.parametrize("key,value", [
    ("steam.max_retries", 2.5), ("steam.interval_s", "fast"), ("steam.max_retries", True),
    ("fills.ratio_source", "other"), ("mc.sales_tiers_h", [1, "x"]), ("wf.start", "not-a-date"),
])
def test_wrong_type(key: str, value: object) -> None:
    with pytest.raises(ConfigError, match=key):
        load_settings(Path("missing.toml"), **{key: value})
