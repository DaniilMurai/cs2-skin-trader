import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from cs2trader.models import CostModel, FillModel, RunResult, RunSpec


def _iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def spec_to_dict(spec: RunSpec) -> dict:
    d = asdict(spec)
    d["start"], d["end"] = spec.start.isoformat(), spec.end.isoformat()
    d["costs"] = [{**asdict(c), "valid_from": c.valid_from.isoformat()} for c in spec.costs]
    return d


def save_run(result: RunResult, runs_dir: Path) -> Path:
    out = runs_dir / result.run_id
    out.mkdir(parents=True, exist_ok=True)
    result.trades.write_parquet(out / "trades.parquet")
    result.equity.write_parquet(out / "equity.parquet")
    meta = {"run_id": result.run_id, "data_version": result.data_version,
            "cashout_available": _iso(result.cashout_available), "spec": spec_to_dict(result.spec)}
    (out / "spec.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return out


def load_spec(path: Path) -> RunSpec:
    d = json.loads(path.read_text())["spec"]
    return RunSpec(
        d["strategy"], d["params"], d["capital"], date.fromisoformat(d["start"]),
        date.fromisoformat(d["end"]), d["freq"],
        tuple(CostModel(**{**c, "valid_from": date.fromisoformat(c["valid_from"])}) for c in d["costs"]),
        FillModel(**d["fills"]),
    )
