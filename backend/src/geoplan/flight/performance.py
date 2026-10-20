from __future__ import annotations

from itertools import pairwise
from math import cos, hypot, radians, sin, sqrt

from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.navigation.routing import coordinate_distance_km, route_bearing_degrees


class WindPerformanceError(ValueError):
    pass


def route_duration_minutes(
    route: list[Coordinate],
    distance_km: float,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
    ground_speed_limit_kmh: float | None = None,
) -> float:
    durations = route_leg_durations_minutes(
        route, distance_km, cruise_speed_kmh, wind, ground_speed_limit_kmh
    )
    return (
        sum(durations)
        if any(durations)
        else (distance_km / _limited_speed(cruise_speed_kmh, ground_speed_limit_kmh) * 60)
    )


def route_leg_durations_minutes(
    route: list[Coordinate],
    distance_km: float,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
    ground_speed_limit_kmh: float | None = None,
) -> list[float]:
    """One duration per geometric edge, including zero-length edges."""
    legs = [(start, end, coordinate_distance_km(start, end)) for start, end in pairwise(route)]
    total_length = sum(distance for _, _, distance in legs)
    if total_length == 0:
        times = [0.0] * len(legs)
        if times:
            times[-1] = distance_km / _limited_speed(cruise_speed_kmh, ground_speed_limit_kmh) * 60
        return times
    durations = []
    for start, end, geometric_distance in legs:
        if geometric_distance == 0:
            durations.append(0.0)
            continue
        leg_distance = distance_km * geometric_distance / total_length
        ground_speed = ground_speed_kmh(start, end, cruise_speed_kmh, wind)
        ground_speed = _limited_speed(ground_speed, ground_speed_limit_kmh)
        duration = leg_distance / ground_speed * 60
        if ground_speed_limit_kmh is not None:
            duration = max(
                duration,
                leg_distance
                / geometric_distance
                * _linear_coordinate_length_bound_km(start, end)
                / ground_speed_limit_kmh
                * 60,
            )
        durations.append(duration)
    return durations


def _linear_coordinate_length_bound_km(start: Coordinate, end: Coordinate) -> float:
    """Bound the speed of latitude/longitude-linear camera waypoint insertion.

    On the same sphere as coordinate_distance_km, ds/dt is bounded by
    R * hypot(delta_lat, max(cos(lat)) * delta_lon). A great-circle edge
    length alone can slightly underestimate the longest finite camera step.
    This changes capped clocks, not geometry or overlap requirements.
    """
    minimum_latitude, maximum_latitude = sorted((start.latitude, end.latitude))
    closest_to_equator = (
        0
        if minimum_latitude <= 0 <= maximum_latitude
        else min(abs(minimum_latitude), abs(maximum_latitude))
    )
    return 6371 * hypot(
        radians(end.latitude - start.latitude),
        cos(radians(closest_to_equator)) * radians(end.longitude - start.longitude),
    )


def ground_speed_kmh(
    start: Coordinate,
    end: Coordinate,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
) -> float:
    if wind is None or wind.speed_mps == 0:
        return cruise_speed_kmh
    wind_speed_kmh = wind.speed_mps * 3.6
    bearing = route_bearing_degrees(start, end)
    relative_angle = radians(bearing - wind.direction_degrees)
    headwind_speed = wind_speed_kmh * cos(relative_angle)
    crosswind_speed = wind_speed_kmh * sin(relative_angle)
    if abs(crosswind_speed) >= cruise_speed_kmh:
        raise WindPerformanceError(
            "БВС не может удерживать заданный путевой угол при указанном боковом ветре."
        )
    along_track_airspeed = sqrt(cruise_speed_kmh**2 - crosswind_speed**2)
    ground_speed = along_track_airspeed - headwind_speed
    if ground_speed <= 0:
        raise WindPerformanceError(
            "Встречный ветер не позволяет БВС продвигаться по заданному маршруту."
        )
    return ground_speed


def _limited_speed(speed_kmh: float, speed_limit_kmh: float | None) -> float:
    if speed_limit_kmh is None:
        return speed_kmh
    return min(speed_kmh, speed_limit_kmh)
