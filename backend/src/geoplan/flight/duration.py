from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from geoplan.domain.enums import ErrorCode
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.flight.models import MissionDurationEstimate, MissionDurationRequest
from geoplan.flight.performance import (
    WindPerformanceError,
    route_duration_minutes,
    route_leg_durations_minutes,
)
from geoplan.navigation.routing import route_distance_km


class MissionDurationCalculator(Protocol):
    def calculate(self, request: MissionDurationRequest) -> MissionDurationEstimate: ...


@dataclass(frozen=True)
class CruiseMissionDurationCalculator:
    def calculate(self, request: MissionDurationRequest) -> MissionDurationEstimate:
        if request.survey_route is None or request.survey_distance_km is None:
            duration = _route_duration_minutes(
                request.route,
                request.distance_km,
                request.cruise_speed_kmh,
                request.wind,
                request.speed_limit_kmh,
            )
            effective_speed = request.distance_km / duration * 60
            return MissionDurationEstimate(
                duration_minutes=round(duration, 2),
                effective_speed_kmh=round(effective_speed, 2),
            )

        transit_legs = _optional_leg_durations(
            request.transit_route,
            request.transit_distance_km,
            request.cruise_speed_kmh,
            request.wind,
        )
        survey_legs = _optional_leg_durations(
            request.survey_route,
            request.survey_distance_km,
            request.cruise_speed_kmh,
            request.wind,
            request.speed_limit_kmh,
        )
        if request.survey_leg_speed_limits_kmh is not None:
            survey_legs = [
                _optional_leg_durations(
                    [a, b], route_distance_km([a, b]), request.cruise_speed_kmh, request.wind, cap
                )[0]
                for (a, b), cap in zip(
                    pairwise(request.survey_route), request.survey_leg_speed_limits_kmh, strict=True
                )
            ]
        landing_legs = _optional_leg_durations(
            request.landing_route,
            request.landing_distance_km,
            request.cruise_speed_kmh,
            request.wind,
        )
        transit_duration, survey_duration, landing_duration = (
            sum(transit_legs),
            sum(survey_legs),
            sum(landing_legs),
        )
        duration = transit_duration + survey_duration + landing_duration
        effective_speed = request.distance_km / duration * 60
        return MissionDurationEstimate(
            duration_minutes=round(duration, 2),
            effective_speed_kmh=round(effective_speed, 2),
            transit_duration_minutes=round(transit_duration + landing_duration, 2),
            survey_duration_minutes=round(survey_duration, 2),
            landing_duration_minutes=round(landing_duration, 2),
            transit_leg_durations_minutes=transit_legs,
            survey_leg_durations_minutes=survey_legs,
            landing_leg_durations_minutes=landing_legs,
        )


def _optional_leg_durations(
    route: list[Coordinate] | None,
    distance_km: float | None,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
    speed_limit_kmh: float | None = None,
) -> list[float]:
    if route is None or distance_km is None or distance_km == 0:
        return [0.0] * (len(route) - 1) if route else []
    try:
        return route_leg_durations_minutes(
            route, distance_km, cruise_speed_kmh, wind, speed_limit_kmh
        )
    except WindPerformanceError as error:
        raise PlanningError(code=ErrorCode.WIND_LIMIT_EXCEEDED, message=str(error)) from error


def _route_duration_minutes(
    route: list[Coordinate],
    distance_km: float,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
    ground_speed_limit_kmh: float | None = None,
) -> float:
    try:
        return route_duration_minutes(
            route,
            distance_km,
            cruise_speed_kmh,
            wind,
            ground_speed_limit_kmh,
        )
    except WindPerformanceError as error:
        raise PlanningError(code=ErrorCode.WIND_LIMIT_EXCEEDED, message=str(error)) from error
