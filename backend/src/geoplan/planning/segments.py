from __future__ import annotations

from statistics import median

from pydantic import ValidationError

from geoplan.budget import check_planning_budget
from geoplan.coverage.models import CoverageArea, CoveragePlanRequest, WorkSegment
from geoplan.coverage.sweep import CoveragePlanner, CoveragePlanningError
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    CoverageAngleCost,
    ErrorCode,
    RecommendationCode,
    RecommendationConfidence,
    SweepDirectionMode,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.flight.sites import _configured_departure_sites, _configured_primary_landing_sites
from geoplan.navigation.curvature import (
    SweepTransitionPlanner,
    build_curvature_aware_sweep_route_with_planner,
)
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates
from geoplan.navigation.models import SweepRouteCurvatureRequest
from geoplan.navigation.routing import RouteGeometryError, route_distance_km
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.fleet_survey import FleetSurveyPolicy
from geoplan.planning.models import PlanRequest


def _resolve_segments(
    request: PlanRequest,
    coverage_planner: CoveragePlanner,
    coverage_reference_airspeed_kmh: float | None,
    drones: list[DroneSpec] | None = None,
) -> list[WorkSegment]:
    if not request.coverage_areas:
        return request.segments
    parameters = request.survey_parameters
    if parameters is None:
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message="Для построения галсов отсутствуют параметры съёмки.",
        )
    invalid = []
    for area in request.coverage_areas:
        check_planning_budget()
        try:
            geometry = polygon_from_coordinates(
                area.coordinates, area.interior_rings, projection_for_coordinates(area.coordinates)
            )
            if geometry.area <= 0:
                invalid.append(area.area_id)
        except ValueError:
            invalid.append(area.area_id)
    if invalid:
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Областей без допустимой площади: {len(invalid)}. "
            f"Исправьте или удалите их: {', '.join(invalid[:8])}.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
                    message=(
                        "Исправьте вырожденные рабочие области; "
                        "они не исключаются из задания автоматически."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="coverage_areas",
                    affected_ids=invalid,
                )
            ],
        )
    generated: list[WorkSegment] = []
    if drones:
        parameters = FleetSurveyPolicy().grid_parameters(request, drones)
    for area in request.coverage_areas:
        check_planning_budget()
        plan_request = CoveragePlanRequest(
            area=area,
            mission_altitude_m=request.mission_altitude_m,
            parameters=parameters,
            exclusions=request.no_fly_zones,
            departure_candidates=[site.coordinate for site in _configured_departure_sites(request)],
            landing_candidates=[
                site.coordinate for site in _configured_primary_landing_sites(request)
            ],
            wind=request.wind,
            reference_airspeed_kmh=coverage_reference_airspeed_kmh,
            planning_area_count=len(request.coverage_areas),
            optimization_mode=request.optimization_mode,
        )
        try:
            result = coverage_planner.plan(plan_request)
        except ValidationError as error:
            raise PlanningError(
                code=ErrorCode.COVERAGE_GENERATION_FAILED,
                message=f"Для области {area.name} не удалось сформировать маршрут. "
                "Проверьте масштаб и геометрию исходного полигона.",
            ) from error
        except CoveragePlanningError as error:
            raise PlanningError(
                code=error.code,
                message=error.message,
                recommendations=_coverage_failure_recommendations(error.code, plan_request),
            ) from error
        # Preserve transferable straight strips. Aircraft-specific curves are
        # constructed inside candidate evaluation, so a failed reference grouping
        # does not prevent the productive pool from finding a different grouping.
        generated.extend(result.segments)
    return [*request.segments, *generated]


def _coverage_failure_recommendations(
    code: ErrorCode,
    request: CoveragePlanRequest,
) -> list[FeasibilityRecommendation]:
    if code is ErrorCode.GSD_REQUIREMENT_UNSATISFIED:
        sensor = request.parameters.sensor
        maximum_altitude_m = (
            request.parameters.maximum_gsd_cm
            / 100
            * sensor.image_width_px
            * sensor.focal_length_mm
            / sensor.sensor_width_mm
        )
        return [
            FeasibilityRecommendation(
                code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                message=(
                    f"Снизьте высоту задания до {maximum_altitude_m:.1f} м или ниже, "
                    "чтобы выполнить требование GSD для выбранной камеры."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="mission_altitude_m",
                current_value=request.mission_altitude_m,
                required_value=round(maximum_altitude_m, 2),
                unit="m",
                affected_ids=[request.area.area_id],
            )
        ]
    return [
        FeasibilityRecommendation(
            code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
            message=(
                "Проверьте пересечение рабочей области и запретов либо разделите "
                "область на независимые доступные части."
            ),
            confidence=RecommendationConfidence.ESTIMATE,
            parameter="coverage_areas/no_fly_zones",
            affected_ids=[request.area.area_id],
        )
    ]


def _coverage_reference_airspeed_kmh(
    request: PlanRequest,
    drones: list[DroneSpec],
) -> float | None:
    if _coverage_angle_cost(request) is CoverageAngleCost.GEOMETRIC_DISTANCE:
        return None
    return float(median(drone.cruise_speed_kmh for drone in drones))


def _coverage_angle_cost(request: PlanRequest) -> CoverageAngleCost:
    if (
        request.coverage_areas
        and request.survey_parameters is not None
        and request.survey_parameters.direction_mode is SweepDirectionMode.AUTOMATIC
        and request.wind is not None
        and request.wind.speed_mps > 0
    ):
        return CoverageAngleCost.WIND_ADJUSTED_DURATION
    return CoverageAngleCost.GEOMETRIC_DISTANCE


def _curvature_constrained_segment(
    segment: WorkSegment,
    area: CoverageArea,
    request: PlanRequest,
    transition_planner: SweepTransitionPlanner,
) -> WorkSegment:
    constraints = _constraints_for_segment(segment, request)
    curved_routes: list[list[Coordinate]] = []
    for route in [segment.route, *segment.alternative_routes]:
        if len(route) % 2 != 0:
            continue
        try:
            curvature_request = SweepRouteCurvatureRequest(
                route=route,
                coverage_area=area,
                additional_coverage_areas=[
                    a
                    for a in request.coverage_areas
                    if a.area_id in segment.area_ids and a.area_id != area.area_id
                ],
                no_fly_zones=constraints,
                minimum_turn_radius_m=request.minimum_turn_radius_m,
                sampling_step_m=max(3, min(25, request.minimum_turn_radius_m / 4)),
            )
        except ValidationError as error:
            raise PlanningError(
                code=ErrorCode.TURN_RADIUS_UNSATISFIED,
                message=f"Участок {segment.segment_id} не подходит для проверки радиуса.",
            ) from error
        try:
            curved = build_curvature_aware_sweep_route_with_planner(
                curvature_request,
                transition_planner,
            )
        except RouteGeometryError, ValidationError:
            continue
        if len(curved.route) <= 10_000:
            curved_routes.append(curved.route)
    if not curved_routes:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=(
                f"Для участка {segment.segment_id} не построены безопасные галсы. "
                f"Радиус: {request.minimum_turn_radius_m:g} м."
            ),
        )
    payload = segment.model_dump()
    payload["route"] = curved_routes[0]
    payload["alternative_routes"] = curved_routes[1:]
    payload["distance_km"] = route_distance_km(curved_routes[0])
    payload["survey_tracks"] = segment.survey_tracks or [
        (segment.route[index], segment.route[index + 1])
        for index in range(0, len(segment.route), 2)
    ]
    try:
        return WorkSegment.model_validate(payload)
    except ValidationError as error:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=f"Криволинейный маршрут участка {segment.segment_id} слишком длинный.",
        ) from error
