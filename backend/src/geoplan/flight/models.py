from __future__ import annotations

from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate


class MissionDurationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    distance_km: float = Field(gt=0)
    cruise_speed_kmh: float = Field(gt=0)
    route: list[Coordinate] = Field(min_length=2)
    wind: WindCondition | None = None
    speed_limit_kmh: float | None = Field(default=None, gt=0)
    transit_route: list[Coordinate] | None = Field(default=None, min_length=2)
    transit_distance_km: float | None = Field(default=None, ge=0)
    survey_route: list[Coordinate] | None = Field(default=None, min_length=2)
    survey_distance_km: float | None = Field(default=None, gt=0)
    landing_route: list[Coordinate] | None = Field(default=None, min_length=2)
    landing_distance_km: float | None = Field(default=None, ge=0)
    survey_leg_speed_limits_kmh: list[float | None] | None = None

    @model_validator(mode="after")
    def valid_survey_leg_limits(self) -> MissionDurationRequest:
        limits = self.survey_leg_speed_limits_kmh
        if limits is not None and (
            self.survey_route is None
            or len(limits) != len(self.survey_route) - 1
            or any(cap is not None and (not isfinite(cap) or cap <= 0) for cap in limits)
        ):
            raise ValueError("Survey speed limits must match the route's individual legs.")
        return self


class MissionDurationEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    duration_minutes: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    transit_duration_minutes: float = Field(default=0, ge=0)
    survey_duration_minutes: float = Field(default=0, ge=0)
    landing_duration_minutes: float = Field(default=0, ge=0)
    transit_leg_durations_minutes: list[float] | None = Field(default=None, exclude=True)
    survey_leg_durations_minutes: list[float] | None = Field(default=None, exclude=True)
    landing_leg_durations_minutes: list[float] | None = Field(default=None, exclude=True)

    @property
    def precise_duration_minutes(self) -> float:
        phases = (
            self.transit_leg_durations_minutes,
            self.survey_leg_durations_minutes,
            self.landing_leg_durations_minutes,
        )
        return (
            sum(sum(p) for p in phases if p is not None)
            if all(p is not None for p in phases)
            else self.duration_minutes
        )
