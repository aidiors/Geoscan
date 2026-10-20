from dataclasses import replace

import pytest

from benchmarks.productive_cases import tiny_problems
from benchmarks.productive_model import CostModel
from benchmarks.productive_schedule_pool import duration_pool
from geoplan.domain.enums import OptimizationCriterion


def test_pool_keeps_different_splits_despite_worse_load_objective():
    problem = replace(tiny_problems()[0], criterion=OptimizationCriterion.COMPLETION_TIME)
    model = CostModel(problem)
    pool, report = duration_pool(model, [3, 4, 30, 30], 1, 1)
    assert report["catalog_complete"]
    assert len(pool) >= 2
    assert len({member.solution for member in pool}) == len(pool)
    assert len({len(member.solution) for member in pool}) >= 2
    assert len({model.score(member.solution) for member in pool}) >= 2
    assert all(model.validate_geometry(member.solution) for member in pool)
    assert [attempt["target_minutes"] for attempt in report["attempts"]] == [3, 4, 30]


def test_impossible_target_is_reported_without_substituting_baseline():
    model = CostModel(tiny_problems()[0])
    pool, report = duration_pool(model, [0.01], 1, 1)
    assert not pool
    assert report["attempts"] == [{"target_minutes": 0.01, "status": "no_complete_decode"}]


def test_catalog_assignment_survives_when_spreading_sorties_looks_better_by_load():
    problem = replace(tiny_problems()[0], criterion=OptimizationCriterion.COMPLETION_TIME)
    model = CostModel(problem)
    pool, _ = duration_pool(model, [4], 1, 1, ("load", "catalog"))
    by_allocation = {member.allocation: member.solution for member in pool}
    assert set(by_allocation) == {"load", "catalog"}
    load, catalog = (model.metrics(by_allocation[key]) for key in ("load", "catalog"))
    assert load is not None and catalog is not None
    assert load["load_makespan_minutes"] < catalog["load_makespan_minutes"]
    assert catalog["total_minutes"] <= load["total_minutes"]
    assert all(model.validate_geometry(member.solution) for member in pool)


@pytest.mark.parametrize("case", range(4))
def test_pool_preserves_wind_capacity_and_aircraft_eligibility(case):
    problem = tiny_problems()[case]
    model = CostModel(problem)
    pool, _ = duration_pool(model, [4, 8, 30], 1, 1)
    assert pool
    assert all(model.validate_geometry(member.solution) for member in pool)
    assert model.problem == problem
    assert all(plane.available_minutes == 30 for plane in model.problem.aircraft)
