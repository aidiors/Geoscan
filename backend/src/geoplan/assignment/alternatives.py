"""Optional assignment solver port, independent of scheduling and HTTP."""

from dataclasses import dataclass
from typing import Protocol

from geoplan.assignment.models import AssignmentOptimizationRequest, AssignmentOptimizationResult
from geoplan.domain.enums import AssignmentAlgorithm, OptimalityStatus


@dataclass(frozen=True)
class AssignmentAlternative:
    result: AssignmentOptimizationResult | None
    status: str
    solver_statuses: tuple[str, ...] = ()
    runtime_seconds: float = 0
    integer_assignment_proven: bool = False


class AssignmentAlternativeProvider(Protocol):
    def propose(
        self, request: AssignmentOptimizationRequest, incumbent: AssignmentOptimizationResult
    ) -> AssignmentAlternative: ...


@dataclass(frozen=True)
class CpSatAssignmentAlternative:
    time_limit_seconds: float = 2
    random_seed: int = 0

    def propose(
        self, request: AssignmentOptimizationRequest, incumbent: AssignmentOptimizationResult
    ) -> AssignmentAlternative:
        # Core planning remains usable without the optional optimization extra.
        try:
            from geoplan.assignment.cp_sat import CpSatAssignmentSolver
        except ModuleNotFoundError as error:
            if error.name != "ortools" and not (error.name or "").startswith("ortools."):
                raise
            return AssignmentAlternative(None, "unavailable")
        by_segment = {candidate.segment_id: candidate for candidate in incumbent.assignments}
        choices = tuple(
            group.index(by_segment[group[0].segment_id]) for group in request.candidate_groups
        )
        solved = CpSatAssignmentSolver(
            time_limit_seconds=self.time_limit_seconds, random_seed=self.random_seed
        ).solve(request, choices)
        evaluated = solved.evaluation
        result = None
        if evaluated is not None:
            result = AssignmentOptimizationResult(
                assignments=[
                    group[index]
                    for group, index in zip(
                        request.candidate_groups, evaluated.choices, strict=True
                    )
                ],
                algorithm=AssignmentAlgorithm.CP_SAT,
                # Integer assignment proof does not prove a scheduled flight plan optimal.
                optimality=OptimalityStatus.HEURISTIC,
                completion_minutes=evaluated.completion_minutes,
                total_flight_minutes=evaluated.total_minutes,
                active_drone_count=evaluated.active_drones,
            )
        return AssignmentAlternative(
            result=result,
            status="candidate" if result is not None else "no_solution",
            solver_statuses=solved.statuses,
            runtime_seconds=solved.runtime_seconds,
            integer_assignment_proven=solved.proven_lexicographic,
        )
