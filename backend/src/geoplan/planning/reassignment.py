"""Reassign the selected regrouped sorties against the complete job's fleet loads."""

from geoplan.assignment.alternatives import AssignmentAlternativeProvider
from geoplan.assignment.models import AssignmentOptimizationRequest, MissionAssignmentCandidate
from geoplan.assignment.solver import AssignmentOptimizer
from geoplan.budget import improvement_allowed
from geoplan.coverage.models import WorkSegment
from geoplan.domain.fleet import DroneSpec
from geoplan.errors import PlanningError
from geoplan.planning.assignment_portfolio import (
    AuditedAssignmentPortfolio,
    assignment_search_requested,
)
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.planning.productive import CandidateBuilder, PlanEvaluator


def improve_regrouped_assignment(
    request: PlanRequest,
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    baseline: PlanResponse,
    build: CandidateBuilder,
    evaluate: PlanEvaluator,
    optimizer: AssignmentOptimizer,
    alternative: AssignmentAlternativeProvider | None,
) -> tuple[PlanResponse, int]:
    best, checked = baseline, 0
    if (
        not plan_is_audited(baseline)
        or len(segments) * len(drones) > 2_000
        or not improvement_allowed(90)
    ):
        return best, checked
    try:
        groups = []
        for segment in segments:
            group = []
            for drone in drones:
                mission = build(segment, drone)
                if mission is not None:
                    group.append(
                        MissionAssignmentCandidate(
                            segment_id=segment.segment_id, drone_id=drone.drone_id, mission=mission
                        )
                    )
            if not group:
                return best, checked
            groups.append(group)
        problem = AssignmentOptimizationRequest(
            criterion=request.criterion,
            optimization_mode=request.optimization_mode,
            drone_ids=[d.drone_id for d in drones],
            candidate_groups=groups,
            maximum_active_drones=request.maximum_active_drones,
            completion_deadline_minutes=request.completion_deadline_minutes,
            turnaround_minutes_by_drone={
                d.drone_id: max(request.turnaround_minutes, d.minimum_turnaround_minutes)
                for d in drones
            },
        )
        assigned = optimizer.optimize(problem)
        candidate = evaluate(segments, assigned.assignments)
        checked += 1
        if plan_is_audited(candidate) and scheduled_plan_objective(
            request, candidate
        ) < scheduled_plan_objective(request, best):
            best = candidate
        if (
            alternative is not None
            and improvement_allowed()
            and assignment_search_requested(request, len(drones), len(segments))
        ):
            candidate, report = AuditedAssignmentPortfolio(alternative).improve(
                request,
                problem,
                assigned,
                best,
                lambda result: evaluate(segments, result.assignments),
            )
            checked += int(report.status in ("accepted", "retained_baseline"))
            assert candidate.optimization is not None
            best = candidate.model_copy(
                update={
                    "optimization": candidate.optimization.model_copy(
                        update={"assignment_search": report}
                    )
                }
            )
    except PlanningError:
        pass
    return best, checked
