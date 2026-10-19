"""Generate bounded route alternatives; accept only audited scheduled improvements."""

from collections.abc import Callable
from math import ceil

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.budget import PlanningTimeExceeded, improvement_allowed
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import OptimizationMode
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.identifiers import derived_id
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.energy import _effective_endurance_minutes
from geoplan.flight.sites import _eligible_departure_sites, _eligible_landing_sites
from geoplan.flight.takeoff_landing import TakeoffLandingEstimator
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.curved_costs import CurvedConnectionCost
from geoplan.planning.fleet_survey import FleetSurveyPolicy
from geoplan.planning.models import PlanRequest, PlanResponse, ProductiveRoutingSummary
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.routing.model import (
    Aircraft,
    CostModel,
    RoutingProblem,
    Solution,
    Track,
)
from geoplan.routing.pool import ProductiveRoutePool, RoutePoolGenerator

CandidateBuilder = Callable[[WorkSegment, DroneSpec], Mission | None]
PlanEvaluator = Callable[[list[WorkSegment], list[MissionAssignmentCandidate]], PlanResponse]


def improve_productive_routes(
    request: PlanRequest,
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    baseline: PlanResponse | None,
    build_candidate: CandidateBuilder,
    evaluate: PlanEvaluator,
    route_pool: RoutePoolGenerator | None = None,
) -> PlanResponse | None:
    """Retain the baseline and a bounded diverse pool for real scheduling.

    The segment size controls the reference split; productive groups are bounded
    by physical battery resources. Mission construction rechecks actual sites,
    terrain, emergency reserve, range and airspace; no fixture adapter is used.
    """
    reason = _unsupported_reason(request, segments)
    checked = 0
    selected_name = "baseline"
    best = baseline
    pre_cross = None
    cross_checked = 0
    if reason is None:
        model = _routing_model(request, segments, drones)
        if model is None:
            reason = "Отсутствуют площадки или запас энергии для дополнительных вариантов."
        else:
            alternatives = (
                route_pool.generate(model, request.maximum_active_drones)
                if route_pool is not None
                else ProductiveRoutePool().generate(model, request.maximum_active_drones)
            )
            for is_cross, candidates in (
                (False, alternatives.primary),
                (True, alternatives.cross_objective),
            ):
                if is_cross and candidates:
                    pre_cross = best
                for name, solution in candidates:
                    if best is not None and not improvement_allowed():
                        break
                    if (
                        request.maximum_active_drones is not None
                        and len({trip.aircraft for trip in solution})
                        > request.maximum_active_drones
                    ):
                        continue
                    if not model.validate_geometry(solution):
                        continue
                    mission_candidates = _mission_candidates(
                        request, segments[0], drones, model, solution, build_candidate
                    )
                    if mission_candidates is None:
                        continue
                    grouped, assignments = mission_candidates
                    try:
                        candidate = evaluate(grouped, assignments)
                    except PlanningTimeExceeded:
                        if best is None:
                            raise
                        break
                    except PlanningError:
                        continue
                    checked += 1
                    cross_checked += int(is_cross)
                    if not plan_is_audited(candidate):
                        continue
                    if (
                        best is None
                        or not plan_is_audited(best)
                        or scheduled_plan_objective(request, candidate)
                        < scheduled_plan_objective(request, best)
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
    if not plan_is_audited(best):
        message = "Новые варианты не прошли аудит. Исходный план требует исправлений."
    summary = ProductiveRoutingSummary(
        selected_variant=selected_name,
        evaluated_variants=checked,
        pre_cross_objective=list(scheduled_plan_objective(request, pre_cross))
        if pre_cross is not None and plan_is_audited(pre_cross)
        else None,
        pre_cross_makespan_minutes=pre_cross.metrics.completion_minutes
        if pre_cross is not None
        else None,
        pre_cross_total_flight_minutes=pre_cross.metrics.total_flight_minutes
        if pre_cross is not None
        else None,
        cross_objective_variants_evaluated=cross_checked,
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
    if len(request.coverage_areas) != 1 or request.segments:
        return "Группировка вылетов применяется к одной области без ручных маршрутов."
    if not segments or any(
        (s.survey_tracks is None and len(s.route) % 2) or s.survey_speed_limit_kmh is None
        for s in segments
    ):
        return "Исходная геометрия не содержит отдельных прямых съёмочных галсов."
    count = sum(len(_survey_tracks(s)) for s in segments)
    if not 2 <= count <= 400:
        return "Для этого числа галсов сохранено исходное планирование."
    return None


def _survey_tracks(segment: WorkSegment) -> list[tuple[Coordinate, Coordinate]]:
    return segment.survey_tracks or [
        (segment.route[index], segment.route[index + 1])
        for index in range(0, len(segment.route), 2)
    ]


def _routing_model(
    request: PlanRequest, segments: list[WorkSegment], drones: list[DroneSpec]
) -> CostModel[RoutingProblem] | None:
    tracks = tuple(
        Track(
            f"track-{index}-{offset}",
            track[0],
            track[1],
            segment.survey_speed_limit_kmh or 1,
        )
        for index, segment in enumerate(segments)
        for offset, track in enumerate(_survey_tracks(segment))
    )
    aircraft = []
    for drone in drones:
        physical = FleetSurveyPolicy().for_drone(request, drone)
        survey = physical.survey_parameters
        camera_speed = (
            physical.survey_height_agl.minimum_m
            * survey.sensor.sensor_height_mm
            / survey.sensor.focal_length_mm
            * (1 - survey.forward_overlap_percent / 100)
            / survey.sensor.minimum_trigger_interval_s
            * 3.6
            if survey is not None
            else None
        )
        departure = next((s for s in _eligible_departure_sites(request, drone.drone_id) if s), None)
        landing = next((s for s in _eligible_landing_sites(request, drone.drone_id) if s), None)
        available = (
            _effective_endurance_minutes(drone)
            * (drone.initial_charge_percent - drone.battery_reserve_percent)
            / 100
        )
        if departure is not None and landing is not None and available > 0:
            phases = TakeoffLandingEstimator().estimate(
                drone, physical.survey_height_agl.maximum_m, physical.survey_height_agl.maximum_m
            )
            aircraft.append(
                Aircraft(
                    drone.drone_id,
                    drone.cruise_speed_kmh,
                    available,
                    departure.coordinate,
                    max(request.turnaround_minutes, drone.minimum_turnaround_minutes),
                    landing=landing.coordinate,
                    survey_speed_kmh=camera_speed,
                    minimum_turn_radius_m=physical.minimum_turn_radius_m,
                    resource_limit=drone.resource_limit(),
                    sortie_overhead_minutes=phases.duration_minutes,
                    sortie_energy_surcharge_minutes=phases.energy_surcharge_minutes,
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
        ),
        connection_cost=CurvedConnectionCost(request, tuple(exclusions.values())),
    )


def _route_pool(
    model: CostModel[RoutingProblem], maximum_active_drones: int | None = None
) -> list[tuple[str, Solution]]:
    return ProductiveRoutePool().generate(model, maximum_active_drones).all


def _mission_candidates(
    request: PlanRequest,
    template: WorkSegment,
    drones: list[DroneSpec],
    model: CostModel[RoutingProblem],
    solution: Solution,
    build: CandidateBuilder,
    *,
    track_area_ids: tuple[str, ...] | None = None,
    group_id: str | None = None,
) -> tuple[list[WorkSegment], list[MissionAssignmentCandidate]] | None:
    fleet = {drone.drone_id: drone for drone in drones}
    segments, candidates = [], []
    parameters = request.survey_parameters
    assert parameters is not None
    trigger = (
        request.survey_height_agl.minimum_m
        * parameters.sensor.sensor_height_mm
        / parameters.sensor.focal_length_mm
        * (1 - parameters.forward_overlap_percent / 100)
    )
    for index, trip in enumerate(solution):
        tracks = [model.endpoints(token) for token in trip.visits]
        route = [coordinate for track in tracks for coordinate in track]
        segment = template.model_copy(
            update={
                "segment_id": derived_id(
                    group_id or template.coverage_area_id or "area", f"-productive-{index + 1}"
                ),
                "distance_km": route_distance_km(route),
                "route": route,
                "alternative_routes": [],
                "survey_tracks": tracks,
                "image_count": sum(
                    ceil(route_distance_km(list(track)) * 1000 / trigger) + 1 for track in tracks
                ),
            }
        )
        if track_area_ids is not None:
            owners = [track_area_ids[token // 2] for token in trip.visits]
            segment = WorkSegment.model_validate(
                {
                    **segment.model_dump(),
                    "coverage_area_id": owners[0],
                    "survey_track_area_ids": owners,
                    "flight_direction_degrees": None,
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
