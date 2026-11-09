from typing import Literal

SourceName = Literal["steam", "mc_public", "mc_keyed", "nbu", "cbr", "items_db"]


class Cs2Error(Exception):
    exit_code = 1


class ConfigError(Cs2Error):
    exit_code = 2


class SourceError(Cs2Error):
    exit_code = 4

    def __init__(self, source: SourceName, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class SteamAuthError(SourceError):
    exit_code = 3


class MarketCsgoKeyError(SourceError):
    exit_code = 3


class RateLimited(SourceError):
    pass


class SourceUnavailable(SourceError):
    pass


class SchemaError(SourceError):
    exit_code = 5


class FxMissing(Cs2Error):
    pass


class LookAheadError(Cs2Error):
    pass


class InvariantViolation(Cs2Error):
    pass
