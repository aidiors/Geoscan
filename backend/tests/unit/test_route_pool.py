from dataclasses import replace
from unittest.mock import patch

import pytest

from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.fleet import DroneResourceLimit
from geoplan.navigation.geometry import LocalProjection
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem, Track
from geoplan.routing.pool import ProductiveRoutePool
from geoplan.routing.split import route_catalog


def test_route_assignment_and_partial_scoring_preserve_finite_battery_inventory():
    from geoplan.routing.allocation import assign_trips
    from geoplan.routing.model import Trip

    original = model()
    aircraft = tuple(
        replace(a, resource_limit=DroneResourceLimit(maximum_sorties=1))
        for a in original.problem.aircraft
    )
    costs = CostModel(replace(original.problem, aircraft=aircraft))
    two = (Trip(0, (0, 2)), Trip(0, (4, 6)))
    assert costs.metrics(two) is None
    assert costs.score(two, complete=False) == (float("inf"),)
    assigned = assign_trips(costs, two, float("inf"))
    assert len(assigned) == 2
    assert {t.aircraft for t in assigned} == {0, 1}
    assert costs.metrics(assigned) is not None


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
@pytest.mark.parametrize("fleet_limit", [1, 2])
def test_incremental_assignment_matches_full_physical_prefix_scores(criterion, fleet_limit):
    from geoplan.routing.allocation import assign_trips
    from geoplan.routing.model import RoutingSearchObjective, Trip, variants

    costs = CostModel(replace(model().problem, criterion=criterion, deadline_minutes=25))
    source = (Trip(0, (0, 2)), Trip(0, (4,)), Trip(0, (6,)))
    guidance = RoutingSearchObjective(criterion, 25)
    result = assign_trips(costs, source, float("inf"), fleet_limit, search_objective=guidance)
    assert costs.validate_geometry(result)
    prefix = ()
    for chosen in result:
        group = next(
            t for t in source if {v // 2 for v in t.visits} == {v // 2 for v in chosen.visits}
        )
        active = {t.aircraft for t in prefix}
        feasible = [
            Trip(plane, visits)
            for plane in range(len(costs.problem.aircraft))
            for visits in variants(group.visits)
            if (len(active) < fleet_limit or plane in active)
            and costs.trip(Trip(plane, visits)) is not None
        ]
        prefix = (*prefix, chosen)
        actual = costs.score(prefix, complete=False, search_objective=guidance)
        candidates = [
            costs.score((*prefix[:-1], t), complete=False, search_objective=guidance)
            for t in feasible
        ]
        assert actual == min(candidates)


def model(criterion=OptimizationCriterion.COMPLETION_TIME):
    projection = LocalProjection(37, 55)
    return CostModel(
        RoutingProblem(
            tuple(
                Track(
                    str(i),
                    projection.unproject(0, i * 100),
                    projection.unproject(10000, i * 100),
                    100,
                )
                for i in range(4)
            ),
            (
                Aircraft("fast", 80, 60, projection.unproject(-1000, 0)),
                Aircraft("slower", 70, 50, projection.unproject(-1000, 0)),
            ),
            None,
            None,
            (),
            criterion,
            None,
        )
    )


def test_parallel_split_is_generated_even_when_everything_fits_one_sortie():
    costs = model()
    pool = ProductiveRoutePool().generate(costs).all
    assert pool
    assert any(len(trips) == 1 for _, trips in pool)
    best = min((trips for _, trips in pool), key=costs.score)
    assert len({trip.aircraft for trip in best}) == 2
    assert costs.metrics(best)["load_makespan_minutes"] < 20
    assert all(costs.validate_geometry(trips) for _, trips in pool)


def test_generated_routes_respect_active_fleet_limit():
    costs = model()
    pool = ProductiveRoutePool().generate(costs, maximum_active_drones=1).all
    assert pool
    assert all(len({trip.aircraft for trip in trips}) == 1 for _, trips in pool)


def test_flight_objective_keeps_economical_single_sortie():
    costs = model(OptimizationCriterion.TOTAL_FLIGHT_TIME)
    pool = ProductiveRoutePool().generate(costs).all
    best = min((trips for _, trips in pool), key=costs.score)
    assert len(best) == 1
    assert costs.metrics(best)["total_minutes"] < 32


def test_pool_is_bounded_and_deduplicated():
    pool = ProductiveRoutePool(maximum_candidates=2).generate(model()).all
    assert 0 < len(pool) <= 2
    assert len({trips for _, trips in pool}) == len(pool)


def test_adaptive_extension_keeps_all_legacy_candidates():
    legacy = ProductiveRoutePool(adaptive_neighborhood_seconds=0).generate(model()).all
    extended = ProductiveRoutePool().generate(model()).all
    assert set(legacy).issubset(set(extended))
    assert len(extended) <= ProductiveRoutePool().maximum_candidates


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_cross_objective_extension_preserves_primary_candidates_and_user_constraints(criterion):
    problem = replace(model().problem, criterion=criterion, deadline_minutes=25)
    primary = ProductiveRoutePool(cross_objective_seconds=0).generate(CostModel(problem), 1).all
    costs = CostModel(problem)
    extended = ProductiveRoutePool().generate(costs, 1).all
    assert set(primary).issubset(set(extended))
    assert all(costs.validate_geometry(solution) for _, solution in extended)
    assert all(len({trip.aircraft for trip in solution}) == 1 for _, solution in extended)
    assert costs.problem is problem and problem.criterion is criterion
    assert problem.deadline_minutes == 25


def test_complete_physical_catalogs_are_reused_between_search_objectives():
    with patch("geoplan.routing.pool.route_catalog", wraps=route_catalog) as build:
        ProductiveRoutePool(cross_objective_seconds=0).generate(model())
        primary_calls = build.call_count
    with patch("geoplan.routing.pool.route_catalog", wraps=route_catalog) as build:
        ProductiveRoutePool().generate(model())
        assert build.call_count == primary_calls > 0


def test_internal_deadline_guidance_does_not_change_problem_without_a_deadline():
    costs = model()
    pool = ProductiveRoutePool().generate(costs).all
    assert pool
    assert costs.problem.deadline_minutes is None
    assert costs.problem.criterion is OptimizationCriterion.COMPLETION_TIME


def test_cross_candidates_share_the_total_candidate_limit():
    pool = (
        ProductiveRoutePool(maximum_primary_candidates=1, maximum_candidates=2)
        .generate(model())
        .all
    )
    assert 0 < len(pool) <= 2
    assert len({solution for _, solution in pool}) == len(pool)
