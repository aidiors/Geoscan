"""Adaptive selection of whole-sortie destroy operators; state is request-local."""

from dataclasses import dataclass, field
from math import hypot
from random import Random
from typing import Protocol

from geoplan.navigation.geometry import projection_for_coordinates
from geoplan.routing.model import Cost, CostModel, Solution, Trip


class SortieDestroyOperator(Protocol):
    @property
    def name(self) -> str: ...

    def select(self, model: CostModel, solution: Solution, random: Random) -> tuple[int, int]: ...


def _cost(model: CostModel, trip: Trip) -> Cost:
    cost = model.trip(trip)
    assert cost is not None, "Destroy operators require a feasible incumbent"
    return cost


def _centers(model: CostModel, solution: Solution) -> list[tuple[float, float]]:
    projection = projection_for_coordinates([track.start for track in model.problem.tracks])
    centers = []
    for trip in solution:
        points = [projection.project(p) for token in trip.visits for p in model.endpoints(token)]
        centers.append(
            (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        )
    return centers


@dataclass(frozen=True)
class CostlySortieRemoval:
    name: str = "deadhead"

    def select(self, model: CostModel, solution: Solution, random: Random) -> tuple[int, int]:
        ranked = sorted(
            range(len(solution)), key=lambda i: _cost(model, solution[i]).deadhead, reverse=True
        )
        first = ranked[0]
        return first, random.choice(ranked[1:6])


@dataclass(frozen=True)
class RelatedSortieRemoval:
    name: str = "related"

    def select(self, model: CostModel, solution: Solution, random: Random) -> tuple[int, int]:
        first = random.randrange(len(solution))
        centers = _centers(model, solution)
        ranked = sorted(
            (i for i in range(len(solution)) if i != first),
            key=lambda i: hypot(
                centers[i][0] - centers[first][0], centers[i][1] - centers[first][1]
            ),
        )
        return first, random.choice(ranked[:3])


@dataclass(frozen=True)
class CriticalLoadSortieRemoval:
    name: str = "critical_load"

    def select(self, model: CostModel, solution: Solution, random: Random) -> tuple[int, int]:
        loads: dict[int, float] = {}
        for trip in solution:
            turn = model.problem.aircraft[trip.aircraft].turnaround_minutes
            loads[trip.aircraft] = loads.get(trip.aircraft, -turn) + turn + _cost(model, trip).total
        critical = max(loads, key=lambda plane: loads[plane])
        first = max(
            (i for i, trip in enumerate(solution) if trip.aircraft == critical),
            key=lambda i: _cost(model, solution[i]).total,
        )
        centers = _centers(model, solution)
        partners = [i for i, trip in enumerate(solution) if trip.aircraft != critical]
        if not partners:
            partners = [i for i in range(len(solution)) if i != first]
        second = min(
            partners,
            key=lambda i: hypot(
                centers[i][0] - centers[first][0], centers[i][1] - centers[first][1]
            ),
        )
        return first, second


def sweep_order(model: CostModel, tokens: tuple[int, ...]) -> tuple[int, ...]:
    """Sort removed tracks by actual cross-track position, independent of task IDs."""
    projection = projection_for_coordinates([model.endpoints(token)[0] for token in tokens])
    a, b = (projection.project(p) for p in model.endpoints(tokens[0]))
    length = hypot(b[0] - a[0], b[1] - a[1]) or 1
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length

    def key(token):
        p, q = (projection.project(point) for point in model.endpoints(token))
        x, y = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        return -x * uy + y * ux, x * ux + y * uy, token // 2

    return tuple(token // 2 for token in sorted(tokens, key=key))


@dataclass
class AdaptiveDestroySelection:
    random: Random
    operators: tuple[SortieDestroyOperator, ...] = field(
        default_factory=lambda: (
            CostlySortieRemoval(),
            RelatedSortieRemoval(),
            CriticalLoadSortieRemoval(),
        )
    )
    reaction: float = 0.25
    weights: dict[str, float] = field(default_factory=dict)

    def choose(self, model: CostModel, solution: Solution, iteration: int):
        operator = (
            self.operators[iteration]
            if iteration < len(self.operators)
            else self.random.choices(
                self.operators,
                weights=[self.weights.get(op.name, 1.0) for op in self.operators],
                k=1,
            )[0]
        )
        return operator.name, operator.select(model, solution, self.random)

    def reward(self, name: str, *, improved: bool, feasible: bool) -> None:
        reward = 4.0 if improved else (1.0 if feasible else 0.1)
        self.weights[name] = (1 - self.reaction) * self.weights.get(
            name, 1.0
        ) + self.reaction * reward
