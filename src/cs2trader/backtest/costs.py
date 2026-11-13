import math
from typing import Literal

from cs2trader.models import CostModel

# 0.29 / 0.001 is 289.99999999999994 in floats; the epsilon keeps a price that is on the grid on it.
_EPS = 1e-9


def _ticks(amount: float, step: float, up: bool) -> float:
    if step <= 0:
        return amount
    n = amount / step
    return round((math.ceil(n - _EPS) if up else math.floor(n + _EPS)) * step, 9)


def round_price(price: float, cost: CostModel, side: Literal["buy", "sell"]) -> float:
    # A modelled price (Steam price x ratio x spread) falls between two listable prices.
    # Rounding against ourselves on both sides can only overstate the cost, never hide it.
    return _ticks(price, cost.price_step, up=side == "buy")


def tradable(price: float, cost: CostModel) -> bool:
    return price >= cost.min_price - _EPS


def unit_fee(price: float, rate: float, cost: CostModel) -> float:
    """Fee for one item. Every item is its own sale, so the minimum applies per item, not per order."""
    if rate <= 0:
        return 0.0
    return max(_ticks(price * rate, cost.price_step, up=True), cost.min_fee)


def sale_proceeds(price: float, cost: CostModel) -> float:
    """What one item brings when sold at a model price; 0.0 when the sale cannot happen or pays nothing."""
    price = round_price(price, cost, "sell")
    if not tradable(price, cost):
        return 0.0
    return max(price - unit_fee(price, cost.seller_fee, cost), 0.0)
