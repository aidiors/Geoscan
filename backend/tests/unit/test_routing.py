import pytest

from geoplan.domain.airspace import AllowedAirspace, NoFlyZone
from geoplan.domain.geo import Coordinate
from geoplan.navigation.routing import (
    RouteGeometryError,
    build_safe_route,
    calculate_route_multiplier,
    route_is_clear_of_no_fly_zones,
    route_is_inside_allowed_airspaces,
)


def test_build_safe_route_returns_original_route_when_zone_is_not_crossed() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.42, latitude=55.7),
    ]
    zone = NoFlyZone(
        zone_id="north",
        coordinates=[
            Coordinate(longitude=37.408, latitude=55.71),
            Coordinate(longitude=37.412, latitude=55.71),
            Coordinate(longitude=37.412, latitude=55.712),
            Coordinate(longitude=37.408, latitude=55.712),
        ],
    )

    safe_route = build_safe_route(route, [zone])

    assert safe_route == route


def test_build_safe_route_adds_detour_when_zone_is_crossed() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.42, latitude=55.7),
    ]
    zone = NoFlyZone(
        zone_id="crossing",
        coordinates=[
            Coordinate(longitude=37.408, latitude=55.698),
            Coordinate(longitude=37.412, latitude=55.698),
            Coordinate(longitude=37.412, latitude=55.702),
            Coordinate(longitude=37.408, latitude=55.702),
        ],
    )

    safe_route = build_safe_route(route, [zone])

    assert len(safe_route) == 4
    assert calculate_route_multiplier(route, safe_route) > 1


def test_route_is_inside_allowed_airspaces_when_route_is_covered() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.42, latitude=55.7),
    ]
    airspace = AllowedAirspace(
        airspace_id="corridor",
        coordinates=[
            Coordinate(longitude=37.39, latitude=55.69),
            Coordinate(longitude=37.43, latitude=55.69),
            Coordinate(longitude=37.43, latitude=55.71),
            Coordinate(longitude=37.39, latitude=55.71),
        ],
        minimum_altitude_m=20,
        maximum_altitude_m=500,
    )

    assert route_is_inside_allowed_airspaces(route, [airspace])


def test_route_is_outside_allowed_airspaces_when_endpoint_is_not_covered() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.42, latitude=55.7),
    ]
    airspace = AllowedAirspace(
        airspace_id="corridor",
        coordinates=[
            Coordinate(longitude=37.39, latitude=55.69),
            Coordinate(longitude=37.41, latitude=55.69),
            Coordinate(longitude=37.41, latitude=55.71),
            Coordinate(longitude=37.39, latitude=55.71),
        ],
        minimum_altitude_m=20,
        maximum_altitude_m=500,
    )

    assert not route_is_inside_allowed_airspaces(route, [airspace])


def test_build_safe_route_avoids_multiple_overlapping_zones() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.43, latitude=55.7),
    ]
    zones = [
        NoFlyZone(
            zone_id=f"zone-{index}",
            coordinates=[
                Coordinate(longitude=longitude, latitude=55.698),
                Coordinate(longitude=longitude + 0.008, latitude=55.698),
                Coordinate(longitude=longitude + 0.008, latitude=55.702),
                Coordinate(longitude=longitude, latitude=55.702),
            ],
        )
        for index, longitude in enumerate([37.406, 37.412])
    ]

    safe_route = build_safe_route(route, zones)

    assert len(safe_route) >= 4
    assert route_is_clear_of_no_fly_zones(safe_route, zones)


def test_route_inside_restriction_hole_remains_unchanged() -> None:
    route = [
        Coordinate(longitude=37.409, latitude=55.7),
        Coordinate(longitude=37.411, latitude=55.7),
    ]
    zone = NoFlyZone(
        zone_id="donut",
        coordinates=[
            Coordinate(longitude=37.4, latitude=55.69),
            Coordinate(longitude=37.42, latitude=55.69),
            Coordinate(longitude=37.42, latitude=55.71),
            Coordinate(longitude=37.4, latitude=55.71),
        ],
        interior_rings=[
            [
                Coordinate(longitude=37.407, latitude=55.697),
                Coordinate(longitude=37.413, latitude=55.697),
                Coordinate(longitude=37.413, latitude=55.703),
                Coordinate(longitude=37.407, latitude=55.703),
            ]
        ],
        safety_margin_m=0,
    )

    assert build_safe_route(route, [zone]) == route


def test_build_safe_route_fails_when_start_is_inside_restriction() -> None:
    route = [
        Coordinate(longitude=37.41, latitude=55.7),
        Coordinate(longitude=37.43, latitude=55.7),
    ]
    zone = NoFlyZone(
        zone_id="blocked-start",
        coordinates=[
            Coordinate(longitude=37.408, latitude=55.698),
            Coordinate(longitude=37.412, latitude=55.698),
            Coordinate(longitude=37.412, latitude=55.702),
            Coordinate(longitude=37.408, latitude=55.702),
        ],
    )

    with pytest.raises(RouteGeometryError):
        build_safe_route(route, [zone])
