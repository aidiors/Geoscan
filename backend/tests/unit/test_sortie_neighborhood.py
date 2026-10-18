from dataclasses import replace
from time import perf_counter

import pytest

from geoplan.domain.enums import OptimizationCriterion
from geoplan.navigation.geometry import LocalProjection
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem, Track, Trip
from geoplan.routing.neighborhood import SortieNeighborhoodSearch


def interleaved_problem():
    projection = LocalProjection(37, 55)
    tracks = tuple(
        Track(str(i), projection.unproject(x, 0), projection.unproject(x, 1000), 80)
        for i, x in enumerate((0, 100, 10000, 10100))
    )
    return CostModel(
        RoutingProblem(
            tracks,
            (
                Aircraft("a", 80, 20, projection.unproject(5000, 0)),
                Aircraft("b", 75, 20, projection.unproject(5000, 0)),
            ),
            None,
            None,
            (),
            OptimizationCriterion.TOTAL_FLIGHT_TIME,
            None,
        )
    )


@pytest.mark.parametrize("adaptive", [False, True])
def test_destroy_repair_removes_cross_cluster_deadhead_without_losing_tracks(adaptive):
    model = interleaved_problem()
    seed = (Trip(0, (0, 4)), Trip(1, (2, 6)))
    assert model.metrics(seed) is not None
    alternatives = SortieNeighborhoodSearch(adaptive=adaptive).improve(
        model, seed, (20,), 2, perf_counter() + 2
    )
    assert alternatives
    best = min(alternatives, key=model.score)
    assert model.metrics(best)["total_minutes"] < 0.7 * model.metrics(seed)["total_minutes"]
    assert all(model.validate_geometry(candidate) for candidate in alternatives)
    assert model.sortie_destroy_attempts > 0 and model.sortie_repair_completed > 0


def test_expired_budget_does_not_return_partial_repairs():
    model = interleaved_problem()
    seed = (Trip(0, (0, 4)), Trip(1, (2, 6)))
    assert SortieNeighborhoodSearch().improve(model, seed, (20,), 1, perf_counter() - 1) == []


@pytest.mark.parametrize("adaptive", [False, True])
def test_repair_respects_single_aircraft_limit(adaptive):
    model = interleaved_problem()
    seed = (Trip(0, (0, 4)), Trip(0, (2, 6)))
    alternatives = SortieNeighborhoodSearch(adaptive=adaptive).improve(
        model, seed, (20,), 1, perf_counter() + 2
    )
    assert alternatives
    assert all(len({trip.aircraft for trip in candidate}) == 1 for candidate in alternatives)


def test_adaptive_geometric_repair_does_not_depend_on_track_ids_and_is_reproducible():
    source = interleaved_problem().problem
    problem = replace(source, tracks=tuple(source.tracks[i] for i in (0, 2, 1, 3)))
    seed = (Trip(0, (0, 2)), Trip(1, (4, 6)))
    search = SortieNeighborhoodSearch(adaptive=True, maximum_iterations=12, random_seed=19)
    models = [CostModel(problem), CostModel(problem)]
    results = [search.improve(model, seed, (20,), 2, perf_counter() + 3) for model in models]
    assert results[0] and results[0] == results[1]
    before = models[0].metrics(seed)
    after = models[0].metrics(results[0][0])
    assert before is not None and after is not None
    assert after["total_minutes"] < 0.7 * before["total_minutes"]
    assert all(models[0].validate_geometry(solution) for solution in results[0])


def test_all_adaptive_operators_preserve_payload_eligibility():
    source = interleaved_problem().problem
    aircraft = tuple(
        replace(plane, eligible_tracks=frozenset(tracks))
        for plane, tracks in zip(source.aircraft, ((0, 1), (2, 3)), strict=True)
    )
    model = CostModel(replace(source, aircraft=aircraft))
    seed = (Trip(0, (0, 2)), Trip(1, (4, 6)))
    results = SortieNeighborhoodSearch(adaptive=True).improve(
        model, seed, (20,), 2, perf_counter() + 3
    )
    assert set(model.neighborhood_operator_attempts) == {"deadhead", "related", "critical_load"}
    assert model.sortie_repair_completed > 0
    for solution in results:
        assert model.validate_geometry(solution)
        assert all(
            model.service_allowed(trip.aircraft, token)
            for trip in solution
            for token in trip.visits
        )
