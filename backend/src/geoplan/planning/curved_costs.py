"""Heading-aware route-search costs; final missions still use the canonical builder."""

from dataclasses import dataclass
from math import inf

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.geo import Coordinate
from geoplan.flight.performance import route_duration_minutes
from geoplan.navigation.curvature import DubinsSweepTransitionPlanner
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.models import SweepRouteCurvatureRequest
from geoplan.navigation.routing import RouteGeometryError
from geoplan.planning.models import PlanRequest
from geoplan.routing.model import Aircraft


@dataclass(frozen=True)
class CurvedConnectionCost:
    request: PlanRequest
    exclusions: tuple[NoFlyZone, ...] = ()

    def cost(
        self,
        aircraft: Aircraft,
        source: tuple[Coordinate, Coordinate],
        target: tuple[Coordinate, Coordinate],
    ) -> tuple[float, float]:
        radius = aircraft.minimum_turn_radius_m
        query = SweepRouteCurvatureRequest(
            route=[*source, *target],
            coverage_area=self.request.coverage_areas[0],
            no_fly_zones=list(self.exclusions),
            minimum_turn_radius_m=radius,
            sampling_step_m=max(3, min(25, radius / 4)),
        )
        try:
            transition = DubinsSweepTransitionPlanner(ExactDubinsPlanner()).connect(
                list(source), list(target), query
            )
            return route_duration_minutes(
                transition.route, transition.distance_km, aircraft.speed_kmh, self.request.wind
            ), transition.distance_km
        except RouteGeometryError, ValueError:
            # This bounded heuristic excludes this arc; Hybrid A* in the final
            # builder may support additional connections. No infeasibility proof.
            return inf, inf
