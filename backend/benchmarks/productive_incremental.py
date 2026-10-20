"""Delta route evaluation and fleet loads for the experimental repair operator."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, inf, radians
from time import perf_counter

from benchmarks.productive_model import Cost, CostModel, Solution, Trip


@dataclass(frozen=True)
class PartialLoads:
    costs: tuple[Cost, ...]
    loads: tuple[float, ...]
    counts: tuple[int, ...]
    total: float
    deadhead: float

    @classmethod
    def build(cls, model: CostModel, solution: Solution) -> PartialLoads:
        costs = []
        loads = [0.0] * len(model.problem.aircraft)
        counts = [0] * len(loads)
        for trip in solution:
            cost = model.trip(trip)
            if cost is None:
                raise ValueError("Partial solution contains an infeasible trip.")
            costs.append(cost)
            loads[trip.aircraft] += cost.total + (
                model.problem.aircraft[trip.aircraft].turnaround_minutes
                if counts[trip.aircraft]
                else 0
            )
            counts[trip.aircraft] += 1
        return cls(
            tuple(costs),
            tuple(loads),
            tuple(counts),
            sum(c.total for c in costs),
            sum(c.deadhead for c in costs),
        )

    def replacement_score(
        self, model: CostModel, aircraft: int, cost: Cost, index: int | None
    ) -> tuple[float, ...]:
        previous = self.costs[index] if index is not None else Cost()
        delta = cost.total - previous.total
        load = self.loads[aircraft] + delta
        if index is None and self.counts[aircraft]:
            load += model.problem.aircraft[aircraft].turnaround_minutes
        completion = max(load if i == aircraft else value for i, value in enumerate(self.loads))
        active = sum(c > 0 for c in self.counts) + int(index is None and not self.counts[aircraft])
        return model.objective(
            self.total + delta,
            self.deadhead + cost.deadhead - previous.deadhead,
            completion,
            active,
        )


def insertion_cost(
    model: CostModel, trip: Trip, cost: Cost, position: int, token: int
) -> Cost | None:
    """Replace at most one connector; include changes to departure and return."""
    if not model.service_allowed(trip.aircraft, token):
        return None
    model.insertion_evaluations += 1
    if not 0 <= position <= len(trip.visits):
        raise ValueError("Insertion position is outside the route.")
    plane = model.problem.aircraft[trip.aircraft]
    start, end = model.endpoints(token)
    service, distance = model.service(trip.aircraft, token)
    departure, returning = cost.departure, cost.returning
    connections, internal = cost.connections, cost.internal_distance + distance
    previous = model.endpoints(trip.visits[position - 1])[1] if position else None
    following = model.endpoints(trip.visits[position])[0] if position < len(trip.visits) else None
    if previous is None:
        departure = model.leg(trip.aircraft, plane.home, start)[0]
    else:
        duration, length = model.leg(trip.aircraft, previous, start)
        connections += duration
        internal += length
    if following is None:
        returning = model.leg(trip.aircraft, end, plane.home)[0]
    else:
        duration, length = model.leg(trip.aircraft, end, following)
        connections += duration
        internal += length
    if previous is not None and following is not None:
        duration, length = model.leg(trip.aircraft, previous, following)
        connections -= duration
        internal -= length
    result = Cost(cost.survey + service, departure, max(0.0, connections), returning, internal)
    return result if model.feasible_cost(trip.aircraft, result) else None


def insertion_fast(
    model: CostModel,
    remaining: set[int],
    initial: Solution,
    deadline: float,
    regret: int = 2,
    *,
    route_limit: int | None = 6,
) -> Solution:
    current = initial
    if any(model.trip(trip) is None for trip in initial):
        return ()
    scale = cos(radians(model.problem.tracks[0].start.latitude))
    centers = tuple(
        ((t.start.longitude + t.end.longitude) * scale / 2, (t.start.latitude + t.end.latitude) / 2)
        for t in model.problem.tracks
    )
    while remaining:
        if perf_counter() >= deadline:
            return ()
        state = PartialLoads.build(model, current)
        choices = []
        for task in sorted(remaining):
            # One best placement per existing route or new-aircraft resource.
            options: list[tuple[tuple[float, ...], int | None, Trip]] = []
            for plane in range(len(model.problem.aircraft)):
                placements = []
                for flag in (0, 1):
                    trip = Trip(plane, (2 * task + flag,))
                    cost = model.trip(trip)
                    if cost is not None:
                        placements.append(
                            (state.replacement_score(model, plane, cost, None), None, trip)
                        )
                if placements:
                    options.append(min(placements, key=lambda p: p[0]))
            indices = list(range(len(current)))
            if (
                route_limit is not None
                and len(model.problem.tracks) > 50
                and len(indices) > route_limit
            ):
                x, y = centers[task]
                indices = sorted(
                    sorted(
                        indices,
                        key=lambda i: min(
                            (centers[t // 2][0] - x) ** 2 + (centers[t // 2][1] - y) ** 2
                            for t in current[i].visits
                        ),
                    )[:route_limit]
                )
            for index in indices:
                trip = current[index]
                best = None
                for position in range(len(trip.visits) + 1):
                    for flag in (0, 1):
                        if perf_counter() >= deadline:
                            return ()
                        token = 2 * task + flag
                        cost = insertion_cost(model, trip, state.costs[index], position, token)
                        if cost is None:
                            continue
                        score = state.replacement_score(model, trip.aircraft, cost, index)
                        if best is None or score < best[0]:
                            visits = (*trip.visits[:position], token, *trip.visits[position:])
                            best = score, index, Trip(trip.aircraft, visits)
                    if perf_counter() >= deadline:
                        return ()
                if best is not None:
                    options.append(best)
            if not options:
                return ()
            options.sort(key=lambda p: p[0])
            gap = tuple(
                a - b
                for a, b in zip(
                    options[min(regret - 1, len(options) - 1)][0], options[0][0], strict=True
                )
            )
            priority = (inf,) * len(gap) if len(options) < regret else gap
            choices.append((priority, task, options[0]))
            if perf_counter() >= deadline:
                return ()
        _, task, (_, index, trip) = max(choices, key=lambda p: (p[0], -p[1]))
        if model.trip(trip) is None:
            return ()
        current = (
            (*current, trip) if index is None else (*current[:index], trip, *current[index + 1 :])
        )
        remaining.remove(task)
    return current
