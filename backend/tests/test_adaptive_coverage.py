from src.adaptive_coverage import AdaptiveCoveragePlanner
from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import corridor_coverage_benchmark_cases
from src.routing import (
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_coverage_area,
)
from src.schemas import SweepAngleAlgorithm, SweepDirectionMode


def test_adaptive_planner_uses_local_angles_for_bent_corridor() -> None:
    case = next(
        item
        for item in corridor_coverage_benchmark_cases()
        if item.case_id == "gradual_bend_corridor"
    )
    baseline = BoustrophedonCoveragePlanner().plan(case.request)

    result = AdaptiveCoveragePlanner().plan(case.request)

    baseline_distance = sum(route_distance_km(segment.route) for segment in baseline.segments)
    adaptive_distance = sum(route_distance_km(segment.route) for segment in result.segments)
    directions = {
        segment.flight_direction_degrees
        for segment in result.segments
        if segment.flight_direction_degrees is not None
    }
    assert result.angle_algorithm is SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION
    assert adaptive_distance < baseline_distance * 0.97
    assert len(directions) >= 3
    assert len({segment.segment_id for segment in result.segments}) == len(result.segments)
    assert all(segment.coverage_area_id == case.request.area.area_id for segment in result.segments)
    assert all(
        route_is_inside_coverage_area(segment.route, case.request.area)
        for segment in result.segments
    )
    assert all(
        route_is_clear_of_no_fly_zones(segment.route, case.request.exclusions)
        for segment in result.segments
    )


def test_adaptive_planner_preserves_manual_direction() -> None:
    case = next(
        item
        for item in corridor_coverage_benchmark_cases()
        if item.case_id == "gradual_bend_corridor"
    )
    parameters = case.request.parameters.model_copy(
        update={
            "direction_mode": SweepDirectionMode.MANUAL,
            "flight_direction_degrees": 90,
        }
    )

    result = AdaptiveCoveragePlanner().plan(
        case.request.model_copy(update={"parameters": parameters})
    )

    assert result.angle_algorithm is SweepAngleAlgorithm.FIXED_INPUT
    assert result.selected_flight_direction_degrees == 90
    assert all(segment.flight_direction_degrees == 90 for segment in result.segments)
