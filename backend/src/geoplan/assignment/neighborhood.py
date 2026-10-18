"""Bounded relocation and exchange search over already feasible missions."""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

from geoplan.assignment.models import AssignmentOptimizationRequest


@dataclass(frozen=True)
class AssignmentNeighborhoodSearch:
    maximum_rounds: int = 5
    maximum_exchange_tasks: int = 24

    def improve(
        self,
        request: AssignmentOptimizationRequest,
        assignment: list[int],
        allowed_drones: frozenset[str],
        score: Callable[[list[int]], tuple[float, ...]],
    ) -> list[int]:
        current = list(assignment)
        best = score(current)
        for _ in range(self.maximum_rounds):
            improved = False
            for task, group in enumerate(request.candidate_groups):
                for option, candidate in enumerate(group):
                    if option == current[task] or candidate.drone_id not in allowed_drones:
                        continue
                    trial = current.copy()
                    trial[task] = option
                    value = score(trial)
                    if value < best:
                        current, best, improved = trial, value, True
                        break
            if improved:
                continue
            # Exchanges can leave a relocation local minimum without accepting
            # a worse intermediate plan. Long missions get the bounded budget.
            tasks = sorted(
                range(len(current)),
                key=lambda i: (
                    -request.candidate_groups[i][current[i]].mission.duration_minutes,
                    i,
                ),
            )[: self.maximum_exchange_tasks]
            for first, second in combinations(tasks, 2):
                a = request.candidate_groups[first][current[first]].drone_id
                b = request.candidate_groups[second][current[second]].drone_id
                if a == b:
                    continue
                choices = []
                for task, drone in ((first, b), (second, a)):
                    options = [
                        (c.mission.duration_minutes, i)
                        for i, c in enumerate(request.candidate_groups[task])
                        if c.drone_id == drone
                    ]
                    if not options:
                        break
                    choices.append(min(options)[1])
                if len(choices) != 2:
                    continue
                trial = current.copy()
                trial[first], trial[second] = choices
                value = score(trial)
                if value < best:
                    current, best, improved = trial, value, True
                    break
            if not improved:
                break
        return current
