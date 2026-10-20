"""Bounded, criterion-aware alternatives over unchanged survey tracks."""

from dataclasses import dataclass, field, replace
from math import isfinite
from time import perf_counter
from typing import Protocol

from geoplan.domain.enums import OptimizationCriterion
from geoplan.routing.allocation import assign_trips
from geoplan.routing.cross_objective import CrossObjectiveCandidates
from geoplan.routing.greedy_seed import FleetSweepSeed
from geoplan.routing.model import CostModel, RoutingProblem, RoutingSearchObjective, Solution
from geoplan.routing.neighborhood import SortieNeighborhoodSearch
from geoplan.routing.ordering import cell_orders, order_cost
from geoplan.routing.split import Arc, decode, route_catalog


@dataclass(frozen=True)
class RoutePoolBatch:
    primary: tuple[tuple[str, Solution], ...] = ()
    cross_objective: tuple[tuple[str, Solution], ...] = ()

    @property
    def all(self) -> list[tuple[str, Solution]]:
        return [*self.primary, *self.cross_objective]


class RoutePoolGenerator(Protocol):
    def generate(
        self, model: CostModel[RoutingProblem], maximum_active_drones: int | None = None
    ) -> RoutePoolBatch: ...


@dataclass(frozen=True)
class ProductiveRoutePool:
    maximum_orders: int = 3
    maximum_candidates: int = 20
    maximum_primary_candidates: int = 16
    cross_objective_seconds: float = 4
    cross_objective_candidates: CrossObjectiveCandidates = field(
        default_factory=CrossObjectiveCandidates
    )
    maximum_catalog_candidates: int = 10
    seconds_per_catalog: float = 2
    seconds_per_decode: float = 0.5
    total_seconds: float = 8
    neighborhood_seconds: float = 1.5
    maximum_neighborhood_candidates: int = 2
    neighborhood_search: SortieNeighborhoodSearch = field(default_factory=SortieNeighborhoodSearch)
    adaptive_neighborhood_seconds: float = 0.75
    adaptive_neighborhood_search: SortieNeighborhoodSearch = field(
        default_factory=lambda: SortieNeighborhoodSearch(adaptive=True, maximum_iterations=24)
    )
    completion_seed_seconds: float = 0.75
    maximum_completion_candidates: int = 2

    def generate(
        self, model: CostModel[RoutingProblem], maximum_active_drones: int | None = None
    ) -> RoutePoolBatch:
        catalogs: dict[tuple[int, ...], list[list[Arc]]] = {}
        primary = replace(
            self, maximum_candidates=min(self.maximum_candidates, self.maximum_primary_candidates)
        )
        guidance = RoutingSearchObjective(model.problem.criterion, model.problem.deadline_minutes)
        pool = primary._generate_for(model, maximum_active_drones, guidance, catalogs)
        original = tuple(pool)
        if not pool or self.cross_objective_seconds <= 0 or len(pool) >= self.maximum_candidates:
            return RoutePoolBatch(primary=original)
        auxiliary = self.cross_objective_candidates.guidance(model, pool)
        exchange_pool = replace(
            primary,
            maximum_candidates=primary.maximum_candidates + 2,
            maximum_neighborhood_candidates=max(3, primary.maximum_neighborhood_candidates),
            adaptive_neighborhood_search=replace(
                primary.adaptive_neighborhood_search, exchange_original_objective=True
            ),
        )
        alternatives = exchange_pool._generate_for(
            model,
            maximum_active_drones,
            auxiliary,
            catalogs,
            stopping_at=perf_counter() + self.cross_objective_seconds,
        )
        seen = {solution for _, solution in pool}
        fresh = [(name, solution) for name, solution in alternatives if solution not in seen]
        for name, solution in self.cross_objective_candidates.select(model, fresh, auxiliary):
            if len(pool) >= self.maximum_candidates:
                break
            pool.append((f"cross-{auxiliary.criterion.value}-{name}", solution))
        return RoutePoolBatch(primary=original, cross_objective=tuple(pool[len(original) :]))

    def _generate_for(
        self,
        model: CostModel[RoutingProblem],
        maximum_active_drones: int | None,
        objective: RoutingSearchObjective,
        catalogs: dict[tuple[int, ...], list[list[Arc]]],
        stopping_at: float = float("inf"),
    ) -> list[tuple[str, Solution]]:
        started = perf_counter()
        construction_end = min(started + self.total_seconds, stopping_at)

        def score(solution):
            return model.score(solution, search_objective=objective)

        orders = sorted(cell_orders(model), key=lambda order: (order_cost(model, order), order))
        pool: list[tuple[str, Solution]] = []
        seen: set[Solution] = set()
        fleet_limit = min(
            maximum_active_drones or len(model.problem.aircraft), len(model.problem.aircraft)
        )
        targets = self.duration_targets(model, fleet_limit, objective)
        curved = any(plane.minimum_turn_radius_m > 0 for plane in model.problem.aircraft)
        finite_fleet = any(
            plane.resource_limit is not None and plane.resource_limit.is_bounded
            for plane in model.problem.aircraft
        )
        if orders and (curved or finite_fleet):
            seed_end = min(construction_end, perf_counter() + 2)
            # Long groups save transit; shorter groups expose parallel schedules.
            for target in (targets[0], min(targets, key=lambda value: abs(value - targets[0] / 2))):
                if len(pool) >= self.maximum_candidates:
                    break
                seed = FleetSweepSeed().generate(
                    model, orders[0], target, fleet_limit, seed_end, objective
                )
                if seed and seed not in seen:
                    seen.add(seed)
                    label = "curved" if curved else "resource"
                    pool.append((f"{label}-sweep-seed-{len(pool)}", seed))
        contexts: list[tuple[int, list[list[Arc]], int]] = []
        catalog_limit = min(self.maximum_candidates, self.maximum_catalog_candidates)

        def append_variants(order_index: int, target_index: int, catalog: list[list[Arc]]) -> None:
            if len(pool) >= catalog_limit or perf_counter() >= construction_end:
                return
            solution = decode(
                catalog,
                targets[target_index],
                min(stopping_at, perf_counter() + self.seconds_per_decode),
            )
            if not solution:
                return
            allocated = assign_trips(
                model,
                solution,
                min(stopping_at, perf_counter() + self.seconds_per_decode),
                maximum_active_drones=fleet_limit,
                search_objective=objective,
            )
            prefix = "" if order_index == 0 else f"order-{order_index}-"
            if target_index == 0:
                variants = ((prefix + "catalog", solution), (prefix + "allocated", allocated))
            else:
                variants = ((f"split-{order_index}-{target_index}", allocated),)
            for name, candidate in variants:
                if len(pool) >= catalog_limit:
                    return
                if (
                    candidate
                    and candidate not in seen
                    and len({trip.aircraft for trip in candidate}) <= fleet_limit
                    and model.metrics(candidate) is not None
                ):
                    seen.add(candidate)
                    pool.append((name, candidate))

        for order_index, order in enumerate(orders[: self.maximum_orders]):
            if perf_counter() >= construction_end:
                break
            catalog = catalogs.get(order)
            if catalog is None:
                catalog = route_catalog(
                    model, order, min(construction_end, perf_counter() + self.seconds_per_catalog)
                )
                if model.catalog_complete:
                    catalogs[order] = catalog
            prefix_count = 1
            if (
                order_index == 0
                and objective.criterion is not OptimizationCriterion.TOTAL_FLIGHT_TIME
                and max(p.available_minutes for p in model.problem.aircraft) > 60
            ):
                prefix_count = min(2, len(targets))
            # Finish the original alternatives before expanding other orders.
            for target_index in range(prefix_count):
                append_variants(order_index, target_index, catalog)
            contexts.append((order_index, catalog, prefix_count))
        # Spend the remaining budget across orders rather than exhausting it on
        # every duration threshold of the first order.
        for offset in range(len(targets)):
            for order_index, catalog, prefix_count in contexts:
                target_index = prefix_count + offset
                if target_index < len(targets):
                    append_variants(order_index, target_index, catalog)

        def append_neighborhood(
            search: SortieNeighborhoodSearch, seconds: float, label: str
        ) -> None:
            remaining = min(
                self.maximum_neighborhood_candidates, self.maximum_candidates - len(pool)
            )
            if not pool or seconds <= 0 or remaining <= 0:
                return
            seed = min((candidate for _, candidate in pool), key=score)
            improved = replace(search, maximum_results=remaining).improve(
                model,
                seed,
                targets,
                fleet_limit,
                min(stopping_at, perf_counter() + seconds),
                search_objective=objective,
            )
            for index, candidate in enumerate(improved):
                if candidate not in seen:
                    seen.add(candidate)
                    pool.append((f"{label}-{index}", candidate))

        append_neighborhood(self.neighborhood_search, self.neighborhood_seconds, "destroy-repair")
        # Keep every earlier candidate: a better load proxy may yield a worse
        # actual schedule. The planning layer audits and compares both pools.
        append_neighborhood(
            self.adaptive_neighborhood_search, self.adaptive_neighborhood_seconds, "adaptive-repair"
        )
        if (
            objective.criterion is OptimizationCriterion.BALANCED
            and self.completion_seed_seconds > 0
            and len(pool) < self.maximum_candidates
        ):
            deadline = min(stopping_at, perf_counter() + self.completion_seed_seconds)
            quick: set[Solution] = set()
            for _, candidate in tuple(pool):
                if perf_counter() >= deadline:
                    break
                allocated = assign_trips(
                    model,
                    candidate,
                    deadline,
                    fleet_limit,
                    criterion=OptimizationCriterion.COMPLETION_TIME,
                )
                if allocated and allocated not in seen:
                    quick.add(allocated)
            for index, candidate in enumerate(
                sorted(
                    quick,
                    key=lambda solution: model.score(
                        solution, criterion=OptimizationCriterion.COMPLETION_TIME
                    ),
                )[: self.maximum_completion_candidates]
            ):
                if len(pool) >= self.maximum_candidates:
                    break
                seen.add(candidate)
                pool.append((f"completion-seed-{index}", candidate))
        return pool

    def duration_targets(
        self,
        model: CostModel[RoutingProblem],
        fleet_limit: int,
        objective: RoutingSearchObjective | None = None,
    ) -> tuple[float, ...]:
        objective = objective or RoutingSearchObjective(
            model.problem.criterion, model.problem.deadline_minutes
        )
        physical = max(plane.available_minutes for plane in model.problem.aircraft)
        targets = [physical]
        if objective.criterion is not OptimizationCriterion.TOTAL_FLIGHT_TIME:
            # Preserve the previous alternatives before expanding the pool.
            if physical > 60:
                targets.append(45.0)
            if objective.deadline_minutes is not None:
                targets.append(min(physical, objective.deadline_minutes))
            service = sum(
                min(
                    (
                        model.service(plane, 2 * task)[0]
                        for plane in range(len(model.problem.aircraft))
                        if model.service_allowed(plane, 2 * task)
                    ),
                    default=float("inf"),
                )
                for task in range(len(model.problem.tracks))
            )
            parallel_work = service / max(1, fleet_limit)
            # A small grid covers different sortie counts without guessing a
            # universal duration such as 45 min. Actual battery/site/quality
            # constraints are rechecked when missions are built downstream.
            if isfinite(parallel_work):
                targets.extend(min(physical, parallel_work * factor) for factor in (1.25, 1.6, 2.0))
            targets.append(physical / 2)
        return tuple(dict.fromkeys(round(value, 6) for value in targets if value > 0))
