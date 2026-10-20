"""Bounded exchange of structurally different candidates between search objectives."""

from dataclasses import dataclass

from geoplan.domain.enums import OptimizationCriterion
from geoplan.routing.model import CostModel, RoutingSearchObjective, Solution


@dataclass(frozen=True)
class CrossObjectiveCandidates:
    maximum_candidates: int = 4
    fallback_deadline_factor: float = 1.15

    def guidance(self, model: CostModel, incumbent: list[tuple[str, Solution]]):
        if model.problem.criterion is OptimizationCriterion.BALANCED:
            return RoutingSearchObjective(OptimizationCriterion.COMPLETION_TIME)
        if model.problem.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
            return RoutingSearchObjective(OptimizationCriterion.COMPLETION_TIME)
        deadline = model.problem.deadline_minutes
        if deadline is None:
            # This threshold guides construction only. It never becomes a
            # mission deadline or enters the final plan comparison.
            completion = float("inf")
            for _, solution in incumbent:
                metrics = model.metrics(solution)
                assert metrics is not None
                completion = min(completion, metrics["load_makespan_minutes"])
            deadline = completion * self.fallback_deadline_factor
        return RoutingSearchObjective(OptimizationCriterion.BALANCED, deadline)

    def select(
        self,
        model: CostModel,
        candidates: list[tuple[str, Solution]],
        guidance: RoutingSearchObjective,
    ) -> list[tuple[str, Solution]]:
        if self.maximum_candidates <= 0:
            return []
        selected: list[tuple[str, Solution]] = []
        seen: set[Solution] = set()
        rankings = (
            lambda solution: model.score(solution, search_objective=guidance),
            model.score,
            lambda solution: model.score(solution, criterion=OptimizationCriterion.COMPLETION_TIME),
            lambda solution: model.score(
                solution, criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME
            ),
        )
        ordered = [sorted(candidates, key=lambda item: score(item[1])) for score in rankings]
        for rank in range(len(candidates)):
            for ordering in ordered:
                name, solution = ordering[rank]
                if solution not in seen:
                    seen.add(solution)
                    selected.append((name, solution))
                    if len(selected) >= self.maximum_candidates:
                        return selected
        return selected
