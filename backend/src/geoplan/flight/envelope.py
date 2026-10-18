"""Recover the airspeed implied by the flown ground track and horizontal clock."""

from dataclasses import dataclass
from itertools import pairwise
from math import cos, hypot, radians, sin

from geoplan.domain.environment import WindCondition
from geoplan.domain.mission import Mission
from geoplan.navigation.routing import coordinate_distance_km, route_bearing_degrees


@dataclass(frozen=True)
class HorizontalAirspeedEnvelope:
    def range_kmh(self, mission: Mission, wind: WindCondition | None) -> tuple[float, float] | None:
        speeds = []
        for stage in mission.stages:
            times = stage.leg_durations_minutes
            if times is None:
                if any(a != b for a, b in pairwise(stage.coordinates)):
                    return None
                continue
            for (start, end), minutes in zip(pairwise(stage.coordinates), times, strict=True):
                if start == end:
                    continue
                if minutes <= 0:
                    return None
                ground = coordinate_distance_km(start, end) / minutes * 60
                relative = radians(
                    route_bearing_degrees(start, end) - (wind.direction_degrees if wind else 0)
                )
                wind_speed = wind.speed_mps * 3.6 if wind else 0
                speeds.append(
                    hypot(ground + wind_speed * cos(relative), wind_speed * sin(relative))
                )
        return (min(speeds), max(speeds)) if speeds else None
