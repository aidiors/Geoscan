from benchmarks.models import SmallRadiusBenchmarkMethod, SmallRadiusBenchmarkRequest
from benchmarks.small_radius import run_small_radius_benchmark
from geoplan.composition import create_default_dependencies
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.navigation.curvature import DubinsSweepTransitionPlanner
from geoplan.navigation.dubins import ExactDubinsPlanner
from scenarios.coverage import (
    geometry_stress_coverage_benchmark_cases,
    large_scattered_work_area_cases,
)

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
