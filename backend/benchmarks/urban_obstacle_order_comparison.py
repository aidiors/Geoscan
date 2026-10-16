from __future__ import annotations

import json
from unittest.mock import patch

from benchmarks.models import UrbanObstacleOrderBenchmark, UrbanObstacleOrderMeasurement
from geoplan.composition import create_default_dependencies
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.navigation.curvature import HybridAStarSweepTransitionPlanner
from geoplan.navigation.models import TurnAwareSweepRequest, TurnAwareSweepResult
from geoplan.navigation.routing import RouteGeometryError
from geoplan.navigation.turn_aware import build_turn_aware_sweep_route
from scenarios.coverage import urban_obstacle_coverage_case

RADII_M = [15.0, 27.5, 40.0]


def run_experiment() -> UrbanObstacleOrderBenchmark:
    case = urban_obstacle_coverage_case()
    parameters = case.request.parameters.model_copy(update={"maximum_segment_distance_km": 500})
    coverage_request = case.request.model_copy(update={"parameters": parameters})
    coverage = BoustrophedonCoveragePlanner().plan(coverage_request)
    if len(coverage.segments) != 1 or len(coverage.segments[0].route) % 2:
        raise RuntimeError("Benchmark requires one segment with complete survey legs.")

    source_route = coverage.segments[0].route
    source_legs = [source_route[index : index + 2] for index in range(0, len(source_route), 2)]
    turn_request = TurnAwareSweepRequest(
        route=source_route,
        coverage_area=coverage_request.area,
        no_fly_zones=coverage_request.exclusions,
        minimum_turn_radius_m=RADII_M[0],
        strip_width_m=min(coverage.footprint_width_m, parameters.strip_width_m),
        footprint_height_m=coverage.footprint_height_m,
    )
    dependencies = create_default_dependencies()
    transition_planner = dependencies.sweep_transition_planner
    if not isinstance(transition_planner, HybridAStarSweepTransitionPlanner):
        raise RuntimeError("The default benchmark transition planner changed unexpectedly.")

    results: list[UrbanObstacleOrderMeasurement] = []
    for radius_m in RADII_M:
        measured_request = turn_request.model_copy(update={"minimum_turn_radius_m": radius_m})
        for order in ("original", "reordered_blocks"):
            try:
                if order == "original":
                    curved = _evaluate_original_order(measured_request, transition_planner)
                else:
                    curved = _evaluate_reordered_blocks(measured_request, transition_planner)
            except RouteGeometryError as error:
                results.append(
                    UrbanObstacleOrderMeasurement(
                        order=order,
                        radius_m=radius_m,
                        status="NO_ROUTE_FOR_SELECTED_SWEEPS",
                        distance_km=None,
                        uncovered_area_m2=None,
                        selected_survey_order_changed=None,
                        error=str(error),
                    )
                )
                continue
            results.append(
                UrbanObstacleOrderMeasurement(
                    order=order,
                    radius_m=radius_m,
                    status="FEASIBLE",
                    distance_km=curved.distance_km,
                    uncovered_area_m2=curved.uncovered_area_m2,
                    selected_survey_order_changed=(
                        curved.survey_legs != source_legs if order == "reordered_blocks" else False
                    ),
                    error=None,
                )
            )

    return UrbanObstacleOrderBenchmark(
        case_id=case.case_id,
        coverage_angle_degrees=coverage.selected_flight_direction_degrees,
        survey_leg_count=len(source_route) // 2,
        comparison=(
            "same turn-aware candidate/retreat search, block reordering disabled for baseline"
        ),
        planner="Hybrid A* (default app settings, max 20,000 expansions per transition)",
        results=results,
    )


def _evaluate_original_order(
    request: TurnAwareSweepRequest,
    transition_planner: HybridAStarSweepTransitionPlanner,
) -> TurnAwareSweepResult:
    with patch("geoplan.navigation.turn_aware._reorder_obstacle_blocks", return_value=None):
        return build_turn_aware_sweep_route(request, transition_planner)


def _evaluate_reordered_blocks(
    request: TurnAwareSweepRequest,
    transition_planner: HybridAStarSweepTransitionPlanner,
) -> TurnAwareSweepResult:
    return build_turn_aware_sweep_route(request, transition_planner)


if __name__ == "__main__":
    print(json.dumps(run_experiment().model_dump(mode="json"), ensure_ascii=False, indent=2))
