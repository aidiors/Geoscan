from __future__ import annotations

from math import cos, radians

from geoplan.coverage.models import CoverageArea, WorkSegment
from geoplan.domain.airspace import FlightObstacle, NoFlyZone
from geoplan.domain.enums import AltitudeReference, ValidityMode
from geoplan.domain.geo import Coordinate
from geoplan.domain.identifiers import derived_id
from geoplan.planning.models import PlanRequest

GEOMETRY_CLEARANCE_M = 0.5


def _coverage_area(
    areas: list[CoverageArea],
    area_id: str | None,
) -> CoverageArea | None:
    if area_id is None:
        return None
    return next((area for area in areas if area.area_id == area_id), None)


def _constraints_for_segment(
    segment: WorkSegment,
    request: PlanRequest,
) -> list[NoFlyZone]:
    coverage_holes = _coverage_hole_zones(segment, request.coverage_areas)
    obstacle_zones = [
        _obstacle_zone(obstacle)
        for obstacle in request.obstacles
        if (
            request.altitude_reference is AltitudeReference.AGL
            or _obstacle_requires_avoidance(obstacle, request.mission_altitude_m)
        )
        and _obstacle_is_near_segment(obstacle, segment)
    ]
    permanent_no_fly_zones = [
        zone
        for zone in request.no_fly_zones
        if (
            zone.altitude_reference is not request.altitude_reference
            or _zone_applies_at_altitude(zone, request.mission_altitude_m)
        )
        and (zone.validity is None or zone.validity.mode is ValidityMode.PERMANENT)
    ]
    return [*permanent_no_fly_zones, *coverage_holes, *obstacle_zones]


def _zone_applies_at_altitude(zone: NoFlyZone, altitude_m: float) -> bool:
    if zone.minimum_altitude_m is None or zone.maximum_altitude_m is None:
        return True
    return zone.minimum_altitude_m <= altitude_m <= zone.maximum_altitude_m


def _coverage_hole_zones(
    segment: WorkSegment,
    coverage_areas: list[CoverageArea],
) -> list[NoFlyZone]:
    return [
        NoFlyZone(
            zone_id=derived_id(area.area_id, f"-hole-{index + 1}"),
            coordinates=ring,
            safety_margin_m=GEOMETRY_CLEARANCE_M,
        )
        for area in coverage_areas
        if area.area_id in segment.area_ids
        for index, ring in enumerate(area.interior_rings)
        if len(ring) >= 3
    ]


def _obstacle_requires_avoidance(
    obstacle: FlightObstacle,
    mission_altitude_m: float,
) -> bool:
    vertical_clearance_m = 30
    top = obstacle.known_top_msl_m
    return top is None or mission_altitude_m <= top + vertical_clearance_m


def _obstacle_is_near_segment(obstacle: FlightObstacle, segment: WorkSegment) -> bool:
    segment_longitudes = [coordinate.longitude for coordinate in segment.route]
    segment_latitudes = [coordinate.latitude for coordinate in segment.route]
    obstacle_longitudes = [coordinate.longitude for coordinate in obstacle.coordinates]
    obstacle_latitudes = [coordinate.latitude for coordinate in obstacle.coordinates]
    margin = obstacle.safety_radius_m / 111_000
    return not (
        max(obstacle_longitudes) + margin < min(segment_longitudes)
        or min(obstacle_longitudes) - margin > max(segment_longitudes)
        or max(obstacle_latitudes) + margin < min(segment_latitudes)
        or min(obstacle_latitudes) - margin > max(segment_latitudes)
    )


def _obstacle_zone(obstacle: FlightObstacle) -> NoFlyZone:
    longitudes = [coordinate.longitude for coordinate in obstacle.coordinates]
    latitudes = [coordinate.latitude for coordinate in obstacle.coordinates]
    center_latitude = (min(latitudes) + max(latitudes)) / 2
    latitude_margin = obstacle.safety_radius_m / 111_000
    longitude_scale = max(cos(radians(center_latitude)), 0.2)
    longitude_margin = latitude_margin / longitude_scale
    minimum_longitude = min(longitudes) - longitude_margin
    maximum_longitude = max(longitudes) + longitude_margin
    minimum_latitude = min(latitudes) - latitude_margin
    maximum_latitude = max(latitudes) + latitude_margin
    return NoFlyZone(
        zone_id=f"obstacle-{obstacle.obstacle_id}"[:64],
        coordinates=[
            Coordinate(longitude=minimum_longitude, latitude=minimum_latitude),
            Coordinate(longitude=maximum_longitude, latitude=minimum_latitude),
            Coordinate(longitude=maximum_longitude, latitude=maximum_latitude),
            Coordinate(longitude=minimum_longitude, latitude=maximum_latitude),
        ],
        safety_margin_m=0,
    )
