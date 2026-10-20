from dataclasses import replace
from math import cos, radians, sin
from time import perf_counter

import pytest

from benchmarks.productive_cases import tiny_problems
from benchmarks.productive_cells import (
    cell_orders,
    cells_adaptive,
    cells_sorties,
    destroy_sortie,
    sortie_search,
    track_cells,
)
from benchmarks.productive_model import CostModel, Track, Trip
from geoplan.domain.enums import OptimizationCriterion
from geoplan.navigation.geometry import LocalProjection


def split_problem():
    base = tiny_problems()[0]
    projection = LocalProjection(37, 55)
    tracks = tuple(
        Track(str(i), projection.unproject(a, y), projection.unproject(b, y), 45)
        for i, (a, b, y) in enumerate(
            (
                (0, 1000, 0),
                (0, 400, 100),
                (600, 1000, 100),
                (0, 400, 200),
                (600, 1000, 200),
                (0, 1000, 300),
            )
        )
    )
    return replace(base, tracks=tracks, baseline=tuple(Trip(1, (2 * i,)) for i in range(6)))


def test_cells_end_at_splits_and_merges_preserving_all_service_tracks():
    problem = split_problem()
    model = CostModel(problem)
    assert track_cells(model) == ((0,), (1, 3), (2, 4), (5,))
    orders = cell_orders(model)
    assert any(o != tuple(range(6)) for o in orders)
    assert all(sorted(o) == list(range(6)) for o in orders)
    for order in orders[1:]:
        assert abs(order.index(1) - order.index(3)) == 1
        assert abs(order.index(2) - order.index(4)) == 1


def test_destroy_removes_whole_sortie_and_no_other_task():
    problem = tiny_problems()[0]
    initial = (Trip(0, (0, 3, 4)), Trip(1, (7,)))
    removed, partial = destroy_sortie(CostModel(problem), initial, 0)
    assert removed == {0, 1, 2}
    assert partial == (initial[1],)


def test_cell_partition_is_preserved_by_rotation_and_input_permutation():
    original = split_problem()
    expected = {
        frozenset(original.tracks[t].track_id for t in cell)
        for cell in track_cells(CostModel(original))
    }
    projection = LocalProjection(37, 55)
    angle = radians(35)

    def rotate(point):
        x, y = projection.project(point)
        return projection.unproject(
            x * cos(angle) - y * sin(angle), x * sin(angle) + y * cos(angle)
        )

    rotated = tuple(replace(t, start=rotate(t.start), end=rotate(t.end)) for t in original.tracks)
    problem = replace(original, tracks=tuple(rotated[i] for i in (3, 1, 5, 0, 4, 2)))
    actual = {
        frozenset(problem.tracks[t].track_id for t in cell)
        for cell in track_cells(CostModel(problem))
    }
    assert actual == expected


def test_nonparallel_tracks_fall_back_without_losing_service_tasks():
    problem = split_problem()
    track = problem.tracks[-1]
    track = replace(
        track, end=track.end.model_copy(update={"latitude": track.end.latitude + 0.001})
    )
    problem = replace(problem, tracks=(*problem.tracks[:-1], track))
    assert track_cells(CostModel(problem)) == tuple((i,) for i in range(len(problem.tracks)))


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
@pytest.mark.parametrize("case", (0, 1, 2, 3))
def test_group_neighborhoods_preserve_constraints_and_incumbent(case, criterion):
    problem = replace(tiny_problems()[case], criterion=criterion, deadline_minutes=12)
    for method in (
        cells_adaptive,
        lambda m, s, d: sortie_search(m, s, d, 4),
        lambda m, s, d: cells_sorties(m, s, d, 4),
    ):
        model = CostModel(problem)
        candidate = method(model, problem.baseline, perf_counter() + 0.08)
        assert model.validate_geometry(candidate)
        assert model.score(candidate) <= model.score(problem.baseline)
        assert method(model, problem.baseline, perf_counter() - 1) == problem.baseline
