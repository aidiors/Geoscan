"""Generate bounded route alternatives; accept only audited scheduled improvements."""

from collections.abc import Callable
from math import ceil
from time import perf_counter

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import OptimizationCriterion, OptimizationMode, SafetyAuditStatus
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.energy import _effective_endurance_minutes
from geoplan.flight.sites import _eligible_departure_sites, _eligible_landing_sites
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.models import PlanRequest, PlanResponse, ProductiveRoutingSummary
from geoplan.routing.allocation import assign_trips
from geoplan.routing.model import (
    Aircraft,
    CostModel,
    RoutingProblem,
    Solution,
    Track,
    routing_objective,
)
from geoplan.routing.ordering import cell_orders, order_cost
from geoplan.routing.split import decode, route_catalog

CandidateBuilder = Callable[[WorkSegment, DroneSpec], Mission | None]
PlanEvaluator = Callable[[list[WorkSegment], list[MissionAssignmentCandidate]], PlanResponse]


def improve_productive_routes(
    request: PlanRequest,
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    baseline: PlanResponse | None,
    build_candidate: CandidateBuilder,
    evaluate: PlanEvaluator,
) -> PlanResponse | None:
    """Retain the baseline and at most three distinct alternatives for real scheduling.

    The segment size controls the reference split; productive groups are bounded
    by physical battery resources. Mission construction rechecks actual sites,
    terrain, emergency reserve, range and airspace; no fixture adapter is used.
    """
    reason = _unsupported_reason(request, segments)
    checked = 0
    selected_name = "baseline"
    best = baseline
    if reason is None:
        model = _routing_model(request, segments, drones)
        if model is None:
            reason = "Отсутствуют площадки или запас энергии для дополнительных вариантов."
        else:
            for name, solution in _route_pool(model):
                if (
                    request.maximum_active_drones is not None
                    and len({trip.aircraft for trip in solution}) > request.maximum_active_drones
                ):
                    continue
                if not model.validate_geometry(solution):
                    continue
                candidates = _mission_candidates(
                    request, segments[0], drones, model, solution, build_candidate
                )
                if candidates is None:
                    continue
                grouped, assignments = candidates
                try:
                    candidate = evaluate(grouped, assignments)
                except PlanningError:
                    continue
                checked += 1
                if not _audited(candidate):
                    continue
                if (
                    best is None
                    or not _audited(best)
                    or _objective(request, candidate) < _objective(request, best)
                ):
                    best, selected_name = candidate, name
    if best is None:
        return None
    if selected_name != "baseline":
        message = "Выбраны новые вылеты по итоговому расписанию при сохранении исходного критерия."
    else:
        message = reason or (
            "Исходный план лучше проверенных дополнительных вариантов."
            if checked
            else "Сохранён исходный план: дополнительные варианты недоступны."
        )
    if not _audited(best):
        message = "Новые варианты не прошли аудит. Исходный план требует исправлений."
    summary = ProductiveRoutingSummary(
        selected_variant=selected_name,
        evaluated_variants=checked,
        baseline_makespan_minutes=baseline.schedule.makespan_minutes
        if baseline is not None and baseline.schedule is not None
        else None,
        baseline_total_flight_minutes=baseline.metrics.total_flight_minutes
        if baseline is not None
        else None,
        baseline_transit_minutes=sum(
            mission.transit_duration_minutes or 0
            for plan in baseline.drone_plans
            for mission in plan.missions
        )
        if baseline is not None
        else None,
        message=message,
    )
    assert best.optimization is not None
    return best.model_copy(
        update={
            "optimization": best.optimization.model_copy(update={"productive_routing": summary})
        }
    )


def _unsupported_reason(request: PlanRequest, segments: list[WorkSegment]) -> str | None:
    if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
        return "Быстрый режим: сохранено исходное планирование."
    if request.minimum_turn_radius_m > 0:
        return "Сохранён исходный план: заданный радиус разворота."
    if len(request.coverage_areas) != 1 or request.segments:
        return "Группировка вылетов применяется к одной области без ручных маршрутов."
    if not segments or any(len(s.route) % 2 or s.survey_speed_limit_kmh is None for s in segments):
        return "Исходная геометрия не содержит отдельных прямых съёмочных галсов."
    count = sum(len(s.route) // 2 for s in segments)
    if not 2 <= count <= 400:
        return "Для этого числа галсов сохранено исходное планирование."
    return None


def _routing_model(
    request: PlanRequest, segments: list[WorkSegment], drones: list[DroneSpec]
) -> CostModel[RoutingProblem] | None:
    tracks = tuple(
        Track(
            f"track-{index}-{offset}",
            segment.route[offset],
            segment.route[offset + 1],
            segment.survey_speed_limit_kmh or 1,
        )
        for index, segment in enumerate(segments)
        for offset in range(0, len(segment.route), 2)
    )
    aircraft = []
    for drone in drones:
        departure = next((s for s in _eligible_departure_sites(request, drone.drone_id) if s), None)
        landing = next((s for s in _eligible_landing_sites(request, drone.drone_id) if s), None)
        available = (
            _effective_endurance_minutes(drone)
            * (drone.initial_charge_percent - drone.battery_reserve_percent)
            / 100
        )
        if departure is not None and landing is not None and available > 0:
            aircraft.append(
                Aircraft(
                    drone.drone_id,
                    drone.cruise_speed_kmh,
                    available,
                    departure.coordinate,
                    max(request.turnaround_minutes, drone.minimum_turnaround_minutes),
                    landing=landing.coordinate,
                )
            )
    if not aircraft:
        return None
    exclusions = {
        zone.zone_id: zone
        for segment in segments
        for zone in _constraints_for_segment(segment, request)
    }
    return CostModel(
        RoutingProblem(
            tracks,
            tuple(aircraft),
            None,
            request.wind,
            tuple(exclusions.values()),
            request.criterion,
            request.completion_deadline_minutes,
        )
    )


def _route_pool(model: CostModel[RoutingProblem]) -> list[tuple[str, Solution]]:
    order = min(cell_orders(model), key=lambda value: (order_cost(model, value), value))
    catalog = route_catalog(model, order, perf_counter() + 2)
    physical = max(plane.available_minutes for plane in model.problem.aircraft)
    targets = [physical]
    if model.problem.criterion is not OptimizationCriterion.TOTAL_FLIGHT_TIME and physical > 60:
        targets.append(45)
    pool, seen = [], set()
    for index, target in enumerate(targets):
        solution = decode(catalog, target, perf_counter() + 1)
        if not solution:
            continue
        allocated = assign_trips(model, solution, perf_counter() + 1)
        variants = (
            [("catalog", solution), ("allocated", allocated)]
            if index == 0
            else [("short_sorties", allocated)]
        )
        for name, candidate in variants:
            if candidate and candidate not in seen:
                pool.append((name, candidate))
                seen.add(candidate)
    return pool


def _mission_candidates(
    request: PlanRequest,
    template: WorkSegment,
    drones: list[DroneSpec],
    model: CostModel[RoutingProblem],
    solution: Solution,
    build: CandidateBuilder,
) -> tuple[list[WorkSegment], list[MissionAssignmentCandidate]] | None:
    fleet = {drone.drone_id: drone for drone in drones}
    segments, candidates = [], []
    parameters = request.survey_parameters
    assert parameters is not None
    trigger = (
        request.mission_altitude_m
        * parameters.sensor.sensor_height_mm
        / parameters.sensor.focal_length_mm
        * (1 - parameters.forward_overlap_percent / 100)
    )
    for index, trip in enumerate(solution):
        tracks = [model.endpoints(token) for token in trip.visits]
        route = [coordinate for track in tracks for coordinate in track]
        prefix = (template.coverage_area_id or "area")[:40]
        segment = template.model_copy(
            update={
                "segment_id": f"{prefix}-productive-{index + 1}",
                "distance_km": route_distance_km(route),
                "route": route,
                "alternative_routes": [],
                "survey_tracks": tracks,
                "image_count": sum(
                    ceil(route_distance_km(list(track)) * 1000 / trigger) + 1 for track in tracks
                ),
            }
        )
        drone_id = model.problem.aircraft[trip.aircraft].drone_id
        mission = build(segment, fleet[drone_id])
        if mission is None:
            return None
        segments.append(segment)
        candidates.append(
            MissionAssignmentCandidate(
                segment_id=segment.segment_id, drone_id=drone_id, mission=mission
            )
        )
    return segments, candidates


def _audited(plan: PlanResponse) -> bool:
    return plan.safety_audit is not None and plan.safety_audit.status is SafetyAuditStatus.PASSED


def _objective(request: PlanRequest, plan: PlanResponse) -> tuple[float, ...]:
    missions = [mission for drone in plan.drone_plans for mission in drone.missions]
    total = sum(mission.duration_minutes for mission in missions)
    transit = sum(mission.transit_duration_minutes or 0 for mission in missions)
    completion = (
        plan.schedule.makespan_minutes if plan.schedule else plan.metrics.completion_minutes
    )
    active = float(plan.metrics.active_drone_count)
    return routing_objective(
        request.criterion,
        request.completion_deadline_minutes,
        total,
        transit,
        completion,
        int(active),
    )
