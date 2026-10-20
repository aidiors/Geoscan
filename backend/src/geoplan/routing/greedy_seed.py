"""Complete fleet seeds before expensive heading-aware catalogs consume their budget."""

from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from geoplan.routing.model import CostModel, RoutingSearchObjective, Solution, Trip


@dataclass(frozen=True)
class FleetSweepSeed:
    def generate(
        self,
        model: CostModel,
        order: tuple[int, ...],
        target_minutes: float,
        fleet_limit: int,
        deadline: float,
        objective: RoutingSearchObjective,
    ) -> Solution:
        result: Solution = ()
        cursor = 0
        while cursor < len(order):
            active = {trip.aircraft for trip in result}
            options = []
            for plane in range(len(model.problem.aircraft)):
                if plane not in active and len(active) >= fleet_limit:
                    continue
                for orientation in (0, 1):
                    if perf_counter() >= deadline:
                        return ()
                    trip = Trip(plane, (2 * order[cursor] + orientation,))
                    if isfinite(
                        model.score((*result, trip), complete=False, search_objective=objective)[0]
                    ):
                        options.append(trip)
            if not options:
                return ()
            chosen = min(
                options,
                key=lambda trip: model.score(
                    (*result, trip), complete=False, search_objective=objective
                ),
            )
            cursor += 1
            while cursor < len(order):
                if perf_counter() >= deadline:
                    return ()
                extensions = []
                for orientation in (0, 1):
                    trip = Trip(chosen.aircraft, (*chosen.visits, 2 * order[cursor] + orientation))
                    cost = model.trip(trip)
                    if (
                        cost is not None
                        and cost.total <= target_minutes
                        and isfinite(
                            model.score(
                                (*result, trip), complete=False, search_objective=objective
                            )[0]
                        )
                    ):
                        extensions.append((cost.total, trip.visits, trip))
                if not extensions:
                    break
                chosen = min(extensions)[2]
                cursor += 1
            result = (*result, chosen)
        return result if model.metrics(result) is not None else ()
