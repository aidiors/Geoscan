import pytest
from pydantic import ValidationError

from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import (
    _candidate_section_plans,
    default_coverage_benchmark_cases,
    urban_obstacle_coverage_case,
)
from src.curvature import (
    HybridAStarSweepTransitionPlanner,
    build_curvature_aware_sweep_route_with_planner,
)
from src.dubins import ExactDubinsPlanner
from src.hybrid_astar import HybridAStarPlanner
from src.routing import RouteGeometryError, route_is_clear_of_no_fly_zones
from src.schemas import SweepRouteCurvatureRequest, TurnAwareSweepRequest
from src.turn_aware import build_turn_aware_sweep_route


def test_turn_aware_sweep_restores_safe_route_without_losing_camera_coverage() -> None:
    case = next(
        item for item in default_coverage_benchmark_cases() if item.case_id == "two_restrictions"
    )
    coverage = _candidate_section_plans(case.request)[0]
    route = coverage.segments[0].route
    planner = _transition_planner()
    original_request = SweepRouteCurvatureRequest(
        route=route,
        coverage_area=case.request.area,
        no_fly_zones=case.request.exclusions,
        minimum_turn_radius_m=30,
        sampling_step_m=3,
    )
    turn_request = TurnAwareSweepRequest(
        route=route,
        coverage_area=case.request.area,
        no_fly_zones=case.request.exclusions,
        minimum_turn_radius_m=30,
        sampling_step_m=3,
        strip_width_m=coverage.footprint_width_m,
        footprint_height_m=coverage.footprint_height_m,
    )

    with pytest.raises(RouteGeometryError, match=r"Hybrid A\*-переход"):
        build_curvature_aware_sweep_route_with_planner(original_request, planner)
    result = build_turn_aware_sweep_route(turn_request, planner)

    assert result.endpoint_retreat_m >= 30
    assert result.uncovered_area_m2 <= 1
    assert route_is_clear_of_no_fly_zones(result.route, case.request.exclusions)
    assert result.distance_km > 0


def test_turn_aware_sweep_rejects_source_route_with_incomplete_camera_coverage() -> None:
    case = next(
        item for item in default_coverage_benchmark_cases() if item.case_id == "two_restrictions"
    )
    route = _candidate_section_plans(case.request)[0].segments[0].route
    request = TurnAwareSweepRequest(
        route=route,
        coverage_area=case.request.area,
        no_fly_zones=case.request.exclusions,
        minimum_turn_radius_m=30,
        strip_width_m=10,
        footprint_height_m=10,
    )

    with pytest.raises(RouteGeometryError, match="полного покрытия"):
        build_turn_aware_sweep_route(request, _transition_planner())


def test_turn_aware_sweep_reorders_urban_obstacle_blocks_without_losing_coverage() -> None:
    case = urban_obstacle_coverage_case()
    parameters = case.request.parameters.model_copy(update={"maximum_segment_distance_km": 500})
    coverage_request = case.request.model_copy(update={"parameters": parameters})
    coverage = BoustrophedonCoveragePlanner().plan(coverage_request)
    source_route = coverage.segments[0].route
    request = TurnAwareSweepRequest(
        route=source_route,
        coverage_area=coverage_request.area,
        no_fly_zones=coverage_request.exclusions,
        minimum_turn_radius_m=15,
        strip_width_m=min(coverage.footprint_width_m, parameters.strip_width_m),
        footprint_height_m=coverage.footprint_height_m,
    )

    result = build_turn_aware_sweep_route(request, _transition_planner())

    assert result.survey_legs != [
        source_route[index : index + 2] for index in range(0, len(source_route), 2)
    ]
    assert result.uncovered_area_m2 <= 1
    assert route_is_clear_of_no_fly_zones(result.route, coverage_request.exclusions)


def test_turn_aware_request_rejects_incomplete_survey_leg() -> None:
    case = next(
        item for item in default_coverage_benchmark_cases() if item.case_id == "two_restrictions"
    )

    with pytest.raises(ValidationError, match="чётное число точек"):
        TurnAwareSweepRequest(
            route=case.request.area.coordinates[:3],
            coverage_area=case.request.area,
            minimum_turn_radius_m=30,
            strip_width_m=150,
            footprint_height_m=100,
        )


def _transition_planner() -> HybridAStarSweepTransitionPlanner:
    return HybridAStarSweepTransitionPlanner(
        planner=HybridAStarPlanner(dubins_planner=ExactDubinsPlanner()),
        primitive_length_m=15,
        grid_resolution_m=8,
        heading_bin_degrees=15,
        analytic_expansion_distance_m=180,
        maximum_expansions=20_000,
    )
