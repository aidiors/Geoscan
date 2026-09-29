from src.coverage import CoveragePlanner, CoveragePlanningError
from src.curvature import SweepTransitionPlanner
from src.routing import (
    RouteGeometryError,
    build_safe_route,
    route_distance_km,
    route_is_inside_allowed_airspaces,
)
from src.schemas import (
    AltitudeReference,
    CoveragePlanRequest,
    NoFlyZone,
    TurnAwareSweepRequest,
    TurnRadiusAnalysisRequest,
    TurnRadiusAnalysisResult,
    TurnRadiusScenario,
    TurnRadiusStatus,
)
from src.turn_aware import build_turn_aware_sweep_route

MAXIMUM_ANALYZED_SURVEY_LEGS = 100


def analyze_turn_radii(
    request: TurnRadiusAnalysisRequest,
    coverage_planner: CoveragePlanner,
    transition_planner: SweepTransitionPlanner,
) -> TurnRadiusAnalysisResult:
    survey_parameters = request.coverage.parameters.model_copy(
        update={"maximum_segment_distance_km": 500}
    )
    coverage_request = request.coverage.model_copy(update={"parameters": survey_parameters})
    try:
        coverage = coverage_planner.plan(coverage_request)
    except CoveragePlanningError as exc:
        return _not_evaluated(request, f"Исходные галсы не построены: {exc.message}")
    if len(coverage.segments) != 1 or len(coverage.segments[0].route) % 2 != 0:
        return _not_evaluated(
            request,
            "Для этой зоны галсы разбиты на несколько частей; проверка радиуса пока недоступна.",
        )
    route = coverage.segments[0].route
    if len(route) > MAXIMUM_ANALYZED_SURVEY_LEGS * 2:
        return _not_evaluated(
            request,
            "Более 100 галсов требуют пакетного расчёта; интерактивная проверка не запускалась.",
        )
    exclusions = _exclusions_with_holes(coverage_request)
    altitude_airspaces = [
        airspace
        for airspace in request.allowed_airspaces
        if airspace.altitude_reference is AltitudeReference.MSL
        and airspace.minimum_altitude_m
        <= coverage_request.mission_altitude_m
        <= airspace.maximum_altitude_m
    ]
    scenarios: list[TurnRadiusScenario] = []
    common_route = None
    for radius_m in request.radii_m:
        if radius_m == 0:
            try:
                candidate_route = build_safe_route(route, exclusions)
            except RouteGeometryError:
                scenarios.append(
                    TurnRadiusScenario(
                        radius_m=radius_m,
                        status=TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS,
                        message="Для выбранных галсов безопасная ломаная не построена.",
                    )
                )
                continue
            distance_km = route_distance_km(candidate_route)
            uncovered_area_m2 = None
            success_message = "Построена безопасная ломаная без ограничения радиуса."
        else:
            turn_request = TurnAwareSweepRequest(
                route=route,
                coverage_area=coverage_request.area,
                no_fly_zones=exclusions,
                minimum_turn_radius_m=radius_m,
                strip_width_m=min(coverage.footprint_width_m, survey_parameters.strip_width_m),
                footprint_height_m=coverage.footprint_height_m,
            )
            try:
                result = build_turn_aware_sweep_route(turn_request, transition_planner)
            except RouteGeometryError:
                scenarios.append(
                    TurnRadiusScenario(
                        radius_m=radius_m,
                        status=TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS,
                        message="Для выбранных галсов безопасный разворот не построен.",
                    )
                )
                continue
            candidate_route = result.route
            distance_km = result.distance_km
            uncovered_area_m2 = result.uncovered_area_m2
            success_message = "Съёмочные галсы, покрытие и развороты проверены."
        if request.allowed_airspaces and (
            not altitude_airspaces
            or not route_is_inside_allowed_airspaces(candidate_route, altitude_airspaces)
        ):
            scenarios.append(
                TurnRadiusScenario(
                    radius_m=radius_m,
                    status=TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS,
                    message="Разворот выходит за пределы разрешённого воздушного пространства.",
                )
            )
            continue
        scenarios.append(
            TurnRadiusScenario(
                radius_m=radius_m,
                status=TurnRadiusStatus.FEASIBLE,
                message=success_message,
                distance_km=distance_km,
                uncovered_area_m2=uncovered_area_m2,
            )
        )
        if radius_m == request.radii_m[-1]:
            common_route = candidate_route
    if common_route is not None:
        maximum_scenario = scenarios[-1]
        scenarios = [
            scenario
            if scenario.status is TurnRadiusStatus.FEASIBLE
            else TurnRadiusScenario(
                radius_m=scenario.radius_m,
                status=TurnRadiusStatus.FEASIBLE,
                message="Подходит общий маршрут, рассчитанный для наибольшего радиуса.",
                distance_km=maximum_scenario.distance_km,
                uncovered_area_m2=maximum_scenario.uncovered_area_m2,
            )
            for scenario in scenarios
        ]
    if common_route is not None and request.radii_m == [0]:
        message = (
            "Проверена съёмочная ломаная без ограничения радиуса разворота. "
            "Взлёт, транзит, посадка и ограничения конкретного БВС здесь не проверены."
        )
    elif common_route is not None:
        message = (
            "Маршрут для наибольшего радиуса пригоден для всех меньших радиусов диапазона. "
            "Взлёт, транзит, посадка и ограничения конкретного БВС здесь не проверены."
        )
    else:
        message = (
            "Общий маршрут для всего диапазона не найден. Это не доказывает невыполнимость задачи."
        )
    return TurnRadiusAnalysisResult(
        area_id=request.coverage.area.area_id,
        scenarios=scenarios,
        common_survey_route=common_route,
        message=message,
    )


def _exclusions_with_holes(request: CoveragePlanRequest) -> list[NoFlyZone]:
    holes = [
        NoFlyZone(
            zone_id=f"analysis-hole-{index + 1}",
            coordinates=ring,
            safety_margin_m=0.5,
        )
        for index, ring in enumerate(request.area.interior_rings)
    ]
    return [*request.exclusions, *holes]


def _not_evaluated(
    request: TurnRadiusAnalysisRequest,
    message: str,
) -> TurnRadiusAnalysisResult:
    return TurnRadiusAnalysisResult(
        area_id=request.coverage.area.area_id,
        scenarios=[
            TurnRadiusScenario(
                radius_m=radius_m,
                status=TurnRadiusStatus.NOT_EVALUATED,
                message=message,
            )
            for radius_m in request.radii_m
        ],
        message=message,
    )
