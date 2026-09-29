from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from shapely.geometry import GeometryCollection, LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from src.curvature import SweepTransitionPlanner, build_curvature_aware_sweep_route_with_planner
from src.geometry import LocalProjection, polygon_from_coordinates, projection_for_coordinates
from src.routing import RouteGeometryError
from src.schemas import (
    Coordinate,
    SectionRouteOptimizationRequest,
    SectionRouteVariant,
    SweepRouteCurvatureRequest,
    SweepRouteCurvatureResult,
    TurnAwareSweepRequest,
    TurnAwareSweepResult,
)
from src.section_optimizer import ExactSectionRouteOptimizer

MAXIMUM_UNCOVERED_AREA_M2 = 1.0
OBSTACLE_ENDPOINT_DISTANCE_M = 2.0
RETREAT_RADIUS_FACTORS = (1.0, 1.5, 2.0)
MAXIMUM_REORDERED_BLOCKS = 10


@dataclass(frozen=True)
class TurnAwareGeometry:
    projection: LocalProjection
    flyable: BaseGeometry
    blocked: BaseGeometry


@dataclass(frozen=True)
class EvaluatedSweep:
    route: SweepRouteCurvatureResult
    retreat_m: float
    uncovered_area_m2: float


def build_turn_aware_sweep_route(
    request: TurnAwareSweepRequest,
    transition_planner: SweepTransitionPlanner,
) -> TurnAwareSweepResult:
    geometry = _prepare_geometry(request)
    original_coverage = _survey_coverage(request.route, request, geometry.projection)
    original_uncovered = geometry.flyable.difference(original_coverage).area
    if original_uncovered > MAXIMUM_UNCOVERED_AREA_M2:
        raise RouteGeometryError("Исходные галсы не обеспечивают полного покрытия целевой зоны.")
    candidate_routes = _candidate_routes(request, geometry)
    feasible: list[EvaluatedSweep] = []
    for route, retreat_m in candidate_routes:
        coverage = _survey_coverage(route, request, geometry.projection)
        uncovered_area_m2 = geometry.flyable.difference(coverage).area
        if uncovered_area_m2 > MAXIMUM_UNCOVERED_AREA_M2:
            continue
        curvature_request = SweepRouteCurvatureRequest(
            route=route,
            coverage_area=request.coverage_area,
            no_fly_zones=request.no_fly_zones,
            minimum_turn_radius_m=request.minimum_turn_radius_m,
            sampling_step_m=request.sampling_step_m,
        )
        try:
            curved = build_curvature_aware_sweep_route_with_planner(
                curvature_request,
                transition_planner,
            )
        except RouteGeometryError:
            continue
        feasible.append(
            EvaluatedSweep(
                route=curved,
                retreat_m=retreat_m,
                uncovered_area_m2=uncovered_area_m2,
            )
        )
    if not feasible:
        raise RouteGeometryError(
            "Покрытие и безопасные развороты одновременно обеспечить не удалось."
        )
    selected = min(
        feasible,
        key=lambda candidate: (candidate.route.distance_km, candidate.retreat_m),
    )
    return TurnAwareSweepResult(
        route=selected.route.route,
        survey_legs=selected.route.survey_legs,
        distance_km=selected.route.distance_km,
        transition_distance_km=selected.route.transition_distance_km,
        endpoint_retreat_m=selected.retreat_m,
        uncovered_area_m2=selected.uncovered_area_m2,
    )


def _prepare_geometry(request: TurnAwareSweepRequest) -> TurnAwareGeometry:
    coordinates = [
        *request.coverage_area.coordinates,
        *(point for ring in request.coverage_area.interior_rings for point in ring),
        *(point for zone in request.no_fly_zones for point in zone.coordinates),
        *(point for zone in request.no_fly_zones for ring in zone.interior_rings for point in ring),
        *request.route,
    ]
    projection = projection_for_coordinates(coordinates)
    target = polygon_from_coordinates(
        request.coverage_area.coordinates,
        request.coverage_area.interior_rings,
        projection,
    )
    blocked_parts = [
        polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
            zone.safety_margin_m,
            join_style="mitre",
        )
        for zone in request.no_fly_zones
    ]
    blocked: BaseGeometry = unary_union(blocked_parts) if blocked_parts else GeometryCollection()
    return TurnAwareGeometry(
        projection=projection,
        flyable=target.difference(blocked),
        blocked=blocked,
    )


def _candidate_routes(
    request: TurnAwareSweepRequest,
    geometry: TurnAwareGeometry,
) -> list[tuple[list[Coordinate], float]]:
    candidates = [(request.route, 0.0)]
    if geometry.blocked.is_empty:
        return candidates
    for factor in RETREAT_RADIUS_FACTORS:
        retreat_m = request.minimum_turn_radius_m * factor
        route = _retreat_obstacle_endpoints(request.route, retreat_m, geometry)
        if route is not None and route != request.route:
            candidates.append((route, retreat_m))
    source_route, source_retreat_m = candidates[-1]
    reordered = _reorder_obstacle_blocks(source_route, request, geometry)
    if reordered is not None:
        candidates.insert(0, (reordered, source_retreat_m))
    return candidates


def _reorder_obstacle_blocks(
    route: list[Coordinate],
    request: TurnAwareSweepRequest,
    geometry: TurnAwareGeometry,
) -> list[Coordinate] | None:
    blocks = _sweep_blocks(route, request, geometry)
    if not 2 <= len(blocks) <= MAXIMUM_REORDERED_BLOCKS:
        return None
    groups = [
        [
            SectionRouteVariant(
                section_id=f"block-{index}",
                variant_id=orientation,
                route=oriented,
                survey_legs=[
                    oriented[offset : offset + 2] for offset in range(0, len(oriented), 2)
                ],
            )
            for orientation, oriented in (("forward", block), ("reverse", list(reversed(block))))
        ]
        for index, block in enumerate(blocks)
    ]
    optimization_request = SectionRouteOptimizationRequest(
        variant_groups=groups,
        coverage_area=request.coverage_area,
        no_fly_zones=request.no_fly_zones,
    )
    try:
        optimized = ExactSectionRouteOptimizer().optimize(optimization_request)
    except RouteGeometryError:
        return None
    reordered = [
        coordinate
        for selection in optimized.selections
        for coordinate in groups[int(selection.section_id.removeprefix("block-"))][
            0 if selection.variant_id == "forward" else 1
        ].route
    ]
    return reordered if reordered != route else None


def _sweep_blocks(
    route: list[Coordinate],
    request: TurnAwareSweepRequest,
    geometry: TurnAwareGeometry,
) -> list[list[Coordinate]]:
    blocks: list[list[Coordinate]] = []
    start = 0
    maximum_short_connector_m = max(request.strip_width_m * 2, request.minimum_turn_radius_m * 4)
    for index in range(2, len(route), 2):
        source = geometry.projection.project(route[index - 1])
        target = geometry.projection.project(route[index])
        connector = LineString([source, target])
        crosses_obstacle = connector.relate(geometry.blocked)[0] != "F"
        if connector.length <= maximum_short_connector_m and not crosses_obstacle:
            continue
        blocks.append(route[start:index])
        start = index
    blocks.append(route[start:])
    return blocks


def _retreat_obstacle_endpoints(
    route: list[Coordinate],
    retreat_m: float,
    geometry: TurnAwareGeometry,
) -> list[Coordinate] | None:
    adjusted: list[Coordinate] = []
    for index in range(0, len(route), 2):
        start = geometry.projection.project(route[index])
        end = geometry.projection.project(route[index + 1])
        distance_m = hypot(end[0] - start[0], end[1] - start[1])
        if distance_m <= 0:
            return None
        start_retreat = (
            retreat_m
            if geometry.blocked.distance(Point(start)) <= OBSTACLE_ENDPOINT_DISTANCE_M
            else 0.0
        )
        end_retreat = (
            retreat_m
            if geometry.blocked.distance(Point(end)) <= OBSTACLE_ENDPOINT_DISTANCE_M
            else 0.0
        )
        if start_retreat + end_retreat >= distance_m - 1:
            return None
        adjusted.append(
            _move_along_leg(start, end, start_retreat / distance_m, geometry.projection)
        )
        adjusted.append(
            _move_along_leg(start, end, 1 - end_retreat / distance_m, geometry.projection)
        )
    return adjusted


def _move_along_leg(
    start: tuple[float, float],
    end: tuple[float, float],
    fraction: float,
    projection: LocalProjection,
) -> Coordinate:
    return projection.unproject(
        start[0] + (end[0] - start[0]) * fraction,
        start[1] + (end[1] - start[1]) * fraction,
    )


def _survey_coverage(
    route: list[Coordinate],
    request: TurnAwareSweepRequest,
    projection: LocalProjection,
) -> BaseGeometry:
    footprints: list[BaseGeometry] = []
    for index in range(0, len(route), 2):
        start = projection.project(route[index])
        end = projection.project(route[index + 1])
        distance_m = hypot(end[0] - start[0], end[1] - start[1])
        if distance_m <= 0:
            raise RouteGeometryError("Съёмочный галс имеет нулевую длину.")
        horizontal = (end[0] - start[0]) / distance_m
        vertical = (end[1] - start[1]) / distance_m
        half_height = request.footprint_height_m / 2
        extended = LineString(
            [
                (start[0] - horizontal * half_height, start[1] - vertical * half_height),
                (end[0] + horizontal * half_height, end[1] + vertical * half_height),
            ]
        )
        footprints.append(extended.buffer(request.strip_width_m / 2, cap_style="flat"))
    return unary_union(footprints)
