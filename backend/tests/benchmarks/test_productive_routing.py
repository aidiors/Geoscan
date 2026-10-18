from __future__ import annotations

from dataclasses import replace
from itertools import permutations, product
from time import perf_counter

import pytest

from benchmarks.productive_cases import production_problems, scale_problems, tiny_problems
from benchmarks.productive_model import Aircraft, Cost, CostModel, Problem, Track, Trip, reverse
from benchmarks.productive_solvers import exact_small, split_order
from benchmarks.run_productive_routing import run_method
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.geo import Coordinate


def test_phase_accounting_has_analytic_distance_speed_answer() -> None:
    # One degree of equatorial longitude is 6371*pi/180 km.
    # Start at 1 km, acquire 1 km at 30 km/h, return 2 km at 60 km/h.
    from math import pi

    degree_per_km = 180 / (6371 * pi)
    a, b, home = (Coordinate(longitude=x * degree_per_km, latitude=0) for x in (1, 2, 0))
    trip = Trip(0, (0,))
    problem = Problem(
        "analytic", (Track("one", a, b, 30),), (Aircraft("uav", 60, 10, home),), (trip,), 5
    )
    model = CostModel(problem)
    cost = model.trip(trip)
    assert cost is not None
    assert (cost.survey, cost.departure, cost.connections, cost.returning) == pytest.approx(
        (2, 1, 0, 2)
    )
    metrics = model.metrics((trip,))
    assert metrics is not None
    assert metrics["useful_time_percent"] == pytest.approx(40)
    assert model.validate_geometry((trip,))
    # The audit must recompute duration, not trust the search cache.
    model._trips[trip] = Cost(survey=1, departure=1, returning=2, internal_distance=1)
    assert not model.validate_geometry((trip,))


def test_once_only_capacity_eligibility_and_bad_indices_are_rejected() -> None:
    for problem in tiny_problems():
        model = CostModel(problem)
        assert model.validate_geometry(problem.baseline)
        assert model.metrics(problem.baseline[:-1]) is None
        assert model.metrics((*problem.baseline, problem.baseline[0])) is None
        assert model.trip(Trip(-1, (0,))) is None
        assert model.trip(Trip(0, (-1,))) is None
        assert model.trip(Trip(0, (2 * len(problem.tracks),))) is None
    capacity = CostModel(tiny_problems()[1])
    assert capacity.trip(Trip(1, (0, 3))) is None
    eligible = CostModel(tiny_problems()[2])
    assert eligible.trip(Trip(1, (0,))) is None
    exhausted = CostModel(
        replace(
            tiny_problems()[0],
            aircraft=(replace(tiny_problems()[0].aircraft[0], available_minutes=0.1),),
        )
    )
    assert exhausted.trip(Trip(0, (0,))) is None


def test_blocked_service_is_rejected_instead_of_becoming_transit() -> None:
    problem = tiny_problems()[0]
    track = problem.tracks[0]
    mid_lon = (track.start.longitude + track.end.longitude) / 2
    lat = track.start.latitude
    zone = NoFlyZone(
        zone_id="blocked",
        coordinates=[
            Coordinate(longitude=mid_lon + dx, latitude=lat + dy)
            for dx, dy in ((-0.001, -0.001), (0.001, -0.001), (0.001, 0.001), (-0.001, 0.001))
        ],
    )
    model = CostModel(replace(problem, exclusions=(zone,)))
    assert model.trip(Trip(0, (0,))) is None
    assert not model.validate_geometry(problem.baseline)


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_exact_oracle_matches_independent_two_task_enumeration(criterion) -> None:
    base = tiny_problems()[3]
    problem = replace(
        base,
        tracks=base.tracks[:2],
        baseline=base.baseline[:2],
        criterion=criterion,
        deadline_minutes=8,
    )
    model = CostModel(problem)
    candidates = []
    for order in permutations((0, 1)):
        for flags in product((0, 1), repeat=2):
            visits = tuple(2 * t + f for t, f in zip(order, flags, strict=True))
            candidates.extend((Trip(plane, visits),) for plane in (0, 1))
            for planes in product((0, 1), repeat=2):
                candidates.append(tuple(Trip(p, (t,)) for p, t in zip(planes, visits, strict=True)))
    expected = min(candidates, key=model.score)
    solution, complete = exact_small(model, perf_counter() + 5)
    assert complete
    assert model.score(solution) == pytest.approx(model.score(expected))
    assert model.validate_geometry(solution)


@pytest.mark.parametrize("case_index", (0, 1, 3))
def test_split_matches_all_cuts_for_a_fixed_order(case_index: int) -> None:
    model = CostModel(tiny_problems()[case_index])
    order = (0, 3, 4, 7)
    candidates = []
    for cuts in product((False, True), repeat=len(order) - 1):
        boundaries = [0, *(i + 1 for i, cut in enumerate(cuts) if cut), len(order)]
        for planes in product((0, 1), repeat=len(boundaries) - 1):
            for flags in product((False, True), repeat=len(boundaries) - 1):
                trips = tuple(
                    Trip(plane, reverse(order[a:b]) if flag else order[a:b])
                    for plane, a, b, flag in zip(
                        planes,
                        boundaries[:-1],
                        boundaries[1:],
                        flags,
                        strict=True,
                    )
                )
                candidates.append(trips)
    expected = min(model.score(c)[0] for c in candidates)
    solution = split_order(model, order, perf_counter() + 5)
    assert model.score(solution)[0] == pytest.approx(expected)
    assert model.validate_geometry(solution)


@pytest.mark.parametrize(
    "method",
    (
        "endpoints",
        "regret2",
        "merge",
        "merge_singletons",
        "split",
        "split_reassign",
        "relocate",
        "alns",
    ),
)
def test_solvers_keep_exact_coverage_and_respect_small_oracle(method: str) -> None:
    problem = tiny_problems()[0]
    model = CostModel(problem)
    optimum, complete = exact_small(model, perf_counter() + 5)
    assert complete
    solution = run_method(model, method, perf_counter() + 0.2, 7)
    assert model.validate_geometry(solution)
    assert model.score(solution)[0] >= model.score(optimum)[0] - 1e-8


def test_expired_oracle_does_not_claim_optimality() -> None:
    model = CostModel(tiny_problems()[0])
    solution, complete = exact_small(model, perf_counter() - 1)
    assert not complete
    assert solution == model.problem.baseline


def test_production_baselines_preserve_all_original_tracks() -> None:
    cases = production_problems()
    assert len(cases) == 18
    for problem, metadata in cases:
        assert CostModel(problem).validate_geometry(problem.baseline)
        original = metadata["original_total_minutes"]
        assert isinstance(original, (float, int)) and original > 0


def test_path_geometry_is_shared_across_aircraft_but_not_directions(monkeypatch) -> None:
    import geoplan.routing.model as implementation

    original = implementation.PreparedSafeRoutes.build
    calls = []

    def counted(context, route):
        calls.append(route)
        return original(context, route)

    monkeypatch.setattr(implementation.PreparedSafeRoutes, "build", counted)
    problem = tiny_problems()[3]
    model = CostModel(problem)
    a, b = problem.tracks[0].start, problem.tracks[0].end
    forward = [model.leg(plane, a, b) for plane in range(len(problem.aircraft))]
    backward = [model.leg(plane, b, a) for plane in range(len(problem.aircraft))]
    assert len(calls) == 2
    assert forward[0][0] != forward[1][0]
    assert forward[1][0] != backward[1][0]


def test_shared_geometry_does_not_cache_an_aircraft_wind_failure() -> None:
    from math import isfinite

    problem = tiny_problems()[3]
    problem = replace(
        problem, aircraft=(replace(problem.aircraft[0], speed_kmh=10), problem.aircraft[1])
    )
    model = CostModel(problem)
    a, b = problem.tracks[0].start, problem.tracks[0].end
    assert not isfinite(model.leg(0, a, b)[0])
    assert isfinite(model.leg(1, a, b)[0])
    assert model.geometry_builds == 1


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_split_reassignment_keeps_a_baseline_and_a_complete_coverage(criterion) -> None:
    problem = replace(tiny_problems()[0], criterion=criterion, deadline_minutes=12)
    model = CostModel(problem)
    solution = run_method(model, "split_reassign", perf_counter() + 1, 0)
    assert model.validate_geometry(solution)
    assert model.score(solution) <= model.score(problem.baseline)
    timed_out = run_method(model, "split_reassign", perf_counter() - 1, 0)
    assert timed_out == problem.baseline


def test_large_generated_case_has_unchanged_quality_and_physical_endurance() -> None:
    cases = scale_problems(internal_distance_km=100, case_names=("scale_flat",))
    assert len(cases) == 3
    for problem, _ in cases:
        assert len(problem.aircraft) == 10
        assert len(problem.tracks) == 203
        assert problem.maximum_internal_distance_km == 100
        assert all(plane.available_minutes == 192 for plane in problem.aircraft)
        assert CostModel(problem).validate_geometry(problem.baseline)
