"""Frozen benchmark metadata over the production routing cost model."""

from dataclasses import dataclass

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.routing.model import Aircraft, Cost, Solution, Track, Trip, reverse, variants
from geoplan.routing.model import CostModel as RoutingCostModel


@dataclass(frozen=True)
class Problem:
    case_id: str
    tracks: tuple[Track, ...]
    aircraft: tuple[Aircraft, ...]
    baseline: Solution
    maximum_internal_distance_km: float | None
    wind: WindCondition | None = None
    exclusions: tuple[NoFlyZone, ...] = ()
    criterion: OptimizationCriterion = OptimizationCriterion.TOTAL_FLIGHT_TIME
    deadline_minutes: float | None = None
    source: str = "synthetic"


class CostModel(RoutingCostModel[Problem]):
    pass


__all__ = [
    "Aircraft",
    "Cost",
    "CostModel",
    "Problem",
    "Solution",
    "Track",
    "Trip",
    "reverse",
    "variants",
]
