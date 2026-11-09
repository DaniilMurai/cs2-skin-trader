from collections.abc import Mapping
from typing import Any, Protocol

from cs2trader.models import Order


class Strategy(Protocol):
    name: str
    params: Mapping[str, Any]

    def decide(self, market: Any, book: Any) -> list[Order]: ...
