"""Settings editor: which keys the UI may change, validation, and the cs2.toml writer."""
import json
import os
import tempfile
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from cs2trader.config import Settings, _apply, load_settings
from cs2trader.errors import ConfigError

# key -> (group, minimum, maximum) for numbers; None = no numeric range. Everything not listed here
# (secrets, paths, collector and Steam settings, the verdict thresholds) is not writable from the UI.
EDITABLE: dict[str, tuple[str, float | None, float | None]] = {
    "costs.seller_fee": ("Costs and fills", 0.0, 0.5),
    "costs.buyer_fee": ("Costs and fills", 0.0, 0.5),
    "costs.withdraw_fee_cash": ("Costs and fills", 0.0, 0.5),
    "costs.withdraw_fee_steam": ("Costs and fills", 0.0, 0.5),
    "costs.lock_days": ("Costs and fills", 0, 60),
    "costs.cashout_hold_days": ("Costs and fills", 0, 60),
    "fills.participation_cap": ("Costs and fills", 0.001, 1.0),
    "fills.spread_penalty": ("Costs and fills", 0.0, 0.5),
    "fills.ratio_source": ("Costs and fills", None, None),
    "fills.ratio_fallback": ("Costs and fills", 0.1, 3.0),
    "universe.min_history_days": ("Regime and universe", 1, 3650),
    "universe.min_median_volume": ("Regime and universe", 0, 100000),
    "universe.min_price_usd": ("Regime and universe", 0.0, 1000.0),
    "regimes.lookback_days": ("Regime and universe", 2, 365),
    "regimes.threshold": ("Regime and universe", 0.001, 1.0),
    "wf.start": ("Report windows", None, None),
    "wf.end": ("Report windows", None, None),
    "wf.train_days": ("Report windows", 30, 3650),
    "wf.test_days": ("Report windows", 7, 730),
    "wf.holdout_start": ("Report windows", None, None),
    "capitals": ("Report windows", None, None),
}
READ_ONLY = {"verdict.cost_stress": "AC-12: the verdict rule is fixed in advance",
             "verdict.spread_stress": "AC-12: the verdict rule is fixed in advance"}
HOLDOUT_WARNING = ("Changing the holdout start means the holdout is no longer the untouched period the "
                   "verdict was designed around; results made with another start are not comparable.")


def _get(obj: Any, key: str) -> Any:
    for part in key.split("."):
        obj = getattr(obj, part)
    return obj


def describe(settings: Settings) -> dict:
    groups: dict[str, list[dict]] = {}
    for key, (group, lo, hi) in EDITABLE.items():
        value = _get(settings, key)
        groups.setdefault(group, []).append(
            {"key": key, "value": value.isoformat() if isinstance(value, date) else value,
             "min": lo, "max": hi, "kind": type(value).__name__})
    return {"groups": groups,
            "read_only": [{"key": k, "value": _get(settings, k), "why": why} for k, why in READ_ONLY.items()],
            "holdout_warning": HOLDOUT_WARNING}


def validate(settings: Settings, changes: dict[str, Any]) -> tuple[Settings, list[str]]:
    errors: list[str] = []
    new = settings
    for key, value in changes.items():
        if key not in EDITABLE:
            errors.append(f"{key}: not editable here")
            continue
        _, lo, hi = EDITABLE[key]
        try:
            new = _apply(new, key.split("."), value, key)
        except ConfigError as e:
            errors.append(str(e))
            continue
        current = _get(new, key)
        if key == "capitals":
            if not current or any(c <= 0 for c in current):
                errors.append("capitals: a non-empty list of positive numbers")
        elif lo is not None and isinstance(current, int | float) and not lo <= current <= hi:
            errors.append(f"{key}: must be between {lo} and {hi}")
    if not errors:
        wf = new.wf
        if wf.start >= wf.end:
            errors.append("wf.start must be before wf.end")
        if wf.holdout_start <= wf.end:
            errors.append("wf.holdout_start must be after wf.end (the holdout comes after tuning)")
    return new, errors


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise ConfigError(f"cannot write {v!r} to TOML")


def dump_toml(table: dict[str, Any]) -> str:
    lines = ["# Overrides for built-in defaults (contracts.md, Configuration). Rewritten by `cs2 ui`; "
             "the previous file is cs2.toml.bak. No secrets here."]
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    lines += [f"{k} = {_toml_value(v)}" for k, v in scalars.items()]
    for name, sub in table.items():
        if isinstance(sub, dict):
            lines += ["", f"[{name}]"] + [f"{k} = {_toml_value(v)}" for k, v in sub.items()]
    return "\n".join(lines) + "\n"


def save(path: Path, settings: Settings, changes: dict[str, Any]) -> tuple[bool, list[str], Settings]:
    new, errors = validate(settings, changes)
    if errors:
        return False, errors, settings
    current = tomllib.loads(path.read_text()) if path.exists() else {}
    for key, value in changes.items():
        value = _get(new, key)
        parts = key.split(".")
        node = current
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    text = dump_toml(current)
    tomllib.loads(text)  # what is written must parse back
    if path.exists():
        path.with_suffix(".toml.bak").write_text(path.read_text())
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cs2toml-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return True, [], load_settings(path)


def settings_changed_note() -> str:
    return "Saved. Results made under other settings have a different run_id."


def defaults_path(data_dir: Path) -> Path:
    return data_dir / "ui" / "strategy_params.json"


def load_strategy_defaults(data_dir: Path) -> dict[str, dict]:
    p = defaults_path(data_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def save_strategy_defaults(data_dir: Path, strategy: str, params: dict) -> list[str]:
    from cs2trader.cli import parse_params

    try:
        parse_params(strategy, json.dumps(params))
    except ConfigError as e:
        return [str(e)]
    p = defaults_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_strategy_defaults(data_dir)
    data[strategy] = params
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return []

