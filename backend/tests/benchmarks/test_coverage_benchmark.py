from benchmarks.coverage import run_coverage_benchmark
from geoplan.coverage.decomposition import automatic_partition_requests
from geoplan.domain.enums import SweepAngleAlgorithm
from scenarios.coverage import corridor_coverage_benchmark_cases, default_coverage_benchmark_cases


def test_coverage_benchmark_compares_nineteen_coverage_algorithms() -> None:
    case = next(item for item in default_coverage_benchmark_cases() if item.case_id == "rotated_35")

    results = run_coverage_benchmark([case])

    assert len(results) == 19
    assert all(result.geometry_valid for result in results)
    automatic = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.COARSE_TO_FINE
    )
    fixed = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.FIXED_INPUT
    )
    assert automatic.route_distance_km is not None
    assert fixed.route_distance_km is not None
    assert automatic.route_distance_km < fixed.route_distance_km * 0.9
    assert automatic.gap_to_reference_percent is not None
    assert automatic.gap_to_reference_percent < 1
    section_dp = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.ADAPTIVE_SECTION_DP
    )
    assert section_dp.geometry_valid
    assert section_dp.route_distance_km is not None
    dubins = next(
        result
        for result in results
        if result.algorithm is SweepAngleAlgorithm.ADAPTIVE_SECTION_DUBINS
    )
    assert dubins.geometry_valid
    assert dubins.route_distance_km is not None
    full_dubins = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.ADAPTIVE_FULL_DUBINS
    )
    assert full_dubins.geometry_valid
    assert full_dubins.route_distance_km is not None
    hybrid_astar = next(
        result
        for result in results
        if result.algorithm is SweepAngleAlgorithm.ADAPTIVE_HYBRID_ASTAR
    )
    assert hybrid_astar.geometry_valid
    assert hybrid_astar.route_distance_km is not None
    turn_aware = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.ADAPTIVE_TURN_AWARE
    )
    assert turn_aware.geometry_valid
    assert turn_aware.route_distance_km is not None
    production = next(
        result for result in results if result.algorithm is SweepAngleAlgorithm.PRODUCTION_ADAPTIVE
    )
    assert production.geometry_valid
    assert production.route_distance_km is not None
    lower_bounds = [
        result
        for result in results
        if result.algorithm
        in {
            SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE,
            SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
        }
    ]
    assert all(result.gap_to_reference_percent is None for result in lower_bounds)


def test_coverage_benchmark_schema_reports_reference_gap() -> None:
    case = default_coverage_benchmark_cases()[0]

    results = run_coverage_benchmark([case])

    gaps = [
        result.gap_to_reference_percent
        for result in results
        if result.gap_to_reference_percent is not None
    ]
    assert min(gaps) == 0
    assert all(result.runtime_ms >= 0 for result in results)


def test_corridor_benchmark_contains_straight_and_changing_angle_cases() -> None:
    cases = corridor_coverage_benchmark_cases()

    assert [case.case_id for case in cases] == [
        "long_corridor_12",
        "long_corridor_43",
        "long_corridor_78",
        "gradual_bend_corridor",
        "zigzag_corridor",
    ]
    gradual = next(case for case in cases if case.case_id == "gradual_bend_corridor")
    zigzag = next(case for case in cases if case.case_id == "zigzag_corridor")
    assert len(gradual.partitioned_requests) == 4
    assert len(zigzag.partitioned_requests) == 4
    assert len(automatic_partition_requests(cases[0].request)) == 1
    assert len(automatic_partition_requests(gradual.request)) == 4
    assert len(automatic_partition_requests(zigzag.request)) >= 4
