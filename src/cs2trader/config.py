import os
from pathlib import Path
from typing import Literal

from cs2trader.errors import ConfigError

SecretName = Literal["STEAM_LOGIN_SECURE", "MARKETCSGO_API_KEY"]


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


def require_secret(name: SecretName) -> str:
    value = os.environ.get(name) or _read_dotenv(Path(".env")).get(name)
    if not value:
        raise ConfigError(f"{name} is not set (environment or .env)")
    return value
