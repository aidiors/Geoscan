from benchmarks.assignment import run_optimization_benchmark
from benchmarks.models import BenchmarkDrone, BenchmarkTask, OptimizationBenchmarkRequest


def test_benchmark_compares_ten_deterministic_assignment_algorithms() -> None:
    request = OptimizationBenchmarkRequest(
        drones=[
            BenchmarkDrone(drone_id="fast", cruise_speed_kmh=60),
            BenchmarkDrone(drone_id="slow", cruise_speed_kmh=40),
        ],
        tasks=[
            BenchmarkTask(task_id="a", distance_km=12),
            BenchmarkTask(task_id="b", distance_km=9),
            BenchmarkTask(task_id="c", distance_km=6),
            BenchmarkTask(task_id="d", distance_km=3),
        ],
    )

    results = run_optimization_benchmark(request)

    assert len(results) == 10
    exact = next(result for result in results if result.algorithm == "exact_branch_and_bound")
    assert exact.completion_minutes == min(result.completion_minutes for result in results)
