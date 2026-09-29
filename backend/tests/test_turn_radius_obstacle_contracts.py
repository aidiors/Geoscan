from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import default_coverage_benchmark_cases
from src.curvature import PlannedSweepTransition
from src.geometry import LocalProjection, projection_for_coordinates
from src.routing import RouteGeometryError
from src.schemas import (
    Coordinate,
    CoveragePlanRequest,
    NoFlyZone,
    SweepRouteCurvatureRequest,
    TurnRadiusAnalysisRequest,
    TurnRadiusStatus,
)
from src.turn_radius_analysis import analyze_turn_radii


class ObstacleRejectingTransitionPlanner:
    def __init__(self) -> None:
        self.requests: list[SweepRouteCurvatureRequest] = []

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition:
        self.requests.append(request)
        raise RouteGeometryError("Test planner rejects obstacle transitions.")


def test_overlapping_no_fly_zones_return_stable_no_route_contract() -> None:
    coverage = _coverage_with_center_block()
    projection = projection_for_coordinates(coverage.area.coordinates)
    center_x, center_y = _center(coverage)
    first = _metric_rectangle("overlap-a", projection, center_x, center_y, 80)
    second = _metric_rectangle("overlap-b", projection, center_x + 25, center_y, 80)
    request = coverage.model_copy(update={"exclusions": [first, second]})

    transition_planner = ObstacleRejectingTransitionPlanner()
    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=request, radii_m=[15, 30]),
        BoustrophedonCoveragePlanner(),
        transition_planner,
    )

    assert result.area_id == coverage.area.area_id
    assert [scenario.status for scenario in result.scenarios] == [
        TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS,
        TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS,
    ]
    assert result.common_survey_route is None
    assert transition_planner.requests
    assert all(len(item.no_fly_zones) == 2 for item in transition_planner.requests)


def test_interior_hole_reports_not_evaluated_when_sweeps_split() -> None:
    coverage = _coverage_with_center_block()
    projection = projection_for_coordinates(coverage.area.coordinates)
    center_x, center_y = _center(coverage)
    hole = _metric_ring(projection, center_x, center_y, 80)
    area = coverage.area.model_copy(update={"interior_rings": [hole]})
    request = coverage.model_copy(update={"area": area})

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=request, radii_m=[15, 30]),
        BoustrophedonCoveragePlanner(),
        ObstacleRejectingTransitionPlanner(),
    )

    assert result.area_id == area.area_id
    assert [scenario.status for scenario in result.scenarios] == [
        TurnRadiusStatus.NOT_EVALUATED,
        TurnRadiusStatus.NOT_EVALUATED,
    ]
    assert result.common_survey_route is None


def _coverage_with_center_block() -> CoveragePlanRequest:
    return default_coverage_benchmark_cases()[0].request


def _center(coverage: CoveragePlanRequest) -> tuple[float, float]:
    projection = projection_for_coordinates(coverage.area.coordinates)
    projected = [projection.project(point) for point in coverage.area.coordinates]
    return (
        sum(point[0] for point in projected) / len(projected),
        sum(point[1] for point in projected) / len(projected),
    )


def _metric_ring(
    projection: LocalProjection,
    center_x: float,
    center_y: float,
    half_size_m: float,
) -> list[Coordinate]:
    return [
        projection.unproject(center_x - half_size_m, center_y - half_size_m),
        projection.unproject(center_x + half_size_m, center_y - half_size_m),
        projection.unproject(center_x + half_size_m, center_y + half_size_m),
        projection.unproject(center_x - half_size_m, center_y + half_size_m),
    ]


def _metric_rectangle(
    zone_id: str,
    projection: LocalProjection,
    center_x: float,
    center_y: float,
    half_size_m: float,
) -> NoFlyZone:
    return NoFlyZone(
        zone_id=zone_id,
        coordinates=_metric_ring(projection, center_x, center_y, half_size_m),
    )
