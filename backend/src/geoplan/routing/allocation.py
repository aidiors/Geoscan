"""Finite-fleet assignment of fixed groups under the original objective."""

from time import perf_counter

from geoplan.domain.enums import OptimizationCriterion
from geoplan.routing.model import CostModel, RoutingSearchObjective, Solution, Trip, variants


def assign_trips(
    model: CostModel,
    trips: Solution,
    deadline: float,
    maximum_active_drones: int | None = None,
    *,
    criterion: OptimizationCriterion | None = None,
    search_objective: RoutingSearchObjective | None = None,
) -> Solution:
    """LPT construction over fixed groups, choosing aircraft by the original objective."""
    groups = []
    for trip in trips:
        options = []
        for plane in range(len(model.problem.aircraft)):
            for visits in variants(trip.visits):
                if perf_counter() >= deadline:
                    return ()
                candidate = Trip(plane, visits)
                cost = model.trip(candidate)
                if cost is not None:
                    options.append((cost, candidate))
        if not options:
            return ()
        groups.append((min(cost.total for cost, _ in options), options))
    current: Solution = ()
    loads: dict[int, float] = {}
    total = deadhead = completion = 0.0
    for _, options in sorted(groups, key=lambda group: group[0], reverse=True):
        if perf_counter() >= deadline:
            return ()
        allowed = [
            (cost, candidate)
            for cost, candidate in options
            if maximum_active_drones is None
            or len(loads) < maximum_active_drones
            or candidate.aircraft in loads
        ]
        if not allowed:
            return ()

        def added_load(option):
            cost, candidate = option
            plane = candidate.aircraft
            turn = model.problem.aircraft[plane].turnaround_minutes if plane in loads else 0.0
            return loads.get(plane, 0.0) + (cost.total + turn)

        # Every option has already passed the canonical trip feasibility check.
        # Only one aircraft load and the two sums change; rescoring the entire
        # prefix here used to dominate the destroy/repair search budget.
        cost, chosen = min(
            allowed,
            key=lambda option: model.objective(
                total + option[0].total,
                deadhead + option[0].deadhead,
                max(completion, added_load(option)),
                len(loads) + int(option[1].aircraft not in loads),
                criterion,
                search_objective,
            ),
        )
        loads[chosen.aircraft] = added_load((cost, chosen))
        completion = max(completion, loads[chosen.aircraft])
        total += cost.total
        deadhead += cost.deadhead
        current = (*current, chosen)
    return current
