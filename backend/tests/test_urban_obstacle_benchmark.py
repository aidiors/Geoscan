import pytest
from pydantic import ValidationError
from shapely.geometry import Polygon

from src.coverage_benchmark import urban_obstacle_coverage_case
from src.geometry import LocalProjection
from src.schemas import UrbanObstacleOrderBenchmark, UrbanObstacleOrderMeasurement


def test_urban_obstacle_case_preserves_urban_rgb_geometry() -> None:
    case = urban_obstacle_coverage_case()
    request = case.request
    projection = LocalProjection(
        origin_longitude=37.41,
        origin_latitude=55.7015,
    )
    area = Polygon([projection.project(point) for point in request.area.coordinates])
    exclusion = request.exclusions[0]
    obstacle = Polygon([projection.project(point) for point in exclusion.coordinates])

    assert case.case_id == "urban_obstacle"
    assert len(request.area.coordinates) == 4
    assert len(request.exclusions) == 1
    assert exclusion.zone_id == "urban-restricted"
    assert [(point.longitude, point.latitude) for point in request.area.coordinates] == [
        (37.378, 55.686),
        (37.442, 55.686),
        (37.442, 55.717),
        (37.378, 55.717),
    ]
    assert [(point.longitude, point.latitude) for point in exclusion.coordinates] == [
        (37.407, 55.698),
        (37.415, 55.698),
        (37.415, 55.704),
        (37.407, 55.704),
    ]
    assert area.is_valid
    assert obstacle.is_valid
    assert area.contains(obstacle)
    assert 13_000_000 < area.area < 14_000_000
    assert 250_000 < obstacle.area < 350_000


def test_urban_obstacle_benchmark_result_uses_validated_schema() -> None:
    measurement = UrbanObstacleOrderMeasurement(
        order="reordered_blocks",
        radius_m=15,
        status="FEASIBLE",
        distance_km=193.143,
        uncovered_area_m2=0,
        selected_survey_order_changed=True,
    )
    result = UrbanObstacleOrderBenchmark(
        case_id="urban_obstacle",
        coverage_angle_degrees=179,
        survey_leg_count=65,
        comparison="Block reordering enabled",
        planner="Hybrid A*",
        results=[measurement],
    )

    assert result.results[0].distance_km == 193.143
    with pytest.raises(ValidationError):
        UrbanObstacleOrderMeasurement.model_validate({**measurement.model_dump(), "radius_m": 0})


def test_urban_obstacle_benchmark_rejects_inconsistent_measurements() -> None:
    with pytest.raises(ValidationError, match="нужны длина и площадь"):
        UrbanObstacleOrderMeasurement(
            order="reordered_blocks",
            radius_m=15,
            status="FEASIBLE",
        )
    with pytest.raises(ValidationError, match="нельзя указывать измерения"):
        UrbanObstacleOrderMeasurement(
            order="original",
            radius_m=40,
            status="NO_ROUTE_FOR_SELECTED_SWEEPS",
            distance_km=100,
        )
