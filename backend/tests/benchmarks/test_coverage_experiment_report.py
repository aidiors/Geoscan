from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from benchmarks.models import CoverageBenchmarkResult, CoverageExperimentReport
from geoplan.domain.enums import SweepAngleAlgorithm


def benchmark_result(
    case_id: str,
    algorithm: SweepAngleAlgorithm,
) -> CoverageBenchmarkResult:
    return CoverageBenchmarkResult(
        case_id=case_id,
        algorithm=algorithm,
        selected_angle_degrees=90,
        route_distance_km=10,
        turn_count=4,
        segment_count=1,
        runtime_ms=1,
        gap_to_reference_percent=0,
        geometry_valid=True,
    )


def test_experiment_report_requires_every_scenario_algorithm_pair() -> None:
    algorithms = [SweepAngleAlgorithm.FIXED_INPUT, SweepAngleAlgorithm.COARSE_TO_FINE]

    report = CoverageExperimentReport(
        experiment_id="coverage-test",
        generated_at_utc=datetime.now(UTC),
        scenario_ids=["first", "second"],
        algorithms=algorithms,
        results=[
            benchmark_result(case_id, algorithm)
            for case_id in ["first", "second"]
            for algorithm in algorithms
        ],
    )

    assert len(report.results) == 4


def test_experiment_report_rejects_incomplete_matrix() -> None:
    with pytest.raises(ValidationError, match="every scenario"):
        CoverageExperimentReport(
            experiment_id="coverage-test",
            generated_at_utc=datetime.now(UTC),
            scenario_ids=["first", "second"],
            algorithms=[SweepAngleAlgorithm.FIXED_INPUT],
            results=[benchmark_result("first", SweepAngleAlgorithm.FIXED_INPUT)],
        )
