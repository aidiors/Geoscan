import pytest
from pydantic import ValidationError

from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import geometry_stress_coverage_benchmark_cases
from src.curvature import DubinsSweepTransitionPlanner
from src.dubins import ExactDubinsPlanner
from src.schemas import (
    SmallRadiusBenchmarkMethod,
    SmallRadiusBenchmarkReport,
    SmallRadiusBenchmarkRequest,
    TurnRadiusStatus,
)
from src.small_radius_benchmark import run_small_radius_benchmark


def test_small_radius_benchmark_reports_feasible_round_and_sawtooth_cases() -> None:
    request = SmallRadiusBenchmarkRequest(
        cases=geometry_stress_coverage_benchmark_cases()[:2],
        radii_m=[5, 10, 15],
    )

    report = run_small_radius_benchmark(
        request,
        BoustrophedonCoveragePlanner(),
        {SmallRadiusBenchmarkMethod.DUBINS: DubinsSweepTransitionPlanner(ExactDubinsPlanner())},
    )

    assert report.case_ids == ["round_32", "sawtooth_98"]
    assert len(report.results) == 2
    assert all(
        scenario.status is TurnRadiusStatus.FEASIBLE
        for result in report.results
        for scenario in result.scenarios
    )
    assert all(result.runtime_ms >= 0 for result in report.results)


def test_small_radius_benchmark_request_rejects_duplicate_or_unordered_inputs() -> None:
    case = geometry_stress_coverage_benchmark_cases()[0]

    with pytest.raises(ValidationError, match="возрастать"):
        SmallRadiusBenchmarkRequest(cases=[case], radii_m=[10, 5])
    with pytest.raises(ValidationError, match="уникальны"):
        SmallRadiusBenchmarkRequest(cases=[case, case], radii_m=[5, 10])


def test_small_radius_benchmark_report_rejects_missing_method_case_pair() -> None:
    request = SmallRadiusBenchmarkRequest(
        cases=geometry_stress_coverage_benchmark_cases()[:1],
        radii_m=[5, 10],
    )
    report = run_small_radius_benchmark(
        request,
        BoustrophedonCoveragePlanner(),
        {SmallRadiusBenchmarkMethod.DUBINS: DubinsSweepTransitionPlanner(ExactDubinsPlanner())},
    )
    invalid = report.model_dump()
    invalid["methods"] = ["dubins", "hybrid_astar"]

    with pytest.raises(ValidationError, match="каждую пару"):
        SmallRadiusBenchmarkReport.model_validate(invalid)
