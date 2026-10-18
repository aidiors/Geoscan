"""Bounded destroy/repair of whole sortie groups over unchanged survey tracks."""

from dataclasses import dataclass
from itertools import combinations
from random import Random
from time import perf_counter

from geoplan.routing.allocation import assign_trips
from geoplan.routing.destroy import AdaptiveDestroySelection, sweep_order
from geoplan.routing.model import CostModel, RoutingSearchObjective, Solution
from geoplan.routing.split import decode, route_catalog


@dataclass(frozen=True)
class SortieNeighborhoodSearch:
    maximum_iterations: int = 6
    maximum_removed_tracks: int = 64
    maximum_results: int = 2
    adaptive: bool = False
    random_seed: int = 0
    exchange_original_objective: bool = False

    def improve(
        self,
        model: CostModel,
        incumbent: Solution,
        targets: tuple[float, ...],
        maximum_active_drones: int,
        deadline: float,
        *,
        search_objective: RoutingSearchObjective | None = None,
    ) -> list[Solution]:
        if model.metrics(incumbent) is None:
            return []
        current = incumbent
        results: list[Solution] = []
        selection = AdaptiveDestroySelection(Random(self.random_seed))

        def score(solution):
            return model.score(solution, search_objective=search_objective)

        def retain(values: list[Solution]) -> list[Solution]:
            ranked = sorted(values, key=score)
            if not self.exchange_original_objective or self.maximum_results < 3:
                return ranked[: self.maximum_results]
            # Keep the two guide-objective leaders and the best original-objective
            # exchange. Further auxiliary improvement must not discard a useful
            # intermediate grouping just because it has more total flight time.
            selected = ranked[:2]
            for candidate in sorted(values, key=model.score):
                if candidate not in selected:
                    selected.append(candidate)
                if len(selected) >= self.maximum_results:
                    break
            return selected

        # Every iteration destroys two whole sorties and reconstructs their
        # boundaries and orientations. Other sorties keep their track groups.
        for iteration in range(self.maximum_iterations):
            if perf_counter() >= deadline or len(current) < 2:
                break
            if self.adaptive:
                operator, (first, second) = selection.choose(model, current, iteration)
            else:
                operator = "legacy"
                ranked = sorted(
                    range(len(current)),
                    key=lambda i: self._priority(model, current[i], iteration),
                    reverse=True,
                )[:6]
                pairs = list(combinations(ranked, 2))
                first, second = pairs[iteration % len(pairs)]
            model.neighborhood_operator_attempts[operator] = (
                model.neighborhood_operator_attempts.get(operator, 0) + 1
            )
            removed = (*current[first].visits, *current[second].visits)
            if len(removed) > self.maximum_removed_tracks:
                selection.reward(operator, improved=False, feasible=False)
                continue
            model.sortie_destroy_attempts += 1
            # Original concatenation keeps route locality; raw track ordering
            # allows interleaved groups to be regrouped across old boundaries.
            order = tuple(token // 2 for token in removed)
            if iteration % 2:
                order = sweep_order(model, removed) if self.adaptive else tuple(sorted(order))
            catalog = route_catalog(model, order, deadline)
            survivors = tuple(t for i, t in enumerate(current) if i not in (first, second))
            feasible = improved = False
            for target in targets:
                if perf_counter() >= deadline:
                    break
                repaired = decode(catalog, target, deadline)
                if not repaired:
                    continue
                candidate = assign_trips(
                    model,
                    (*survivors, *repaired),
                    deadline,
                    maximum_active_drones,
                    search_objective=search_objective,
                )
                if not candidate or model.metrics(candidate) is None:
                    continue
                model.sortie_repair_completed += 1
                feasible = True
                if score(candidate) < score(current):
                    improved = True
                    current = candidate
                    if candidate not in results:
                        results.append(candidate)
                    if self.exchange_original_objective:
                        exchanged = assign_trips(model, candidate, deadline, maximum_active_drones)
                        if exchanged and exchanged not in results:
                            results.append(exchanged)
            selection.reward(operator, improved=improved, feasible=feasible)
            if improved:
                model.neighborhood_operator_improvements[operator] = (
                    model.neighborhood_operator_improvements.get(operator, 0) + 1
                )
            if self.adaptive:
                results = retain(results)
            elif len(results) >= self.maximum_results:
                break
        return retain(results)

    @staticmethod
    def _priority(model, trip, iteration):
        cost = model.trip(trip)
        return (cost.total if iteration % 2 == 0 else cost.deadhead) if cost else 0
