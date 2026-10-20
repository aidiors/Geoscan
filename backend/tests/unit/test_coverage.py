import pytest

from geoplan.coverage.models import (
    CoverageArea,
    CoveragePlanRequest,
    SensorProfile,
    SurveyParameters,
)
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner, CoveragePlanningError
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import ErrorCode, SweepAngleAlgorithm, SweepDirectionMode
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.routing import (
    build_safe_route,
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_coverage_area,
)


def sensor_profile() -> SensorProfile:
    return SensorProfile(
        sensor_id="rgb-camera",
        name="RGB-камера",
        sensor_width_mm=13.2,
        sensor_height_mm=8.8,
        focal_length_mm=8.8,
        image_width_px=5_472,
        image_height_px=3_648,
        minimum_trigger_interval_s=1,
    )


def coverage_area() -> CoverageArea:
    return CoverageArea(
        area_id="field",
        name="Поле",
        coordinates=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.71),
            Coordinate(longitude=37.4, latitude=55.71),
        ],
    )


def survey_parameters(maximum_gsd_cm: float = 5) -> SurveyParameters:
    return SurveyParameters(
        flight_direction_degrees=90,
        side_overlap_percent=60,
        forward_overlap_percent=75,
        maximum_gsd_cm=maximum_gsd_cm,
        strip_width_m=200,
        maximum_segment_distance_km=5,
        sensor=sensor_profile(),
    )


def narrow_appendage_request() -> CoveragePlanRequest:
    projection = LocalProjection(37, 55)
    area = CoverageArea(
        area_id="appendage",
        name="Thin side branch",
        coordinates=[
            projection.unproject(x, y)
            for x, y in (
                (0, 0),
                (400, 0),
                (400, 110),
                (700, 110),
                (700, 125),
                (400, 125),
                (400, 400),
                (0, 400),
            )
        ],
    )
    return CoveragePlanRequest(
        area=area,
        mission_altitude_m=100,
        parameters=survey_parameters().model_copy(
            update={"direction_mode": SweepDirectionMode.MANUAL}
        ),
    )


def test_fixed_angle_that_misses_a_narrow_appendage_is_rejected():
    with pytest.raises(CoveragePlanningError):
        BoustrophedonCoveragePlanner().plan(narrow_appendage_request())


def test_bounded_angles_find_a_complete_layout_instead_of_the_shorter_incomplete_one():
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    from geoplan.coverage.sweep import _flyable_geometry

    request = narrow_appendage_request()
    request = request.model_copy(
        update={
            "parameters": request.parameters.model_copy(
                update={"direction_mode": SweepDirectionMode.AUTOMATIC}
            )
        }
    )
    planner = BoustrophedonCoveragePlanner(SweepAngleAlgorithm.BOUNDED_PORTFOLIO)
    first = planner.plan(request)
    second = planner.plan(request)
    assert first == second
    target, projection = _flyable_geometry(request)
    strips = [
        LineString([projection.project(p) for p in segment.route[i : i + 2]])
        for segment in first.segments
        for i in range(0, len(segment.route) - 1, 2)
    ]
    covered = unary_union(
        [strip.buffer(first.footprint_width_m / 2, cap_style="square") for strip in strips]
    )
    assert covered.intersection(target).area / target.area >= 0.99


def test_boustrophedon_planner_generates_snake_route_inside_area() -> None:
    area = coverage_area()

    result = BoustrophedonCoveragePlanner().plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=survey_parameters(),
        )
    )

    assert len(result.segments) >= 2
    assert result.angle_algorithm is SweepAngleAlgorithm.GEOMETRY_AWARE
    assert result.strip_spacing_m == 60
    assert result.computed_gsd_cm == pytest.approx(2.741, abs=0.001)
    assert all(route_is_inside_coverage_area(segment.route, area) for segment in result.segments)
    assert all(route_distance_km(segment.route) <= 5.8 for segment in result.segments)
    assert all(segment.image_count is not None for segment in result.segments)
    assert any(segment.alternative_routes for segment in result.segments)
    assert all(
        route_is_inside_coverage_area(route, area)
        for segment in result.segments
        for route in segment.alternative_routes
    )


def test_manual_sweep_direction_is_preserved() -> None:
    parameters = survey_parameters().model_copy(
        update={
            "direction_mode": SweepDirectionMode.MANUAL,
            "flight_direction_degrees": 37,
        }
    )

    result = BoustrophedonCoveragePlanner().plan(
        CoveragePlanRequest(
            area=coverage_area(),
            mission_altitude_m=100,
            parameters=parameters,
        )
    )

    assert result.selected_flight_direction_degrees == 37
    assert result.angle_algorithm is SweepAngleAlgorithm.FIXED_INPUT


def test_automatic_sweep_direction_minimizes_wind_adjusted_duration() -> None:
    area = CoverageArea(
        area_id="wind-field",
        name="Поле для ветрового сценария",
        coordinates=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.42, latitude=55.7),
            Coordinate(longitude=37.42, latitude=55.712),
            Coordinate(longitude=37.4, latitude=55.712),
        ],
    )
    planner = BoustrophedonCoveragePlanner()
    calm = planner.plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=survey_parameters().model_copy(update={"maximum_segment_distance_km": 100}),
        )
    )
    windy = planner.plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=survey_parameters().model_copy(update={"maximum_segment_distance_km": 100}),
            wind=WindCondition(speed_mps=10, direction_degrees=0),
            reference_airspeed_kmh=55,
        )
    )
    aligned_wind = planner.plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=survey_parameters().model_copy(update={"maximum_segment_distance_km": 100}),
            wind=WindCondition(speed_mps=10, direction_degrees=90),
            reference_airspeed_kmh=55,
        )
    )

    assert calm.selected_flight_direction_degrees == 0
    assert windy.selected_flight_direction_degrees == 90
    assert aligned_wind.selected_flight_direction_degrees == calm.selected_flight_direction_degrees
    assert all(route_is_inside_coverage_area(segment.route, area) for segment in windy.segments)


def test_boustrophedon_planner_rejects_altitude_that_breaks_gsd_requirement() -> None:
    request = CoveragePlanRequest(
        area=coverage_area(),
        mission_altitude_m=100,
        parameters=survey_parameters(maximum_gsd_cm=1),
    )

    with pytest.raises(CoveragePlanningError) as error:
        BoustrophedonCoveragePlanner().plan(request)

    assert error.value.code is ErrorCode.GSD_REQUIREMENT_UNSATISFIED


def test_boustrophedon_route_can_detour_around_buffered_restriction_corners() -> None:
    area = CoverageArea(
        area_id="agro",
        name="Ограниченное поле",
        coordinates=[
            Coordinate(longitude=39.684, latitude=54.596),
            Coordinate(longitude=39.746, latitude=54.596),
            Coordinate(longitude=39.746, latitude=54.629),
            Coordinate(longitude=39.684, latitude=54.629),
        ],
    )
    restriction = NoFlyZone(
        zone_id="nature-reserve",
        coordinates=[
            Coordinate(longitude=39.712, latitude=54.606),
            Coordinate(longitude=39.721, latitude=54.606),
            Coordinate(longitude=39.721, latitude=54.613),
            Coordinate(longitude=39.712, latitude=54.613),
        ],
        safety_margin_m=30,
    )

    result = BoustrophedonCoveragePlanner().plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=survey_parameters(),
            exclusions=[restriction],
        )
    )

    safe_routes = [build_safe_route(segment.route, [restriction]) for segment in result.segments]
    assert all(route_is_clear_of_no_fly_zones(route, [restriction]) for route in safe_routes)
