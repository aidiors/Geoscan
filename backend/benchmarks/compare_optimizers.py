from statistics import median

from src.algorithm_benchmark import run_optimization_benchmark
from src.schemas import BenchmarkDrone, BenchmarkTask, OptimizationBenchmarkRequest

REPETITIONS = 7


def benchmark_request() -> OptimizationBenchmarkRequest:
    return OptimizationBenchmarkRequest(
        drones=[
            BenchmarkDrone(drone_id="geoscan-201", cruise_speed_kmh=70),
            BenchmarkDrone(drone_id="geoscan-801", cruise_speed_kmh=55),
            BenchmarkDrone(drone_id="geoscan-gemini", cruise_speed_kmh=43),
        ],
        tasks=[
            BenchmarkTask(
                task_id=f"sector-{index + 1}",
                distance_km=distance,
                transit_minutes_by_drone={
                    "geoscan-201": transit[0],
                    "geoscan-801": transit[1],
                    "geoscan-gemini": transit[2],
                },
            )
            for index, (distance, transit) in enumerate(
                [
                    (18, (5, 8, 3)),
                    (15, (7, 4, 9)),
                    (13, (3, 7, 6)),
                    (11, (8, 3, 5)),
                    (9, (2, 6, 8)),
                    (8, (6, 2, 4)),
                    (7, (4, 5, 2)),
                    (6, (3, 7, 5)),
                    (5, (5, 3, 6)),
                ]
            )
        ],
        random_seed=42,
    )


def main() -> None:
    request = benchmark_request()
    run_optimization_benchmark(request)
    batches = [run_optimization_benchmark(request) for _ in range(REPETITIONS)]
    print("algorithm,completion_minutes,total_flight_minutes,runtime_ms")
    for index, result in enumerate(batches[0]):
        runtime_ms = median(batch[index].runtime_ms for batch in batches)
        print(
            f"{result.algorithm},{result.completion_minutes},"
            f"{result.total_flight_minutes},{runtime_ms}"
        )


if __name__ == "__main__":
    main()
