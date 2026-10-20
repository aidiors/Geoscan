"""Battery-bounded groups over fixed raw tracks; original fleet objective selects the result."""

from __future__ import annotations

from time import perf_counter

from benchmarks.productive_model import CostModel, Solution
from benchmarks.productive_solvers import assign_trips
from geoplan.domain.enums import OptimizationCriterion
from geoplan.routing.split import Arc, decode, representatives, route_catalog


def adaptive(
    model: CostModel,
    initial: Solution,
    deadline: float,
    *,
    order: tuple[int, ...] | None = None,
    catalog_fraction: float = 0.6,
) -> Solution:
    start = perf_counter()
    catalog = route_catalog(
        model,
        order if order is not None else tuple(range(len(model.problem.tracks))),
        start + max(0, deadline - start) * catalog_fraction,
    )
    best = initial
    physical = max(plane.available_minutes for plane in model.problem.aircraft)
    targets = [physical]
    if model.problem.criterion is not OptimizationCriterion.TOTAL_FLIGHT_TIME:
        minimum_survey = sum(
            min(
                model.service(plane, 2 * task + flag)[0]
                for plane in range(len(model.problem.aircraft))
                for flag in (0, 1)
                if model.service_allowed(plane, 2 * task + flag)
            )
            for task in range(len(model.problem.tracks))
        )
        average = minimum_survey / len(model.problem.aircraft)
        balanced = [min(physical, average * factor) for factor in (1.15, 1.3, 1.05, 1.5)]
        targets = (
            balanced + targets
            if model.problem.criterion is OptimizationCriterion.COMPLETION_TIME
            else targets + balanced
        )
    for target in dict.fromkeys(targets):
        if perf_counter() >= deadline:
            break
        solution = decode(catalog, target, deadline)
        if not solution:
            continue
        model.decoded_candidates += 1
        allocated = assign_trips(model, solution, deadline)
        best = min((best, solution, allocated), key=model.score)
    return best


__all__ = ["Arc", "adaptive", "decode", "representatives", "route_catalog"]
