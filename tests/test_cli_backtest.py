import hashlib
import subprocess
import sys
from datetime import date

import pytest
from test_lookahead import FakeStore, synthetic_bars

from cs2trader.backtest.engine import run
from cs2trader.backtest.runs import load_spec, save_run
from cs2trader.cli import parse_params
from cs2trader.config import load_settings
from cs2trader.errors import ConfigError
from cs2trader.models import CostModel, FillModel, RunSpec


def _spec() -> RunSpec:
    return RunSpec("bh_universe", {}, 1000.0, date(2024, 1, 1), date(2024, 3, 1),
                   costs=(CostModel(valid_from=date(2013, 1, 1)),
                          CostModel(valid_from=date(2024, 2, 1), seller_fee=0.1)),
                   fills=FillModel(ratio={"knife": 0.8}))


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_same_spec_same_run_id_and_bytes(tmp_path):
    store = FakeStore(synthetic_bars(1, 90))
    settings = load_settings()
    r1, r2 = run(_spec(), store, settings), run(_spec(), store, settings)
    assert r1.run_id == r2.run_id
    a, b = save_run(r1, tmp_path / "a"), save_run(r2, tmp_path / "b")
    for f in ("trades.parquet", "equity.parquet", "spec.json"):
        assert _sha(a / f) == _sha(b / f)


def test_spec_json_round_trips(tmp_path):
    result = run(_spec(), FakeStore(synthetic_bars(1, 90)), load_settings())
    out = save_run(result, tmp_path)
    assert load_spec(out / "spec.json") == _spec()


def test_params_validation():
    assert parse_params("bh_universe", "{}") == {}
    with pytest.raises(ConfigError, match="unknown key"):
        parse_params("bh_universe", '{"x": 1}')
    with pytest.raises(ConfigError, match="bh_universe"):
        parse_params("nope", "{}")
    with pytest.raises(ConfigError):
        parse_params("bh_universe", "[1]")


def test_unknown_param_exits_2():
    r = subprocess.run([sys.executable, "-c", "from cs2trader.cli import main; main()", "backtest", "--strategy", "bh_universe",
                        "--capital", "100", "--from", "2024-01-01", "--to", "2024-02-01",
                        "--params", '{"x": 1}'], capture_output=True, text=True)
    assert r.returncode == 2, r.stderr
    assert "ConfigError" in r.stderr
