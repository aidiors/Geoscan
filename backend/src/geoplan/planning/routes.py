from __future__ import annotations

from math import hypot

from geoplan.coverage.models import WorkSegment
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    AltitudeReference,
    ErrorCode,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.navigation.connections import _safe_route
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.geometry import projection_for_coordinates
from geoplan.navigation.models import (
    RouteOptimizationRequest,
    RouteOptimizationResult,
    SweepRouteCurvatureRequest,
)
from geoplan.navigation.optimizer import RouteOptimizer
from geoplan.navigation.routing import (
    RouteGeometryError,
    route_is_clear_of_no_fly_zones,
    route_is_inside_allowed_airspaces,
    route_is_inside_coverage_area,
)
from geoplan.planning.constraints import _coverage_area
from geoplan.planning.models import PlanRequest


def _optimize_route(
    optimizer: RouteOptimizer,
    request: RouteOptimizationRequest,
    segment_id: str,
) -> RouteOptimizationResult:
    try:
        return optimizer.optimize(request)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Для участка {segment_id} не найден безопасный маршрут.",
        ) from error


def _departure_route(
    departure: Coordinate,
    survey_route: list[Coordinate],
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
) -> list[Coordinate]:
    if request.minimum_turn_radius_m == 0:
        return _safe_route(
            [departure, survey_route[0]],
            constraints,
            segment.segment_id,
        )
    if departure == survey_route[0]:
        return [departure, departure]
    source = [
        _coordinate_before(
            departure,
            survey_route[0],
            request.minimum_turn_radius_m,
        ),
        departure,
    ]
    return _curvature_constrained_connection(
        source,
        survey_route[:2],
        segment,
        request,
        constraints,
        transition_planner,
        phase_name="транзит к зоне съёмки",
    )


def _landing_route(
    survey_route: list[Coordinate],
    landing: Coordinate,
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
) -> list[Coordinate]:
    if request.minimum_turn_radius_m == 0:
        return _safe_route(
            [survey_route[-1], landing],
            constraints,
            segment.segment_id,
        )
    if survey_route[-1] == landing:
        return [landing, landing]
    target = [
        landing,
        _coordinate_beyond(
            survey_route[-1],
            landing,
            request.minimum_turn_radius_m,
        ),
    ]
    return _curvature_constrained_connection(
        survey_route[-2:],
        target,
        segment,
        request,
        constraints,
        transition_planner,
        phase_name="подход к посадке",
    )


def _curvature_constrained_connection(
    source: list[Coordinate],
    target: list[Coordinate],
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
    phase_name: str,
) -> list[Coordinate]:
    coverage_area = _coverage_area(request.coverage_areas, segment.coverage_area_id)
    if coverage_area is None:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=f"Для участка {segment.segment_id} не определена зона съёмки.",
        )
    transition_request = SweepRouteCurvatureRequest(
        route=target,
        coverage_area=coverage_area,
        no_fly_zones=constraints,
        minimum_turn_radius_m=request.minimum_turn_radius_m,
        sampling_step_m=max(3, min(25, request.minimum_turn_radius_m / 4)),
    )
    try:
        transition = transition_planner.connect(source, target, transition_request)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=(
                f"Для участка {segment.segment_id} не построен безопасный {phase_name}. "
                f"Радиус: {request.minimum_turn_radius_m:g} м."
            ),
        ) from error
    return transition.route


def _coordinate_before(
    start: Coordinate,
    end: Coordinate,
    distance_m: float,
) -> Coordinate:
    projection = projection_for_coordinates([start, end])
    start_x, start_y = projection.project(start)
    end_x, end_y = projection.project(end)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length = hypot(delta_x, delta_y)
    if length == 0:
        raise ValueError("Нельзя продолжить направление между совпадающими точками.")
    return projection.unproject(
        start_x - delta_x / length * distance_m,
        start_y - delta_y / length * distance_m,
    )


def _coordinate_beyond(
    start: Coordinate,
    end: Coordinate,
    distance_m: float,
) -> Coordinate:
    projection = projection_for_coordinates([start, end])
    start_x, start_y = projection.project(start)
    end_x, end_y = projection.project(end)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length = hypot(delta_x, delta_y)
    if length == 0:
        raise ValueError("Нельзя продолжить направление между совпадающими точками.")
    return projection.unproject(
        end_x + delta_x / length * distance_m,
        end_y + delta_y / length * distance_m,
    )


def _combine_routes(*routes: list[Coordinate]) -> list[Coordinate]:
    combined: list[Coordinate] = []
    for route in routes:
        for coordinate in route:
            if not combined or coordinate != combined[-1]:
                combined.append(coordinate)
    return combined


def _validate_geometry(
    full_route: list[Coordinate],
    survey_route: list[Coordinate],
    constraints: list[NoFlyZone],
    segment: WorkSegment,
    request: PlanRequest,
) -> None:
    if not route_is_clear_of_no_fly_zones(full_route, constraints):
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Маршрут участка {segment.segment_id} пересекает запретную зону.",
        )
    coverage_area = _coverage_area(request.coverage_areas, segment.coverage_area_id)
    if (
        coverage_area is not None
        and request.minimum_turn_radius_m == 0
        and not all(
            route_is_inside_coverage_area(track, coverage_area)
            for track in (
                [list(t) for t in segment.survey_tracks]
                if segment.survey_tracks is not None
                else [survey_route]
            )
        )
    ):
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Галсы участка {segment.segment_id} выходят за целевую зону.",
        )
    if not request.allowed_airspaces:
        return
    if request.altitude_reference is AltitudeReference.AGL:
        if not route_is_inside_allowed_airspaces(full_route, request.allowed_airspaces):
            raise PlanningError(
                code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
                message=f"Route for segment {segment.segment_id} leaves allowed airspace.",
            )
        return
    compatible = [
        airspace
        for airspace in request.allowed_airspaces
        if airspace.altitude_reference is request.altitude_reference
        and airspace.minimum_altitude_m <= request.mission_altitude_m <= airspace.maximum_altitude_m
    ]
    if not compatible:
        nearest_altitude = min(
            (
                min(
                    max(request.mission_altitude_m, airspace.minimum_altitude_m),
                    airspace.maximum_altitude_m,
                )
                for airspace in request.allowed_airspaces
            ),
            key=lambda altitude: abs(altitude - request.mission_altitude_m),
        )
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message="Высота задания не входит в разрешённое воздушное пространство.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                    message=(
                        "Измените высоту задания до ближайшего допустимого уровня "
                        f"{nearest_altitude:g} м."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="mission_altitude_m",
                    current_value=request.mission_altitude_m,
                    required_value=nearest_altitude,
                    unit="m",
                    affected_ids=[segment.segment_id],
                )
            ],
        )
    if not route_is_inside_allowed_airspaces(full_route, compatible):
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message=(
                f"Маршрут участка {segment.segment_id} выходит за границы "
                "разрешённого воздушного пространства."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
                    message=(
                        "Расширьте подтверждённый разрешённый коридор либо перенесите "
                        "площадки и рабочую область внутрь него."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="allowed_airspaces",
                    affected_ids=[segment.segment_id],
                )
            ],
        )
