from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from src.dubins import DubinsPlanner, ExactDubinsPlanner
from src.hybrid_astar import HybridAStarPlanner
from src.routing import (
    RouteGeometryError,
    route_bearing_degrees,
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_coverage_area,
)
from src.schemas import (
    Coordinate,
    DubinsPathRequest,
    DubinsPathResult,
    DubinsPathType,
    DubinsPose,
    HybridAStarPathRequest,
    SweepRouteCurvatureRequest,
    SweepRouteCurvatureResult,
)


@dataclass(frozen=True)
class PlannedSweepTransition:
    route: list[Coordinate]
    distance_km: float
    path_type: DubinsPathType | None


class SweepTransitionPlanner(Protocol):
    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition: ...


@dataclass(frozen=True)
class DubinsSweepTransitionPlanner:
    planner: DubinsPlanner

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition:
        result = _shortest_clear_dubins_transition(source, target, request, self.planner)
        return PlannedSweepTransition(
            route=result.route,
            distance_km=result.distance_km,
            path_type=result.path_type,
        )


@dataclass(frozen=True)
class HybridAStarSweepTransitionPlanner:
    planner: HybridAStarPlanner
    primitive_length_m: float
    grid_resolution_m: float
    heading_bin_degrees: float
    analytic_expansion_distance_m: float
    maximum_expansions: int

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition:
        path_request = HybridAStarPathRequest(
            start=DubinsPose(
                coordinate=source[-1],
                heading_degrees=route_bearing_degrees(source[0], source[-1]),
            ),
            end=DubinsPose(
                coordinate=target[0],
                heading_degrees=route_bearing_degrees(target[0], target[-1]),
            ),
            no_fly_zones=request.no_fly_zones,
            minimum_turn_radius_m=request.minimum_turn_radius_m,
            primitive_length_m=self.primitive_length_m,
            sampling_step_m=request.sampling_step_m,
            grid_resolution_m=self.grid_resolution_m,
            heading_bin_degrees=self.heading_bin_degrees,
            analytic_expansion_distance_m=self.analytic_expansion_distance_m,
            maximum_expansions=self.maximum_expansions,
        )
        try:
            result = self.planner.plan(path_request)
        except RuntimeError as error:
            raise RouteGeometryError("Между галсами отсутствует Hybrid A*-переход.") from error
        return PlannedSweepTransition(
            route=result.route,
            distance_km=result.distance_km,
            path_type=None,
        )


def build_curvature_aware_sweep_route(
    request: SweepRouteCurvatureRequest,
) -> SweepRouteCurvatureResult:
    return build_curvature_aware_sweep_route_with_planner(
        request,
        DubinsSweepTransitionPlanner(planner=ExactDubinsPlanner()),
    )


def build_curvature_aware_sweep_route_with_planner(
    request: SweepRouteCurvatureRequest,
    transition_planner: SweepTransitionPlanner,
) -> SweepRouteCurvatureResult:
    survey_legs = [request.route[index : index + 2] for index in range(0, len(request.route), 2)]
    _validate_survey_legs(survey_legs, request)
    route = list(survey_legs[0])
    transitions: list[PlannedSweepTransition] = []
    for source, target in pairwise(survey_legs):
        transition = transition_planner.connect(source, target, request)
        transitions.append(transition)
        route = _join_routes([route, transition.route, target])
    if not route_is_clear_of_no_fly_zones(route, request.no_fly_zones):
        raise RouteGeometryError("Криволинейная змейка пересекает запретную зону.")
    return SweepRouteCurvatureResult(
        route=route,
        survey_legs=survey_legs,
        transition_path_types=[
            transition.path_type for transition in transitions if transition.path_type is not None
        ],
        distance_km=route_distance_km(route),
        transition_distance_km=sum(transition.distance_km for transition in transitions),
    )


def _validate_survey_legs(
    survey_legs: list[list[Coordinate]],
    request: SweepRouteCurvatureRequest,
) -> None:
    for leg in survey_legs:
        if not route_is_inside_coverage_area(leg, request.coverage_area):
            raise RouteGeometryError("Съёмочный галс выходит за пределы целевой зоны.")
        if not route_is_clear_of_no_fly_zones(leg, request.no_fly_zones):
            raise RouteGeometryError("Съёмочный галс пересекает запретную зону.")


def _shortest_clear_dubins_transition(
    source: list[Coordinate],
    target: list[Coordinate],
    request: SweepRouteCurvatureRequest,
    planner: DubinsPlanner,
) -> DubinsPathResult:
    path_request = DubinsPathRequest(
        start=DubinsPose(
            coordinate=source[-1],
            heading_degrees=route_bearing_degrees(source[0], source[-1]),
        ),
        end=DubinsPose(
            coordinate=target[0],
            heading_degrees=route_bearing_degrees(target[0], target[-1]),
        ),
        minimum_turn_radius_m=request.minimum_turn_radius_m,
        sampling_step_m=request.sampling_step_m,
    )
    if not request.no_fly_zones:
        try:
            return planner.plan(path_request)
        except RuntimeError as error:
            raise RouteGeometryError("Между галсами отсутствует Dubins-переход.") from error
    try:
        candidates = planner.plan_candidates(path_request)
    except RuntimeError as error:
        raise RouteGeometryError("Между галсами отсутствует Dubins-переход.") from error
    clear = [
        candidate
        for candidate in candidates
        if route_is_clear_of_no_fly_zones(candidate.route, request.no_fly_zones)
    ]
    if not clear:
        raise RouteGeometryError("Допустимые Dubins-переходы между галсами отсутствуют.")
    return min(clear, key=lambda candidate: (candidate.distance_km, candidate.path_type.value))


def _join_routes(routes: list[list[Coordinate]]) -> list[Coordinate]:
    combined: list[Coordinate] = []
    for route in routes:
        if combined and combined[-1] == route[0]:
            combined.extend(route[1:])
        else:
            combined.extend(route)
    return combined
