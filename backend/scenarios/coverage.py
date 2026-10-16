from __future__ import annotations

from itertools import pairwise
from math import cos, radians, sin, tau

from shapely.geometry import LineString, Polygon

from benchmarks.models import CoverageBenchmarkCase
from geoplan.coverage.models import (
    CoverageArea,
    CoveragePlanRequest,
    SensorProfile,
    SurveyParameters,
)
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import SweepDirectionMode
from geoplan.domain.geo import Coordinate
from geoplan.navigation.geometry import LocalProjection


def default_coverage_benchmark_cases() -> list[CoverageBenchmarkCase]:
    return [
        _case("axis_aligned", "Axis-aligned rectangle", _rectangle(1_400, 500, 0)),
        _case("rotated_35", "Narrow rectangle rotated by 35 degrees", _rectangle(1_500, 350, 35)),
        _case(
            "diagonal_corridor",
            "Diagonal corridor rotated by 67 degrees",
            _rectangle(1_800, 220, 67),
        ),
        _case("concave_l", "Rotated concave L-shaped area", _rotated_l_shape(28)),
        _restricted_case(),
        *corridor_coverage_benchmark_cases(),
    ]


def geometry_stress_coverage_benchmark_cases() -> list[CoverageBenchmarkCase]:
    return [
        _case("round_32", "Thirty-two-sided near-circular survey area", _radial_ring(32, 650, 650)),
        _case("sawtooth_98", "Ninety-eight-vertex serrated survey boundary", _sawtooth_ring()),
        _case(
            "jagged_48",
            "Forty-eight-vertex deeply indented survey area",
            _radial_ring(48, 900, 520),
        ),
        _large_scattered_case(),
    ]


def large_scattered_work_area_cases() -> list[CoverageBenchmarkCase]:
    return [
        _case(
            "large_scattered_west",
            "Western 15-square-kilometre work area",
            _rectangle(5_000, 3_000, 12, center_x=-12_000, center_y=-1_000),
        ),
        _case(
            "large_scattered_center",
            "Central 15-square-kilometre work area",
            _rectangle(5_000, 3_000, 35, center_y=1_500),
        ),
        _case(
            "large_scattered_east",
            "Eastern 15-square-kilometre work area",
            _rectangle(5_000, 3_000, 78, center_x=12_000, center_y=-500),
        ),
    ]


def urban_obstacle_coverage_case() -> CoverageBenchmarkCase:
    """Return the urban-rgb scenario geometry in geographic coordinates."""
    area_coordinates = [
        (37.378, 55.686),
        (37.442, 55.686),
        (37.442, 55.717),
        (37.378, 55.717),
    ]
    obstacle_coordinates = [
        (37.407, 55.698),
        (37.415, 55.698),
        (37.415, 55.704),
        (37.407, 55.704),
    ]
    description = "Urban RGB survey area with the urban-rgb restricted zone"
    case_id = "urban_obstacle"
    return CoverageBenchmarkCase(
        case_id=case_id,
        description=description,
        request=CoveragePlanRequest(
            area=CoverageArea(
                area_id="urban-target",
                name="Urban RGB target area",
                coordinates=[
                    Coordinate(longitude=lon, latitude=lat) for lon, lat in area_coordinates
                ],
            ),
            mission_altitude_m=120,
            parameters=_survey_parameters(),
            exclusions=[
                NoFlyZone(
                    zone_id="urban-restricted",
                    coordinates=[
                        Coordinate(longitude=lon, latitude=lat) for lon, lat in obstacle_coordinates
                    ],
                )
            ],
        ),
    )


def corridor_coverage_benchmark_cases() -> list[CoverageBenchmarkCase]:
    return [
        _case(
            "long_corridor_12",
            "Five-kilometre corridor rotated by 12 degrees",
            _rectangle(5_000, 260, 12),
        ),
        _case(
            "long_corridor_43",
            "Five-kilometre corridor rotated by 43 degrees",
            _rectangle(5_000, 260, 43),
        ),
        _case(
            "long_corridor_78",
            "Five-kilometre corridor rotated by 78 degrees",
            _rectangle(5_000, 260, 78),
        ),
        _corridor_case(
            "gradual_bend_corridor",
            "Long corridor with a gradually changing heading",
            [(-2_800, -700), (-1_500, -600), (-200, -200), (1_200, 450), (2_800, 700)],
            360,
        ),
        _corridor_case(
            "zigzag_corridor",
            "Long corridor with four alternating headings",
            [(-3_000, -650), (-1_500, 500), (0, -450), (1_500, 550), (3_000, -350)],
            460,
        ),
    ]


def _case(
    case_id: str,
    description: str,
    ring: list[Coordinate],
) -> CoverageBenchmarkCase:
    return CoverageBenchmarkCase(
        case_id=case_id,
        description=description,
        request=CoveragePlanRequest(
            area=CoverageArea(area_id=case_id, name=description, coordinates=ring),
            mission_altitude_m=100,
            parameters=_survey_parameters(),
        ),
    )


def _corridor_case(
    case_id: str,
    description: str,
    centerline: list[tuple[float, float]],
    width_m: float,
) -> CoverageBenchmarkCase:
    full_ring = _corridor_ring(centerline, width_m)
    partitioned_requests = [
        _coverage_request(
            f"{case_id}-part-{index + 1}",
            f"{description}, part {index + 1}",
            _corridor_ring([start, end], width_m),
        )
        for index, (start, end) in enumerate(pairwise(centerline))
    ]
    return CoverageBenchmarkCase(
        case_id=case_id,
        description=description,
        request=_coverage_request(case_id, description, full_ring),
        partitioned_requests=partitioned_requests,
    )


def _coverage_request(
    area_id: str,
    name: str,
    ring: list[Coordinate],
) -> CoveragePlanRequest:
    return CoveragePlanRequest(
        area=CoverageArea(area_id=area_id, name=name, coordinates=ring),
        mission_altitude_m=100,
        parameters=_survey_parameters(),
    )


def _corridor_ring(
    centerline: list[tuple[float, float]],
    width_m: float,
) -> list[Coordinate]:
    geometry = LineString(centerline).buffer(
        width_m / 2,
        cap_style="flat",
        join_style="mitre",
    )
    if not isinstance(geometry, Polygon):
        raise ValueError("Коридор должен образовывать один связный полигон.")
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    return [
        projection.unproject(float(x), float(y)) for x, y in list(geometry.exterior.coords)[:-1]
    ]


def _restricted_case() -> CoverageBenchmarkCase:
    area_ring = _rectangle(1_600, 900, 22)
    exclusions = [
        NoFlyZone(
            zone_id="restricted-a",
            coordinates=_rectangle(260, 220, 22, center_x=-320, center_y=-40),
            safety_margin_m=25,
        ),
        NoFlyZone(
            zone_id="restricted-b",
            coordinates=_rectangle(300, 180, 22, center_x=330, center_y=100),
            safety_margin_m=25,
        ),
    ]
    return CoverageBenchmarkCase(
        case_id="two_restrictions",
        description="Rotated area with two exclusion polygons",
        request=CoveragePlanRequest(
            area=CoverageArea(
                area_id="two_restrictions",
                name="Rotated area with exclusions",
                coordinates=area_ring,
            ),
            mission_altitude_m=100,
            parameters=_survey_parameters(),
            exclusions=exclusions,
        ),
    )


def _large_scattered_case() -> CoverageBenchmarkCase:
    description = "Three large separated flyable regions within one target area"
    return CoverageBenchmarkCase(
        case_id="large_scattered_three_components",
        description=description,
        request=CoveragePlanRequest(
            area=CoverageArea(
                area_id="large_scattered_three_components",
                name=description,
                coordinates=_rectangle(18_000, 3_000, 0),
            ),
            mission_altitude_m=100,
            parameters=_survey_parameters(),
            exclusions=[
                NoFlyZone(
                    zone_id=f"separator-{index + 1}",
                    coordinates=_rectangle(1_000, 3_400, 0, center_x=center_x),
                    safety_margin_m=30,
                )
                for index, center_x in enumerate((-3_000, 3_000))
            ],
        ),
    )


def _survey_parameters() -> SurveyParameters:
    return SurveyParameters(
        direction_mode=SweepDirectionMode.AUTOMATIC,
        flight_direction_degrees=90,
        side_overlap_percent=60,
        forward_overlap_percent=75,
        maximum_gsd_cm=5,
        strip_width_m=200,
        maximum_segment_distance_km=100,
        sensor=SensorProfile(
            sensor_id="benchmark-camera",
            name="Эталонная RGB-камера",
            sensor_width_mm=13.2,
            sensor_height_mm=8.8,
            focal_length_mm=8.8,
            image_width_px=5_472,
            image_height_px=3_648,
            minimum_trigger_interval_s=1,
        ),
    )


def _rectangle(
    width_m: float,
    height_m: float,
    rotation_degrees: float,
    center_x: float = 0,
    center_y: float = 0,
) -> list[Coordinate]:
    half_width = width_m / 2
    half_height = height_m / 2
    points = [
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
        (-half_width, half_height),
    ]
    return _coordinates(points, rotation_degrees, center_x, center_y)


def _rotated_l_shape(rotation_degrees: float) -> list[Coordinate]:
    points = [
        (-750, -500),
        (750, -500),
        (750, -120),
        (100, -120),
        (100, 500),
        (-750, 500),
    ]
    return _coordinates(points, rotation_degrees, 0, 0)


def _radial_ring(
    vertex_count: int, outer_radius_m: float, inner_radius_m: float
) -> list[Coordinate]:
    points = [
        (
            (outer_radius_m if index % 2 == 0 else inner_radius_m)
            * cos(tau * index / vertex_count),
            (outer_radius_m if index % 2 == 0 else inner_radius_m)
            * sin(tau * index / vertex_count),
        )
        for index in range(vertex_count)
    ]
    return _coordinates(points, 0, 0, 0)


def _sawtooth_ring() -> list[Coordinate]:
    top = [(-500 + index * 1_000 / 48, 400 if index % 2 == 0 else 375) for index in range(49)]
    bottom = [
        (-500 + index * 1_000 / 48, -400 if index % 2 == 0 else -375)
        for index in reversed(range(49))
    ]
    return _coordinates([*top, *bottom], 0, 0, 0)


def _coordinates(
    points: list[tuple[float, float]],
    rotation_degrees: float,
    center_x: float,
    center_y: float,
) -> list[Coordinate]:
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    angle = radians(rotation_degrees)
    return [
        projection.unproject(
            point[0] * cos(angle) - point[1] * sin(angle) + center_x,
            point[0] * sin(angle) + point[1] * cos(angle) + center_y,
        )
        for point in points
    ]
