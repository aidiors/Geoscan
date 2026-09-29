from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import (
    geometry_stress_coverage_benchmark_cases,
    large_scattered_work_area_cases,
)
from src.curvature import DubinsSweepTransitionPlanner
from src.dubins import ExactDubinsPlanner
from src.main import create_default_dependencies
from src.schemas import SmallRadiusBenchmarkMethod, SmallRadiusBenchmarkRequest
from src.small_radius_benchmark import run_small_radius_benchmark

if __name__ == "__main__":
    report = run_small_radius_benchmark(
        SmallRadiusBenchmarkRequest(
            cases=[
                *geometry_stress_coverage_benchmark_cases(),
                *large_scattered_work_area_cases(),
            ],
            radii_m=[5, 10, 15],
        ),
        BoustrophedonCoveragePlanner(),
        {
            SmallRadiusBenchmarkMethod.DUBINS: DubinsSweepTransitionPlanner(
                planner=ExactDubinsPlanner()
            ),
            SmallRadiusBenchmarkMethod.HYBRID_ASTAR: (
                create_default_dependencies().sweep_transition_planner
            ),
        },
    )
    print(report.model_dump_json(indent=2))
