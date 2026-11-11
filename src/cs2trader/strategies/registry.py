from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from cs2trader.strategies.base import Strategy
from cs2trader.strategies.benchmarks import (
    BuyAndHoldKnivesGloves,
    BuyAndHoldUniverse,
    Cash,
)
from cs2trader.strategies.event import EVENT_GRID, EVENT_SPEC, EventStrategy
from cs2trader.strategies.lowturn import (
    CASEDIP_GRID,
    CASEDIP_SPEC,
    CHEAP_GRID,
    CHEAP_SPEC,
    QUALITY_GRID,
    QUALITY_SPEC,
    CaseDip,
    CheapReversion,
    QualityBasket,
)
from cs2trader.strategies.mean_reversion import MR_GRID, MR_SPEC, MeanReversion
from cs2trader.strategies.momentum import XS_GRID, XS_SPEC, XsMomentum
from cs2trader.strategies.trend import TREND_GRID, TREND_SPEC, TrendFollow
from cs2trader.strategies.video import (
    V1_GRID,
    V1_SPEC,
    V2_GRID,
    V2_SPEC,
    CategoryMomentum,
    DipKnives,
)


@dataclass(frozen=True)
class StrategyEntry:
    name: str
    family: Literal["video", "trend", "momentum", "mean_reversion", "event", "low_turnover", "benchmark"]
    factory: Callable[[Mapping[str, Any]], Strategy]
    grid: Mapping[str, Sequence[Any]]
    spec_md: str


SPEC_BH = (
    "On the first decision day with a non-empty target set, take the point-in-time universe{scope}. "
    "Estimate each item's fill price as its latest price in the last 7 days x (1 + spread 3%) x "
    "(1 + safety margin 2%). Hold an equal-dollar basket of up to 25 items: the largest "
    "n <= 25 for which at least n items cost no more than budget / n; the n are picked by a stable hash "
    "of the item name. Target "
    "quantity = floor(share / estimated price). Orders fill on the next bar and the volume cap "
    "cancels the rest, so for the following 10 decision days the still-missing target quantities are "
    "re-ordered (limited by cash at the estimated price). Never sells; after the retry window the "
    "leftover cash stays idle."
)

REGISTRY: Mapping[str, StrategyEntry] = {
    "cash": StrategyEntry(
        "cash", "benchmark", Cash, {},
        "Hold the starting balance in cash for the whole period; never trade. Equity is flat.",
    ),
    "bh_universe": StrategyEntry(
        "bh_universe", "benchmark", BuyAndHoldUniverse, {}, SPEC_BH.format(scope=""),
    ),
    "bh_knives_gloves": StrategyEntry(
        "bh_knives_gloves", "benchmark", BuyAndHoldKnivesGloves, {},
        SPEC_BH.format(scope=" restricted to items in the knife or glove categories"),
    ),
    "v1_dip_knives": StrategyEntry("v1_dip_knives", "video", DipKnives, V1_GRID, V1_SPEC),
    "v2_category_momentum": StrategyEntry("v2_category_momentum", "video", CategoryMomentum, V2_GRID, V2_SPEC),
    "xs_momentum": StrategyEntry("xs_momentum", "momentum", XsMomentum, XS_GRID, XS_SPEC),
    "event": StrategyEntry("event", "event", EventStrategy, EVENT_GRID, EVENT_SPEC),
    "trend_ma": StrategyEntry("trend_ma", "trend", TrendFollow, TREND_GRID, TREND_SPEC),
    "mean_reversion": StrategyEntry("mean_reversion", "mean_reversion", MeanReversion, MR_GRID, MR_SPEC),
    "quality_basket": StrategyEntry("quality_basket", "low_turnover", QualityBasket, QUALITY_GRID, QUALITY_SPEC),
    "case_dip": StrategyEntry("case_dip", "low_turnover", CaseDip, CASEDIP_GRID, CASEDIP_SPEC),
    "cheap_reversion": StrategyEntry("cheap_reversion", "mean_reversion", CheapReversion, CHEAP_GRID, CHEAP_SPEC),
}
