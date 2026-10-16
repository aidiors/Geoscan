from benchmarks.coverage import run_coverage_benchmark
from scenarios.coverage import corridor_coverage_benchmark_cases


def main() -> None:
    results = run_coverage_benchmark(corridor_coverage_benchmark_cases())
    print(
        "case_id,algorithm,angle_degrees,route_distance_km,turn_count,"
        "segment_count,runtime_ms,gap_percent,geometry_valid,error_message"
    )
    for result in results:
        print(
            f"{result.case_id},{result.algorithm},{result.selected_angle_degrees},"
            f"{result.route_distance_km},{result.turn_count},{result.segment_count},"
            f"{result.runtime_ms},{result.gap_to_reference_percent},{result.geometry_valid},"
            f"{result.error_message or ''}"
        )


if __name__ == "__main__":
    main()
