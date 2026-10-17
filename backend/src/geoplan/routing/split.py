"""Battery-bounded groups over fixed raw tracks; original fleet objective selects the result."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import inf
from time import perf_counter

from geoplan.routing.model import Cost, CostModel, Solution, Trip


@dataclass(frozen=True)
class Arc:
    end: int
    trip: Trip
    cost: Cost


def representatives(model: CostModel) -> tuple[int, ...]:
    """Remove a plane only when another has the same base/eligibility and dominates resources."""
    fleet = model.problem.aircraft
    return tuple(
        i
        for i, plane in enumerate(fleet)
        if not any(
            i != j
            and plane.home == other.home
            and plane.landing_coordinate == other.landing_coordinate
            and plane.eligible_tracks == other.eligible_tracks
            and other.speed_kmh >= plane.speed_kmh
            and other.available_minutes >= plane.available_minutes
            and (
                other.speed_kmh > plane.speed_kmh
                or other.available_minutes > plane.available_minutes
                or j < i
            )
            for j, other in enumerate(fleet)
        )
    )


def route_catalog(model: CostModel, order: tuple[int, ...], deadline: float) -> list[list[Arc]]:
    """Two endpoint labels optimize orientation for each consecutive group by elapsed time.

    With a distance cap the single label per endpoint is a heuristic; it need not
    retain every time/distance tradeoff. Selected solutions are canonically recosted.
    """
    model.catalog_complete = False
    catalog: list[list[Arc]] = [[] for _ in order]
    planes = representatives(model)
    for start in range(len(order)):
        for plane in planes:
            aircraft = model.problem.aircraft[plane]
            labels: list[tuple[Cost, tuple[int, ...]] | None] = [None, None]
            for end in range(start, len(order)):
                if perf_counter() >= deadline:
                    return catalog
                task = order[end]
                if not model.service_allowed(plane, 2 * task):
                    break
                updated = []
                for orientation in (0, 1):
                    token = 2 * task + orientation
                    first, _ = model.endpoints(token)
                    survey, distance = model.service(plane, token)
                    options = []
                    if end == start:
                        departure = model.leg(plane, aircraft.home, first)[0]
                        options.append((Cost(survey, departure, 0, 0, distance), (token,)))
                    else:
                        for label in labels:
                            if label is None:
                                continue
                            cost, visits = label
                            previous = model.endpoints(visits[-1])[1]
                            duration, length = model.leg(plane, previous, first)
                            options.append(
                                (
                                    Cost(
                                        cost.survey + survey,
                                        cost.departure,
                                        cost.connections + duration,
                                        0,
                                        cost.internal_distance + distance + length,
                                    ),
                                    (*visits, token),
                                )
                            )
                    options = [label for label in options if model.feasible_cost(plane, label[0])]
                    updated.append(
                        min(options, key=lambda label: label[0].total) if options else None
                    )
                labels = updated
                if not any(label is not None for label in labels):
                    break  # Accumulated time/distance only increase on extension.
                closed = []
                for label in labels:
                    if label is None:
                        continue
                    cost, visits = label
                    tail = model.leg(
                        plane, model.endpoints(visits[-1])[1], aircraft.landing_coordinate
                    )[0]
                    cost = replace(cost, returning=tail)
                    if model.feasible_cost(plane, cost):
                        closed.append((cost, visits))
                if closed:
                    cost, visits = min(closed, key=lambda label: label[0].total)
                    catalog[start].append(Arc(end + 1, Trip(plane, visits), cost))
                    model.catalog_arcs += 1
    model.catalog_complete = True
    return catalog


def decode(catalog: list[list[Arc]], maximum_minutes: float, deadline: float) -> Solution:
    n = len(catalog)
    costs = [0.0, *([inf] * n)]
    parents: list[tuple[int, Trip] | None] = [None] * (n + 1)
    for start, arcs in enumerate(catalog):
        if perf_counter() >= deadline:
            return ()
        if costs[start] == inf:
            continue
        for arc in arcs:
            value = costs[start] + arc.cost.total
            if arc.cost.total <= maximum_minutes and value < costs[arc.end]:
                costs[arc.end] = value
                parents[arc.end] = start, arc.trip
    solution = []
    position = n
    while position:
        parent = parents[position]
        if parent is None:
            return ()
        position, trip = parent
        solution.append(trip)
    return tuple(reversed(solution))
