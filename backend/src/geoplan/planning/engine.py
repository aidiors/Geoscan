from __future__ import annotations

from geoplan.airspace.windows import _schedule_time_windows, _vertical_layers_by_segment_id
from geoplan.assignment.models import AssignmentOptimizationRequest
from geoplan.assignment.solver import AssignmentOptimizer
from geoplan.coverage.sweep import CoveragePlanner
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    ErrorCode,
    OptimizationScope,
    RecommendationCode,
    RecommendationConfidence,
    RouteAlgorithm,
)
from geoplan.errors import PlanningError
from geoplan.flight.duration import MissionDurationCalculator
from geoplan.flight.terrain import VerticalProfilePlanner
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.optimizer import RouteOptimizer
from geoplan.planning.candidates import _create_candidate_groups
from geoplan.planning.eligibility import (
    _drones_capable_of_environment,
    _drones_capable_of_task,
    _drones_capable_of_wind,
    _drones_with_eligible_sites,
)
from geoplan.planning.models import OptimizationSummary, PlanRequest, PlanResponse
from geoplan.planning.reporting import (
    _create_drone_plans,
    _create_feasibility_report,
    _create_metrics,
)
from geoplan.planning.segments import (
    _coverage_angle_cost,
    _coverage_reference_airspeed_kmh,
    _resolve_segments,
)
from geoplan.safety.audit import PlanSafetyAuditor
from geoplan.scheduling.models import MissionScheduleRequest, ScheduleSummary
from geoplan.scheduling.scheduler import MissionScheduler, ScheduleWindowUnavailableError


def build_plan(
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    coverage_planner: CoveragePlanner,
    assignment_optimizer: AssignmentOptimizer,
    mission_scheduler: MissionScheduler,
    sweep_transition_planner: SweepTransitionPlanner,
    safety_auditor: PlanSafetyAuditor,
    vertical_profile_planner: VerticalProfilePlanner,
) -> PlanResponse:
    capable_drones = _drones_capable_of_task(request.drones, request.task)
    envelope_capable_drones = _drones_capable_of_environment(capable_drones, request)
    weather_capable_drones = _drones_capable_of_wind(envelope_capable_drones, request.wind)
    eligible_drones = _drones_with_eligible_sites(weather_capable_drones, request)
    coverage_reference_airspeed_kmh = _coverage_reference_airspeed_kmh(
        request,
        eligible_drones,
    )
    segments = _resolve_segments(
        request,
        coverage_planner,
        sweep_transition_planner,
        coverage_reference_airspeed_kmh,
    )
    candidate_groups = _create_candidate_groups(
        segments,
        eligible_drones,
        request,
        duration_calculator,
        route_optimizer,
        sweep_transition_planner,
        vertical_profile_planner,
    )
    optimization_request = AssignmentOptimizationRequest(
        criterion=request.criterion,
        drone_ids=[drone.drone_id for drone in eligible_drones],
        candidate_groups=candidate_groups,
        maximum_active_drones=request.maximum_active_drones,
        completion_deadline_minutes=request.completion_deadline_minutes,
        optimization_mode=request.optimization_mode,
        turnaround_minutes_by_drone={
            drone.drone_id: max(request.turnaround_minutes, drone.minimum_turnaround_minutes)
            for drone in eligible_drones
        },
    )
    try:
        optimized = assignment_optimizer.optimize(optimization_request)
    except ValueError as error:
        current_limit = request.maximum_active_drones or len(eligible_drones)
        required_limit = min(len(eligible_drones), current_limit + 1)
        if required_limit > current_limit:
            assignment_recommendations = [
                FeasibilityRecommendation(
                    code=RecommendationCode.INCREASE_ACTIVE_DRONE_LIMIT,
                    message=(
                        f"Разрешите использовать не менее {required_limit} БВС "
                        "или сократите число одновременно планируемых участков."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="maximum_active_drones",
                    current_value=current_limit,
                    required_value=required_limit,
                    unit="drones",
                )
            ]
        else:
            assignment_recommendations = [
                FeasibilityRecommendation(
                    code=RecommendationCode.REDUCE_MISSION_SCOPE,
                    message="Разделите набор участков на несколько последовательных планов.",
                    confidence=RecommendationConfidence.ESTIMATE,
                    affected_ids=[segment.segment_id for segment in segments],
                )
            ]
        raise PlanningError(
            code=ErrorCode.UNPLANNABLE_SEGMENT,
            message="Ограничение числа БВС не позволяет распределить все участки.",
            recommendations=assignment_recommendations,
        ) from error
    missions = [selected.mission for selected in optimized.assignments]
    vertical_layers = _vertical_layers_by_segment_id(missions, request)
    schedule_request = MissionScheduleRequest(
        missions=missions,
        minimum_separation_m=request.minimum_separation_m,
        minimum_vertical_separation_m=request.minimum_vertical_separation_m,
        position_uncertainty_m=request.position_uncertainty_m,
        start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
        speed_uncertainty_percent=request.speed_uncertainty_percent,
        flight_site_occupancy_seconds=request.flight_site_occupancy_seconds,
        turnaround_minutes=request.turnaround_minutes,
        turnaround_minutes_by_drone={
            drone.drone_id: drone.minimum_turnaround_minutes for drone in request.drones
        },
        planning_start_time=request.planning_start_time,
        time_windows_by_segment_id=_schedule_time_windows(request, missions),
        vertical_layers_by_segment_id=vertical_layers,
    )
    try:
        schedule = mission_scheduler.schedule(schedule_request)
    except ScheduleWindowUnavailableError as error:
        failed_mission = next(
            mission for mission in missions if mission.segment_id == error.segment_id
        )
        recommendations = [
            FeasibilityRecommendation(
                code=RecommendationCode.RESCHEDULE_OPERATION,
                message="Сдвиньте операцию к следующему периоду действия разрешений.",
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="planning_start_time",
                affected_ids=[error.segment_id],
            )
        ]
        if request.planning_start_time is not None and request.planning_end_time is not None:
            operating_minutes = (
                request.planning_end_time - request.planning_start_time
            ).total_seconds() / 60
            recommendations.append(
                FeasibilityRecommendation(
                    code=RecommendationCode.EXTEND_OPERATING_WINDOW,
                    message=(
                        "Увеличьте рабочее окно как минимум до длительности вылета "
                        f"{failed_mission.duration_minutes:.1f} мин и учтите ожидание ограничений."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="planning_end_time",
                    current_value=round(operating_minutes, 2),
                    required_value=failed_mission.duration_minutes,
                    unit="minutes",
                    affected_ids=[error.segment_id],
                )
            )
        raise PlanningError(
            code=ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE,
            message=(
                f"Вылет участка {error.segment_id} не помещается в допустимое "
                "4D-окно. Сдвиньте начало или окончание работ либо уточните "
                "периоды ограничений."
            ),
            recommendations=recommendations,
        ) from error
    except RuntimeError as error:
        raise PlanningError(
            code=ErrorCode.SCHEDULE_GENERATION_FAILED,
            message="Безопасное расписание вылетов построить не удалось.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.RESCHEDULE_OPERATION,
                    message=(
                        "Увеличьте временной разнос вылетов или перенесите часть миссий "
                        "в другое рабочее окно."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="planning_start_time",
                    affected_ids=[mission.segment_id for mission in missions],
                )
            ],
        ) from error
    assignments = {drone.drone_id: [] for drone in request.drones}
    for mission in schedule.missions:
        assignments[mission.drone_id].append(mission)
    drone_plans = _create_drone_plans(request.drones, assignments)
    metrics = _create_metrics(drone_plans, len(request.drones))
    return PlanResponse(
        criterion=request.criterion,
        drone_plans=drone_plans,
        metrics=metrics,
        feasibility=_create_feasibility_report(
            metrics,
            request,
            len(eligible_drones),
        ),
        optimization=OptimizationSummary(
            algorithm=optimized.algorithm,
            coverage_algorithm=coverage_planner.coverage_algorithm,
            route_algorithm=RouteAlgorithm.BIDIRECTIONAL_VISIBILITY_GRAPH,
            scope=OptimizationScope.GENERATED_ROUTE_ASSIGNMENT,
            optimality=optimized.optimality,
            completion_minutes=optimized.completion_minutes,
            total_flight_minutes=optimized.total_flight_minutes,
            active_drone_count=optimized.active_drone_count,
            survey_turn_radius_m=request.minimum_turn_radius_m,
            coverage_angle_cost=_coverage_angle_cost(request),
            coverage_reference_airspeed_kmh=coverage_reference_airspeed_kmh,
        ),
        schedule=ScheduleSummary(
            algorithm=schedule.algorithm,
            makespan_minutes=schedule.makespan_minutes,
            minimum_separation_m=request.minimum_separation_m,
            minimum_vertical_separation_m=request.minimum_vertical_separation_m,
            vertically_deconflicted_mission_count=sum(
                mission.vertical_deconfliction_offset_m > 0 for mission in schedule.missions
            ),
            position_uncertainty_m=request.position_uncertainty_m,
            start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
            speed_uncertainty_percent=request.speed_uncertainty_percent,
            flight_site_occupancy_seconds=request.flight_site_occupancy_seconds,
            turnaround_minutes=request.turnaround_minutes,
            resolved_conflict_count=schedule.resolved_conflict_count,
            resolved_conflicts=schedule.resolved_conflicts,
        ),
        safety_audit=safety_auditor.audit(request, segments, schedule.missions),
    )
