from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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


class MissionDurationEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    duration_minutes: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    transit_duration_minutes: float = Field(default=0, ge=0)
    survey_duration_minutes: float = Field(default=0, ge=0)
    landing_duration_minutes: float = Field(default=0, ge=0)
