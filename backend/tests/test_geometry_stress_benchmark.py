from math import pi

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from src.coverage import BoustrophedonCoveragePlanner
from src.coverage_benchmark import (
    geometry_stress_coverage_benchmark_cases,
    large_scattered_work_area_cases,
)
from src.geometry import LocalProjection
from src.routing import route_is_clear_of_no_fly_zones, route_is_inside_coverage_area


def test_geometry_stress_benchmark_contains_round_jagged_and_scattered_cases() -> None:
    cases = geometry_stress_coverage_benchmark_cases()

    assert [case.case_id for case in cases] == [
        "round_32",
        "sawtooth_98",
        "jagged_48",
        "large_scattered_three_components",
    ]


def test_round_case_is_a_valid_near_circle() -> None:
    case = geometry_stress_coverage_benchmark_cases()[0]
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    polygon = Polygon([projection.project(point) for point in case.request.area.coordinates])

    assert polygon.is_valid
    assert len(case.request.area.coordinates) == 32
    assert abs(polygon.area - pi * 650**2) / (pi * 650**2) < 0.01
    assert polygon.area / polygon.convex_hull.area > 0.99


def test_jagged_case_has_many_vertices_and_deep_indentations() -> None:
    case = geometry_stress_coverage_benchmark_cases()[2]
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    polygon = Polygon([projection.project(point) for point in case.request.area.coordinates])

    assert polygon.is_valid
    assert len(case.request.area.coordinates) == 48
    assert polygon.area / polygon.convex_hull.area < 0.7
    assert polygon.length / polygon.convex_hull.length > 1.3


def test_sawtooth_case_has_many_sharp_bends() -> None:
    case = geometry_stress_coverage_benchmark_cases()[1]
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    polygon = Polygon([projection.project(point) for point in case.request.area.coordinates])

    assert polygon.is_valid
    assert len(case.request.area.coordinates) == 98
    assert polygon.length / polygon.convex_hull.length > 1.25


def test_large_scattered_case_has_three_large_disconnected_flyable_regions() -> None:
    case = geometry_stress_coverage_benchmark_cases()[3]
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    target = Polygon([projection.project(point) for point in case.request.area.coordinates])
    exclusions = unary_union(
        [
            Polygon([projection.project(point) for point in zone.coordinates]).buffer(
                zone.safety_margin_m
            )
            for zone in case.request.exclusions
        ]
    )
    flyable = target.difference(exclusions)

    assert target.is_valid
    assert 50_000_000 < target.area < 60_000_000
    assert isinstance(flyable, MultiPolygon)
    assert len(flyable.geoms) == 3
    assert min(component.area for component in flyable.geoms) > 10_000_000


def test_large_scattered_work_areas_are_three_separate_large_polygons() -> None:
    cases = large_scattered_work_area_cases()
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    polygons = [
        Polygon([projection.project(point) for point in case.request.area.coordinates])
        for case in cases
    ]

    assert len(cases) == 3
    assert all(polygon.is_valid and polygon.area > 14_000_000 for polygon in polygons)
    assert all(
        first.distance(second) > 5_000
        for index, first in enumerate(polygons)
        for second in polygons[index + 1 :]
    )


def test_stress_geometries_produce_only_safe_connected_sweep_sections() -> None:
    planner = BoustrophedonCoveragePlanner()

    for case in geometry_stress_coverage_benchmark_cases():
        result = planner.plan(case.request)

        assert result.segments
        assert all(len(segment.route) % 2 == 0 for segment in result.segments)
        assert all(
            route_is_inside_coverage_area(segment.route, case.request.area)
            for segment in result.segments
        )
        assert all(
            route_is_clear_of_no_fly_zones(segment.route, case.request.exclusions)
            for segment in result.segments
        )
