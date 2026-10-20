"""Choose alternatives only after scheduling and independent safety audit."""

from collections.abc import Callable
from dataclasses import dataclass

from geoplan.assignment.alternatives import AssignmentAlternativeProvider
from geoplan.assignment.models import AssignmentOptimizationRequest, AssignmentOptimizationResult
from geoplan.domain.enums import AssignmentStrategy, OptimizationCriterion, OptimizationMode
from geoplan.errors import PlanningError
from geoplan.planning.models import AssignmentSearchSummary, PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective


def assignment_search_requested(request: PlanRequest, drone_count: int, segment_count: int) -> bool:
    if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
        return False
    return request.assignment_strategy is AssignmentStrategy.CP_SAT_PORTFOLIO or (
        request.assignment_strategy is AssignmentStrategy.AUTOMATIC
        and request.criterion is not OptimizationCriterion.TOTAL_FLIGHT_TIME
        and drone_count > 1
        and segment_count > 1
    )


@dataclass(frozen=True)
class AuditedAssignmentPortfolio:
    provider: AssignmentAlternativeProvider

    def improve(
        self,
        request: PlanRequest,
        problem: AssignmentOptimizationRequest,
        incumbent: AssignmentOptimizationResult,
        baseline: PlanResponse,
        evaluate: Callable[[AssignmentOptimizationResult], PlanResponse],
    ) -> tuple[PlanResponse, AssignmentSearchSummary]:
        alternative = self.provider.propose(problem, incumbent)
        report = AssignmentSearchSummary(
            status=alternative.status,
            solver_statuses=list(alternative.solver_statuses),
            runtime_seconds=alternative.runtime_seconds,
            integer_assignment_proven=alternative.integer_assignment_proven,
            message="Дополнительный решатель не вернул назначения; сохранён исходный план.",
        )
        if alternative.result is None:
            if alternative.status == "unavailable":
                report = report.model_copy(
                    update={"message": "CP-SAT не установлен на сервере; сохранён исходный план."}
                )
            return baseline, report
        try:
            candidate = evaluate(alternative.result)
        except PlanningError:
            return baseline, report.model_copy(
                update={
                    "status": "schedule_rejected",
                    "message": "Расписание CP-SAT отклонено; сохранён исходный план.",
                }
            )
        audited = plan_is_audited(candidate)
        accepted = audited and (
            not plan_is_audited(baseline)
            or scheduled_plan_objective(request, candidate)
            < scheduled_plan_objective(request, baseline)
        )
        return (candidate if accepted else baseline), report.model_copy(
            update={
                "status": "accepted" if accepted else "retained_baseline",
                "candidate_audited": audited,
                "candidate_accepted": accepted,
                "message": (
                    "Назначение CP-SAT улучшило исходное расписание и прошло аудит."
                    if accepted
                    else "Исходное расписание лучше, равноценное либо безопаснее варианта CP-SAT."
                ),
            }
        )
