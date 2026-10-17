from __future__ import annotations

from dataclasses import asdict, replace
from itertools import permutations, product
from time import perf_counter

import pytest

from benchmarks.productive_adaptive import adaptive, representatives, route_catalog
from benchmarks.productive_cases import tiny_problems
from benchmarks.productive_incremental import PartialLoads, insertion_cost, insertion_fast
from benchmarks.productive_model import CostModel, Trip
from geoplan.domain.enums import OptimizationCriterion


@pytest.mark.parametrize("case_index", (0, 1, 2, 3))
def test_every_delta_insertion_matches_canonical_phases_and_constraints(case_index: int) -> None:
    problem = tiny_problems()[case_index]
    model = CostModel(problem)
    for plane in range(len(problem.aircraft)):
        for length in (1, 2):
            for order in permutations(range(3), length):
                for flags in product((0, 1), repeat=length):
                    visits = tuple(2 * t + f for t, f in zip(order, flags, strict=True))
                    trip = Trip(plane, visits)
                    previous = model.trip(trip)
                    if previous is None:
                        continue
                    for position in range(length + 1):
                        for flag in (0, 1):
                            token = 6 + flag
                            expected = model.trip(
                                Trip(plane, (*visits[:position], token, *visits[position:]))
                            )
                            actual = insertion_cost(model, trip, previous, position, token)
                            if expected is None:
                                assert actual is None
                            else:
                                assert actual is not None
                                assert asdict(actual) == pytest.approx(asdict(expected), abs=1e-8)


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
@pytest.mark.parametrize("deadline", (1, 12))
def test_delta_fleet_scores_include_turnaround_and_deadline(criterion, deadline: float) -> None:
    problem = replace(tiny_problems()[0], criterion=criterion, deadline_minutes=deadline)
    model = CostModel(problem)
    initial = (Trip(0, (0,)), Trip(0, (2,)))
    state = PartialLoads.build(model, initial)
    for plane in (0, 1):
        trip = Trip(plane, (4,))
        cost = model.trip(trip)
        assert cost is not None
        assert state.replacement_score(model, plane, cost, None) == pytest.approx(
            model.score((*initial, trip), complete=False),
            abs=1e-8,
        )
    trip = Trip(0, (2, 5))
    cost = model.trip(trip)
    assert cost is not None
    assert state.replacement_score(model, 0, cost, 1) == pytest.approx(
        model.score((initial[0], trip), complete=False),
        abs=1e-8,
    )


@pytest.mark.parametrize("case_index", (0, 3))
def test_orientation_catalog_matches_all_orientations_without_a_distance_cap(
    case_index: int,
) -> None:
    base = tiny_problems()[case_index]
    problem = replace(base, aircraft=base.aircraft[1:], maximum_internal_distance_km=None)
    model = CostModel(problem)
    catalog = route_catalog(model, (0, 1, 2, 3), perf_counter() + 5)
    for start, arcs in enumerate(catalog):
        assert len(arcs) == 4 - start
        for arc in arcs:
            candidates = [
                Trip(0, tuple(2 * t + f for t, f in zip(range(start, arc.end), flags, strict=True)))
                for flags in product((0, 1), repeat=arc.end - start)
            ]
            costs = [cost for t in candidates if (cost := model.trip(t)) is not None]
            expected = min(cost.total for cost in costs)
            assert arc.cost.total == pytest.approx(expected)
            canonical = model.trip(arc.trip)
            assert canonical is not None
            assert asdict(arc.cost) == pytest.approx(asdict(canonical), abs=1e-8)


def test_resource_dominance_preserves_different_bases_and_endurance() -> None:
    problem = tiny_problems()[0]
    assert representatives(CostModel(problem)) == (1,)
    problem = replace(
        problem, aircraft=(replace(problem.aircraft[0], available_minutes=100), problem.aircraft[1])
    )
    assert representatives(CostModel(problem)) == (0, 1)
    problem = replace(
        problem,
        aircraft=(replace(problem.aircraft[0], home=problem.tracks[0].start), problem.aircraft[1]),
    )
    assert representatives(CostModel(problem)) == (0, 1)


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_fast_repair_and_adaptive_are_complete_and_keep_canonical_objective(criterion) -> None:
    from benchmarks.productive_solvers import exact_small

    problem = replace(
        tiny_problems()[0],
        maximum_internal_distance_km=None,
        criterion=criterion,
        deadline_minutes=12,
    )
    model = CostModel(problem)
    solution = insertion_fast(model, set(range(4)), (), perf_counter() + 2)
    assert model.validate_geometry(solution)
    optimum, complete = exact_small(model, perf_counter() + 5)
    assert complete
    assert model.score(solution)[0] >= model.score(optimum)[0] - 1e-8
    candidate = adaptive(model, problem.baseline, perf_counter() + 2)
    assert model.validate_geometry(candidate)
    assert model.score(candidate) <= model.score(problem.baseline)
    assert model.score(candidate)[0] >= model.score(optimum)[0] - 1e-8
    assert adaptive(model, problem.baseline, perf_counter() - 1) == problem.baseline


def test_delta_insertion_preserves_safe_detours_on_departure_and_return() -> None:
    from geoplan.domain.airspace import NoFlyZone
    from geoplan.domain.geo import Coordinate

    base = tiny_problems()[0]
    home = base.aircraft[0].home
    center = (home.longitude + base.tracks[0].start.longitude) / 2
    zone = NoFlyZone(
        zone_id="departure-block",
        coordinates=[
            Coordinate(longitude=center + x, latitude=home.latitude + y)
            for x, y in ((-0.001, -0.0005), (0.001, -0.0005), (0.001, 0.0005), (-0.001, 0.0005))
        ],
    )
    model = CostModel(replace(base, exclusions=(zone,)))
    trip = Trip(1, (0, 3))
    old = model.trip(trip)
    assert old is not None
    for position in range(3):
        for token in (4, 5):
            actual = insertion_cost(model, trip, old, position, token)
            visits = (*trip.visits[:position], token, *trip.visits[position:])
            expected = model.trip(Trip(1, visits))
            assert actual is not None and expected is not None
            assert asdict(actual) == pytest.approx(asdict(expected), abs=1e-8)
            assert model.validate_geometry((Trip(1, visits), Trip(0, (6,))))


@pytest.mark.parametrize("case_index", (0, 1, 2, 3))
def test_dataset_roundtrip_preserves_frozen_problem_and_costs(tmp_path, case_index: int) -> None:
    import json

    from benchmarks.productive_dataset import load_cases
    from benchmarks.run_productive_routing import _serialize

    problem = replace(tiny_problems()[case_index], maximum_internal_distance_km=None)
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps({"cases": [{"problem": asdict(problem), "production": {}}]}, default=_serialize),
        encoding="utf-8",
    )
    loaded, _ = load_cases(path)[0]
    assert loaded == problem
    assert CostModel(loaded).metrics(loaded.baseline) == CostModel(problem).metrics(
        problem.baseline
    )
