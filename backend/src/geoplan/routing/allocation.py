"""Finite-fleet assignment of fixed groups under the original objective."""

from time import perf_counter

from geoplan.routing.model import CostModel, Solution, Trip, variants


def assign_trips(model: CostModel, trips: Solution, deadline: float) -> Solution:
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
        current = min(
            ((*current, candidate) for _, candidate in options),
            key=lambda solution: model.score(solution, complete=False),
        )
    return current
