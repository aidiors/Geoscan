from __future__ import annotations

from geoplan.assignment.alternatives import AssignmentAlternativeProvider
from geoplan.assignment.models import (
    AssignmentOptimizationRequest,
    AssignmentOptimizationResult,
    MissionAssignmentCandidate,
)
from geoplan.assignment.solver import AssignmentOptimizer
from geoplan.budget import PlanningTimeExceeded, bounded_planning, improvement_allowed
from geoplan.coverage.models import WorkSegment
from geoplan.coverage.resolution import SurveyResolution
from geoplan.coverage.sweep import CoveragePlanner
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    AssignmentAlgorithm,
    ErrorCode,
    OptimalityStatus,
    OptimizationScope,
    RecommendationCode,
    RecommendationConfidence,
    RouteAlgorithm,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.duration import MissionDurationCalculator
from geoplan.flight.terrain import VerticalProfilePlanner
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.optimizer import RouteOptimizer
from geoplan.planning.assignment_portfolio import (
    AuditedAssignmentPortfolio,
    assignment_search_requested,
)
from geoplan.planning.candidates import (
    _candidate_evaluation,
    _candidate_is_feasible,
    _create_candidate_groups,
)
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.eligibility import (
    _drones_capable_of_environment,
    _drones_capable_of_task,
    _drones_capable_of_wind,
    _drones_with_eligible_sites,
)
from geoplan.planning.execution import AuditedMissionEvaluator
from geoplan.planning.freeform_ports import FreeformRouteOptimizer
from geoplan.planning.models import (
    AssignmentSearchSummary,
    OptimizationSummary,
    PlanRequest,
    PlanResponse,
)
from geoplan.planning.multi_area import improve_multi_area_routes
from geoplan.planning.plan_quality import plan_is_audited
from geoplan.planning.productive import improve_productive_routes
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
from geoplan.routing.pool import RoutePoolGenerator
from geoplan.safety.audit import PlanSafetyAuditor
from geoplan.scheduling.models import ScheduleSummary
from geoplan.scheduling.scheduler import MissionScheduler, ScheduleWindowUnavailableError


@bounded_planning
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
    freeform_optimizer: FreeformRouteOptimizer,
    assignment_alternative: AssignmentAlternativeProvider | None = None,
    productive_route_pool: RoutePoolGenerator | None = None,
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
        coverage_reference_airspeed_kmh,
        eligible_drones,
    )
    baseline_error: PlanningError | None = None
    baseline = None
    search_report = None

    def annotate(plan: PlanResponse) -> PlanResponse:
        if (
            plan.optimization is None
            or search_report is None
            or plan.optimization.assignment_search is not None
        ):
            return plan
        return plan.model_copy(
            update={
                "optimization": plan.optimization.model_copy(
                    update={"assignment_search": search_report}
                )
            }
        )

    try:
        optimized, assignment_problem = _assign_segments(
            request,
            segments,
            eligible_drones,
            duration_calculator,
            route_optimizer,
            sweep_transition_planner,
            vertical_profile_planner,
            assignment_optimizer,
        )
        baseline = _finish_plan(
            request,
            segments,
            optimized,
            mission_scheduler,
            safety_auditor,
            coverage_planner,
            coverage_reference_airspeed_kmh,
            len(eligible_drones),
        )
        if (
            assignment_search_requested(request, len(eligible_drones), len(segments))
            and assignment_alternative is not None
            and improvement_allowed()
        ):

            def evaluate_assignment(result: AssignmentOptimizationResult) -> PlanResponse:
                return _finish_plan(
                    request,
                    segments,
                    result,
                    mission_scheduler,
                    safety_auditor,
                    coverage_planner,
                    coverage_reference_airspeed_kmh,
                    len(eligible_drones),
                )

            baseline, search_report = AuditedAssignmentPortfolio(assignment_alternative).improve(
                request, assignment_problem, optimized, baseline, evaluate_assignment
            )
        elif (
            assignment_search_requested(request, len(eligible_drones), len(segments))
            and assignment_alternative is None
        ):
            search_report = AssignmentSearchSummary(
                status="unavailable",
                message="Дополнительный решатель не подключён; сохранён исходный план.",
            )
    except PlanningError as error:
        if not request.productive_routing:
            raise
        baseline_error = error
    if request.productive_routing and improvement_allowed():

        def create_candidate(segment: WorkSegment, drone: DroneSpec) -> Mission | None:
            candidate, _ = _candidate_evaluation(
                segment,
                drone,
                request,
                duration_calculator,
                route_optimizer,
                sweep_transition_planner,
                vertical_profile_planner,
                _constraints_for_segment(segment, request),
            )
            return (
                candidate.mission
                if candidate is not None and _candidate_is_feasible(candidate)
                else None
            )

        def evaluate(
            grouped: list[WorkSegment], assignments: list[MissionAssignmentCandidate]
        ) -> PlanResponse:
            loads: dict[str, float] = {}
            turnarounds = {
                d.drone_id: max(request.turnaround_minutes, d.minimum_turnaround_minutes)
                for d in eligible_drones
            }
            for assignment in assignments:
                drone_id = assignment.drone_id
                loads[drone_id] = (
                    loads.get(drone_id, -turnarounds[drone_id])
                    + turnarounds[drone_id]
                    + assignment.mission.duration_minutes
                )
            result = AssignmentOptimizationResult(
                assignments=assignments,
                algorithm=AssignmentAlgorithm.PRODUCTIVE_ROUTE_POOL,
                optimality=OptimalityStatus.HEURISTIC,
                completion_minutes=max(loads.values()),
                total_flight_minutes=sum(a.mission.duration_minutes for a in assignments),
                active_drone_count=len(loads),
            )
            return _finish_plan(
                request,
                grouped,
                result,
                mission_scheduler,
                safety_auditor,
                coverage_planner,
                coverage_reference_airspeed_kmh,
                len(eligible_drones),
            )

        try:
            if len(request.coverage_areas) > 1:
                improved = improve_multi_area_routes(
                    request,
                    segments,
                    eligible_drones,
                    baseline,
                    create_candidate,
                    evaluate,
                    assignment_optimizer=assignment_optimizer,
                    assignment_alternative=assignment_alternative,
                )
            else:
                improved = improve_productive_routes(
                    request,
                    segments,
                    eligible_drones,
                    baseline,
                    create_candidate,
                    evaluate,
                    route_pool=productive_route_pool,
                )
        except PlanningTimeExceeded:
            if baseline is not None and plan_is_audited(baseline):
                return annotate(baseline)
            raise
        if improved is not None:
            try:
                return annotate(
                    freeform_optimizer.improve(
                        request,
                        segments,
                        eligible_drones,
                        improved,
                        create_candidate,
                        evaluate,
                        reference=baseline,
                    )
                )
            except PlanningTimeExceeded:
                if plan_is_audited(improved):
                    return annotate(improved)
                raise
    if baseline is not None:
        return annotate(baseline)
    assert baseline_error is not None
    raise baseline_error


def _assign_segments(
    request: PlanRequest,
    segments: list[WorkSegment],
    eligible_drones: list[DroneSpec],
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    sweep_transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
    assignment_optimizer: AssignmentOptimizer,
) -> tuple[AssignmentOptimizationResult, AssignmentOptimizationRequest]:
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
        maximum_active_drones=(
            min(request.maximum_active_drones, len(eligible_drones))
            if request.maximum_active_drones is not None
            else None
        ),
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
    return optimized, optimization_request


def _finish_plan(
    request: PlanRequest,
    segments: list[WorkSegment],
    optimized: AssignmentOptimizationResult,
    mission_scheduler: MissionScheduler,
    safety_auditor: PlanSafetyAuditor,
    coverage_planner: CoveragePlanner,
    coverage_reference_airspeed_kmh: float | None,
    eligible_drone_count: int,
) -> PlanResponse:
    missions = [selected.mission for selected in optimized.assignments]
    try:
        selection = AuditedMissionEvaluator(mission_scheduler, safety_auditor).evaluate(
            request, segments, missions
        )
        schedule = selection.schedule
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
        if request.survey_parameters is not None:
            mission = mission.model_copy(
                update={
                    "computed_gsd_cm": SurveyResolution().gsd_cm(
                        mission, mission.sensor or request.survey_parameters.sensor
                    )
                }
            )
        assignments[mission.drone_id].append(mission)
    drone_plans = _create_drone_plans(request.drones, assignments)
    metrics = _create_metrics(drone_plans, len(request.drones)).model_copy(
        update={"completion_minutes": schedule.makespan_minutes}
    )
    audit = selection.audit
    return PlanResponse(
        criterion=request.criterion,
        drone_plans=drone_plans,
        metrics=metrics,
        feasibility=_create_feasibility_report(
            metrics,
            request,
            eligible_drone_count,
            audit,
        ),
        optimization=OptimizationSummary(
            algorithm=optimized.algorithm,
            coverage_algorithm=coverage_planner.coverage_algorithm,
            route_algorithm=RouteAlgorithm.BIDIRECTIONAL_VISIBILITY_GRAPH,
            scope=(
                OptimizationScope.FIXED_TRACK_ROUTE_POOL
                if optimized.algorithm is AssignmentAlgorithm.PRODUCTIVE_ROUTE_POOL
                else OptimizationScope.GENERATED_ROUTE_ASSIGNMENT
            ),
            optimality=optimized.optimality,
            completion_minutes=schedule.makespan_minutes,
            total_flight_minutes=sum(m.duration_minutes for m in schedule.missions),
            active_drone_count=optimized.active_drone_count,
            survey_turn_radius_m=request.minimum_turn_radius_m,
            turn_radii_by_drone={
                d.drone_id: max(request.minimum_turn_radius_m, d.minimum_turn_radius_m)
                for d in request.drones
            },
            coverage_angle_cost=_coverage_angle_cost(request),
            coverage_reference_airspeed_kmh=coverage_reference_airspeed_kmh,
        ),
        schedule=ScheduleSummary(
            variants_evaluated=selection.variants_evaluated,
            dispatch_priority=selection.dispatch_priority,
            layer_preference=selection.layer_preference,
            reference_makespan_minutes=selection.reference_makespan_minutes,
            reference_total_flight_minutes=selection.reference_total_flight_minutes,
            reference_audited=selection.reference_audited,
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
        safety_audit=audit,
    )
