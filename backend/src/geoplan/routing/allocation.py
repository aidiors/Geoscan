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
                    options.append((cost.total, candidate))
        if not options:
            return ()
        groups.append((min(value for value, _ in options), options))
    current: Solution = ()
    for _, options in sorted(groups, key=lambda group: group[0], reverse=True):
        if perf_counter() >= deadline:
            return ()
        active = {trip.aircraft for trip in current}
        allowed = [
            candidate
            for _, candidate in options
            if maximum_active_drones is None
            or len(active) < maximum_active_drones
            or candidate.aircraft in active
        ]
        if not allowed:
            return ()
        current = min(
            ((*current, candidate) for candidate in allowed),
            key=lambda solution: model.score(
                solution, complete=False, criterion=criterion, search_objective=search_objective
            ),
        )
    return current
