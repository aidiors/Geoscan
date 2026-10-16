from collections.abc import Mapping
from time import perf_counter

from benchmarks.models import (
    SmallRadiusBenchmarkCaseResult,
    SmallRadiusBenchmarkMethod,
    SmallRadiusBenchmarkReport,
    SmallRadiusBenchmarkRequest,
)
from geoplan.coverage.sweep import CoveragePlanner
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.planning.models import TurnRadiusAnalysisRequest
from geoplan.planning.turn_radius import analyze_turn_radii


def run_small_radius_benchmark(
    request: SmallRadiusBenchmarkRequest,
    coverage_planner: CoveragePlanner,
    transition_planners: Mapping[SmallRadiusBenchmarkMethod, SweepTransitionPlanner],
) -> SmallRadiusBenchmarkReport:
    results: list[SmallRadiusBenchmarkCaseResult] = []
    for method, transition_planner in transition_planners.items():
        for case in request.cases:
            analysis_request = TurnRadiusAnalysisRequest(
                coverage=case.request,
                radii_m=request.radii_m,
            )
            started = perf_counter()
            analysis = analyze_turn_radii(analysis_request, coverage_planner, transition_planner)
            results.append(
                SmallRadiusBenchmarkCaseResult(
                    case_id=case.case_id,
                    method=method,
                    scenarios=analysis.scenarios,
                    runtime_ms=round((perf_counter() - started) * 1_000, 3),
                )
            )
    return SmallRadiusBenchmarkReport(
        case_ids=[case.case_id for case in request.cases],
        radii_m=request.radii_m,
        methods=list(transition_planners),
        results=results,
    )
