"""Bounded fixed-wing climb and descent along existing horizontal transits.

Rates are operating upper bounds. All outbound/return time remains horizontal
flight time: neither phase adds a stationary dwell or an invented orbit.
"""

from dataclasses import dataclass
from itertools import accumulate, pairwise
from math import hypot

from geoplan.domain.airspace import AllowedAirspace
from geoplan.domain.enums import AltitudeReference, ErrorCode
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.flight_phases import ForwardFlightPhases
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.energy import _battery_use_percent
from geoplan.flight.trajectory import mission_route_clock
from geoplan.navigation.routing import coordinate_distance_km, route_is_inside_allowed_airspaces


@dataclass(frozen=True)
class FixedWingPhasePlanner:
    def distance_km(
        self, route: list[Coordinate], altitudes: list[float], ground: float, uncertainty: float
    ) -> float:
        # Bound the vertical slope using the lowest possible site elevation.
        height = max(altitudes) - ground
        scale = (height + uncertainty) / height
        return sum(
            hypot(coordinate_distance_km(a, b), (z2 - z1) * scale / 1000)
            for (a, b), (z1, z2) in zip(pairwise(route), pairwise(altitudes), strict=True)
        )

    def plan(
        self, drone: DroneSpec, height_m: float, departure_minutes: float, landing_minutes: float
    ) -> ForwardFlightPhases | None:
        profile = drone.fixed_wing_phase_profile
        if profile is None:
            return None
        if min(departure_minutes, landing_minutes) <= 0:
            raise PlanningError(
                code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
                message="Самолёту нужен перелёт до первого галса и от последнего галса "
                "для набора и снижения.",
            )
        climb = height_m / (departure_minutes * 60)
        descent = height_m / (landing_minutes * 60)
        if climb > profile.climb_rate_mps + 1e-9 or descent > profile.descent_rate_mps + 1e-9:
            raise PlanningError(
                code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
                message="Длины перелёта не хватает для заданных пределов набора или снижения "
                "самолёта. Перенесите площадку или начало съёмки; "
                "спираль не добавляется автоматически.",
            )
        if (
            drone.maximum_cruise_speed_kmh is not None
            and max(
                hypot(drone.cruise_speed_kmh, climb * 3.6),
                hypot(drone.cruise_speed_kmh, descent * 3.6),
            )
            > drone.maximum_cruise_speed_kmh + 0.05
        ):
            raise PlanningError(
                code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
                message="Полная воздушная скорость самолёта при наборе/снижении превышает предел.",
            )
        return ForwardFlightPhases(
            climb_minutes=departure_minutes,
            descent_minutes=landing_minutes,
            maximum_climb_rate_mps=climb,
            maximum_descent_rate_mps=descent,
            energy_surcharge_minutes=(
                departure_minutes * (profile.climb_energy_multiplier - 1)
                + landing_minutes * (profile.descent_energy_multiplier - 1)
            ),
        )

    def altitudes(
        self,
        departure: list[Coordinate],
        survey: list[Coordinate],
        landing: list[Coordinate],
        departure_times: list[float],
        landing_times: list[float],
        ground: float,
        altitude: float,
    ) -> list[float]:
        up, down = sum(departure_times), sum(landing_times)
        if up <= 0 or down <= 0:
            raise ValueError("Самолётному профилю нужны ненулевые времена перелётов.")
        arrays = [
            [
                ground + (altitude - ground) * t / up
                for t in accumulate(departure_times, initial=0.0)
            ],
            [altitude] * len(survey),
            [
                altitude - (altitude - ground) * t / down
                for t in accumulate(landing_times, initial=0.0)
            ],
        ]
        arrays[0][0], arrays[0][-1] = ground, altitude
        arrays[2][0], arrays[2][-1] = altitude, ground
        points: list[Coordinate] = []
        values: list[float] = []
        for route, heights in zip((departure, survey, landing), arrays, strict=True):
            for point, height in zip(route, heights, strict=True):
                if points and point == points[-1]:
                    if abs(values[-1] - height) > 1e-6:
                        raise ValueError("Разрыв высоты на границе фаз.")
                    continue
                points.append(point)
                values.append(height)
        return values


def forward_route_inside_airspaces(
    route: list[Coordinate],
    altitudes: list[float],
    ground: float,
    uncertainty: float,
    spaces: list[AllowedAirspace],
) -> bool:
    if not spaces:
        return True
    ceiling = max(altitudes)
    for (a, b), (z1, z2) in zip(pairwise(route), pairwise(altitudes), strict=True):
        compatible = []
        for space in spaces:
            lower, upper = min(z1, z2), max(z1, z2)
            if space.altitude_reference is AltitudeReference.AGL:
                lower, upper = max(0, lower - ground - uncertainty), upper - ground + uncertainty
            else:
                lower = max(ground - uncertainty, lower - uncertainty)
                upper = min(ceiling, upper + uncertainty)
            if space.minimum_altitude_m <= lower and upper <= space.maximum_altitude_m:
                compatible.append(space)
        if not compatible or not route_is_inside_allowed_airspaces([a, b], compatible):
            return False
    return True


def forward_flight_margins(drone: DroneSpec, mission: Mission) -> list[float]:
    phases = mission.forward_flight_phases
    if drone.fixed_wing_phase_profile is None:
        return [-1] if phases is not None else []
    if phases is None or mission.route_altitudes_msl_m is None:
        return [-1]
    try:
        if mission.route_leg_durations_minutes is None:
            return [-1]
        outbound = mission.stages[1]
        inbound = mission.stages[-1]
        expected = FixedWingPhasePlanner().plan(
            drone, mission.height_agl.maximum_m, outbound.duration_minutes, inbound.duration_minutes
        )
        assert expected is not None
        offsets = mission_route_clock(mission).offsets_minutes
        nominal_height = mission.altitude_m - mission.flat_ground_elevation_m
        heights = [
            mission.flat_ground_elevation_m + nominal_height * t / expected.climb_minutes
            if t <= expected.climb_minutes
            else mission.flat_ground_elevation_m
            + nominal_height * (mission.duration_minutes - t) / expected.descent_minutes
            if t >= mission.duration_minutes - expected.descent_minutes
            else mission.altitude_m
            for t in offsets
        ]
        return [
            0.001
            - abs(
                mission.distance_km
                - FixedWingPhasePlanner().distance_km(
                    mission.route,
                    mission.route_altitudes_msl_m,
                    mission.flat_ground_elevation_m,
                    mission.terrain_vertical_uncertainty_m,
                )
            ),
            1e-6
            - max(abs(a - b) for a, b in zip(heights, mission.route_altitudes_msl_m, strict=True)),
            1e-6 - abs(phases.climb_minutes - expected.climb_minutes),
            1e-6 - abs(phases.descent_minutes - expected.descent_minutes),
            1e-6 - abs(phases.maximum_climb_rate_mps - expected.maximum_climb_rate_mps),
            1e-6 - abs(phases.maximum_descent_rate_mps - expected.maximum_descent_rate_mps),
            1e-6 - abs(phases.energy_surcharge_minutes - expected.energy_surcharge_minutes),
            mission.vertical_energy_equivalent_minutes - expected.energy_surcharge_minutes + 1e-6,
            mission.estimated_battery_percent
            - _battery_use_percent(
                mission.duration_minutes + mission.vertical_energy_equivalent_minutes, drone
            )
            + 0.02,
        ]
    except PlanningError, ValueError, IndexError:
        return [-1]
