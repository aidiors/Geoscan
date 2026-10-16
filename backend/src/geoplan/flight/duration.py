from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from geoplan.domain.enums import ErrorCode
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.flight.models import MissionDurationEstimate, MissionDurationRequest
from geoplan.flight.performance import WindPerformanceError, route_duration_minutes


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

        transit_duration = _optional_route_duration_minutes(
            request.transit_route,
            request.transit_distance_km,
            request.cruise_speed_kmh,
            request.wind,
        )
        survey_duration = _route_duration_minutes(
            request.survey_route,
            request.survey_distance_km,
            request.cruise_speed_kmh,
            request.wind,
            request.speed_limit_kmh,
        )
        landing_duration = _optional_route_duration_minutes(
            request.landing_route,
            request.landing_distance_km,
            request.cruise_speed_kmh,
            request.wind,
        )
        duration = transit_duration + survey_duration + landing_duration
        effective_speed = request.distance_km / duration * 60
        return MissionDurationEstimate(
            duration_minutes=round(duration, 2),
            effective_speed_kmh=round(effective_speed, 2),
            transit_duration_minutes=round(transit_duration + landing_duration, 2),
            survey_duration_minutes=round(survey_duration, 2),
            landing_duration_minutes=round(landing_duration, 2),
        )


def _optional_route_duration_minutes(
    route: list[Coordinate] | None,
    distance_km: float | None,
    cruise_speed_kmh: float,
    wind: WindCondition | None,
) -> float:
    if route is None or distance_km is None or distance_km == 0:
        return 0
    return _route_duration_minutes(route, distance_km, cruise_speed_kmh, wind)


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
