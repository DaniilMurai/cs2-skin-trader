import os
import secrets
from pathlib import Path

from cs2trader.config import _read_dotenv

SECRET_NAMES = ("STEAM_LOGIN_SECURE", "MARKETCSGO_API_KEY")
TOKEN_HEADER = "X-CS2-Token"
BIND_HOST = "127.0.0.1"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def secret_values(env_path: Path = Path(".env")) -> list[str]:
    dotenv = _read_dotenv(env_path)
    values = [os.environ.get(n) or dotenv.get(n) or "" for n in SECRET_NAMES]
    return [v for v in values if len(v) >= 8]


def scrub(body: bytes, values: list[str]) -> bytes:
    for v in values:
        body = body.replace(v.encode(), b"[redacted]")
    return body


def host_allowed(host_header: str | None, port: int) -> bool:
    # A DNS-rebinding page reaches this server under its own host name; only literal loopback names pass.
    return host_header in (f"127.0.0.1:{port}", f"localhost:{port}")


def token_ok(sent: str | None, expected: str) -> bool:
    return bool(sent) and secrets.compare_digest(sent, expected)
