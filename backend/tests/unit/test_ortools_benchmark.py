"""Optional benchmark dependency: uv run --group benchmark pytest this file."""

from itertools import product

import pytest

pytest.importorskip("ortools")

from benchmarks.compare_ortools import synthetic_request
from benchmarks.ortools_assignment import CpSatAssignmentBenchmark, evaluate_assignment
from geoplan.assignment.solver import HybridAssignmentOptimizer
from geoplan.domain.enums import OptimizationCriterion


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
@pytest.mark.parametrize("deadline", [1.0, 40.0, 1000.0])
def test_cp_sat_matches_exhaustive_assignment(criterion, deadline) -> None:
    request = synthetic_request(31, tasks=5, drones=3).model_copy(
        update={
            "criterion": criterion,
            "maximum_active_drones": 2,
            "completion_deadline_minutes": deadline,
        }
    )
    evaluations = []
    for choices in product(*(range(len(group)) for group in request.candidate_groups)):
        try:
            evaluations.append(evaluate_assignment(request, choices))
        except ValueError:
            continue
    expected = min(evaluations, key=lambda e: e.score)
    result = CpSatAssignmentBenchmark(time_limit_seconds=5).solve(request)
    assert result.evaluation is not None, result.statuses
    assert result.proven_lexicographic, result.statuses
    assert result.evaluation.score == pytest.approx(expected.score, abs=1e-5)
    baseline = HybridAssignmentOptimizer().optimize(request)
    choices = tuple(
        group.index(candidate)
        for group, candidate in zip(request.candidate_groups, baseline.assignments, strict=True)
    )
    assert evaluate_assignment(request, choices).score == pytest.approx(expected.score, abs=1e-5)


def test_infeasible_fleet_is_not_reported_as_timeout() -> None:
    request = synthetic_request(1, tasks=2, drones=2)
    groups = [[request.candidate_groups[0][0]], [request.candidate_groups[1][-1]]]
    assert groups[0][0].drone_id != groups[1][0].drone_id
    request = request.model_copy(update={"candidate_groups": groups, "maximum_active_drones": 1})
    result = CpSatAssignmentBenchmark().solve(request)
    assert result.evaluation is None
    assert result.statuses == ("INFEASIBLE",)
    assert not result.proven_lexicographic


def test_small_budget_returns_unknown_without_inventing_a_solution() -> None:
    result = CpSatAssignmentBenchmark(time_limit_seconds=1e-9).solve(
        synthetic_request(1, tasks=20, drones=8)
    )
    assert result.evaluation is None
    assert not result.proven_lexicographic


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_budget_is_rejected(seconds) -> None:
    with pytest.raises(ValueError):
        CpSatAssignmentBenchmark(time_limit_seconds=seconds).solve(
            synthetic_request(1, tasks=2, drones=2)
        )


def test_turnaround_is_between_sorties_and_not_part_of_flight() -> None:
    request = synthetic_request(1, tasks=2, drones=1).model_copy(
        update={
            "turnaround_minutes_by_drone": {"drone-0": 7.0},
        }
    )
    result = CpSatAssignmentBenchmark().solve(request)
    assert result.evaluation is not None
    assert result.evaluation.completion_minutes == pytest.approx(
        result.evaluation.total_minutes + 7
    )


def test_evaluation_rejects_partial_assignments() -> None:
    with pytest.raises(ValueError):
        evaluate_assignment(synthetic_request(1, tasks=2, drones=2), (0,))


def test_cp_sat_accepts_a_valid_incumbent_hint_without_changing_objective():
    request = synthetic_request(9, tasks=5, drones=3)
    baseline = HybridAssignmentOptimizer().optimize(request)
    choices = tuple(
        group.index(c)
        for group, c in zip(request.candidate_groups, baseline.assignments, strict=True)
    )
    result = CpSatAssignmentBenchmark(time_limit_seconds=5).solve(request, choices)
    assert result.hint_supplied and result.proven_lexicographic
    assert result.evaluation is not None
    assert result.evaluation.score == pytest.approx(evaluate_assignment(request, choices).score)


def test_cp_sat_rejects_incomplete_incumbent_hint():
    with pytest.raises(ValueError):
        CpSatAssignmentBenchmark().solve(synthetic_request(9, 5, 3), (0,))
