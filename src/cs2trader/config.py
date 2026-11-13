import os
import tomllib
import typing
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal

from cs2trader.errors import ConfigError
from cs2trader.models import CostModel

SecretName = Literal["STEAM_LOGIN_SECURE", "MARKETCSGO_API_KEY", "CF_WORKER_URL", "CF_WORKER_TOKEN"]


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def optional_secret(name: SecretName) -> str | None:
    return os.environ.get(name) or _read_dotenv(Path(".env")).get(name) or None


def require_secret(name: SecretName) -> str:
    value = os.environ.get(name) or _read_dotenv(Path(".env")).get(name)
    if not value:
        raise ConfigError(f"{name} is not set (environment or .env)")
    return value


@dataclass(frozen=True)
class SteamSettings:
    interval_s: float = 2.5
    max_retries: int = 3
    backoff_s: float = 60.0
    stop_after_errors: int = 10


@dataclass(frozen=True)
class BackfillSettings:
    universe: str = "feed"


@dataclass(frozen=True)
class McSettings:
    interval_s: float = 2.5
    sales_batch: int = 20
    # 20 names x (600 s / 2.5 s) = 4800 names = 240 requests: one run stays within ~10 minutes
    sales_max_names_per_run: int = 4800
    sales_refill_share: float = 0.5
    sales_tiers_h: list[int] = field(default_factory=lambda: [1, 6, 24])
    data_repo: str = ""


@dataclass(frozen=True)
class UniverseSettings:
    min_history_days: int = 30
    min_median_volume: int = 5
    min_price_usd: float = 0.10
    # 0 = no upper bound; small-budget runs set it so items dearer than the budget drop out
    max_price_usd: float = 0.0


@dataclass(frozen=True)
class RegimesSettings:
    lookback_days: int = 30
    threshold: float = 0.05


@dataclass(frozen=True)
class RatioSettings:
    min_days: int = 14
    min_items: int = 20


@dataclass(frozen=True)
class FillsSettings:
    participation_cap: float = 0.20
    # Share of an item's median daily volume one position may reach; 0 = off, AC-19(d) asks for 0.05
    position_cap: float = 0.0
    spread_penalty: float = 0.03
    ratio_source: Literal["measured", "fixed"] = "fixed"
    ratio_fallback: float = 1.0


@dataclass(frozen=True)
class WfSettings:
    start: date = date(2019, 1, 1)
    end: date = date(2025, 9, 30)
    train_days: int = 365
    test_days: int = 91
    holdout_start: date = date(2025, 10, 1)


@dataclass(frozen=True)
class VerdictSettings:
    cost_stress: float = 1.5
    spread_stress: float = 2.0


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    steam: SteamSettings = SteamSettings()
    backfill: BackfillSettings = BackfillSettings()
    mc: McSettings = McSettings()
    universe: UniverseSettings = UniverseSettings()
    regimes: RegimesSettings = RegimesSettings()
    ratio: RatioSettings = RatioSettings()
    fills: FillsSettings = FillsSettings()
    costs: CostModel = CostModel(valid_from=date(2013, 1, 1))
    wf: WfSettings = WfSettings()
    verdict: VerdictSettings = VerdictSettings()
    capitals: list[float] = field(default_factory=lambda: [100.0, 1000.0])


def _coerce(value: Any, tp: Any, key: str) -> Any:
    origin = typing.get_origin(tp)
    if origin is Literal:
        if value in typing.get_args(tp):
            return value
    elif origin is list:
        if isinstance(value, list):
            return [_coerce(v, typing.get_args(tp)[0], key) for v in value]
    elif tp is bool:
        if isinstance(value, bool):
            return value
    elif tp is float:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    elif tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif tp is str:
        if isinstance(value, str):
            return value
    elif tp is Path:
        if isinstance(value, str | Path):
            return Path(value)
    elif tp is date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    raise ConfigError(f"config key {key!r}: invalid value {value!r} for type {tp}")


def _flatten(table: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in table.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _apply(obj: Any, parts: list[str], value: Any, key: str) -> Any:
    hints = typing.get_type_hints(type(obj))
    names = {f.name for f in fields(obj)}
    if parts[0] not in names:
        raise ConfigError(f"unknown config key {key!r}")
    current = getattr(obj, parts[0])
    if is_dataclass(current):
        if len(parts) == 1:
            raise ConfigError(f"config key {key!r} is a group, set its fields instead")
        return replace(obj, **{parts[0]: _apply(current, parts[1:], value, key)})
    if len(parts) > 1:
        raise ConfigError(f"unknown config key {key!r}")
    return replace(obj, **{parts[0]: _coerce(value, hints[parts[0]], key)})


def load_settings(path: Path = Path("cs2.toml"), **cli_overrides: Any) -> Settings:
    flat: dict[str, Any] = {}
    if path.exists():
        try:
            flat = _flatten(tomllib.loads(path.read_text()))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    flat.update(cli_overrides)
    settings = Settings()
    for key, value in flat.items():
        settings = _apply(settings, key.split("."), value, key)
    return settings
