from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationMode, SweepAngleAlgorithm, SweepDirectionMode
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.domain.survey import SensorProfile as SensorProfile


class WorkSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1, max_length=64)
    distance_km: float = Field(gt=0, le=5_000)
    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    alternative_routes: list[list[Coordinate]] = Field(default_factory=list, max_length=8)
    coverage_area_id: str | None = Field(default=None, min_length=1, max_length=64)
    image_count: int | None = Field(default=None, ge=1)
    computed_gsd_cm: float | None = Field(default=None, gt=0)
    strip_spacing_m: float | None = Field(default=None, gt=0)
    survey_speed_limit_kmh: float | None = Field(default=None, gt=0)
    trigger_spacing_m: float | None = Field(default=None, gt=0)
    flight_direction_degrees: float | None = Field(default=None, ge=0, lt=180)
    survey_tracks: list[tuple[Coordinate, Coordinate]] | None = Field(
        default=None, min_length=1, max_length=5_000
    )
    coverage_pattern: Literal["contours", "shell", "split_shell"] | None = None


class CoverageArea(BaseModel):
    model_config = ConfigDict(frozen=True)

    area_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=100_000)
    interior_rings: list[list[Coordinate]] = Field(default_factory=list, max_length=1_000)


class SurveyParameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction_mode: SweepDirectionMode = SweepDirectionMode.AUTOMATIC
    flight_direction_degrees: float = Field(ge=0, lt=180)
    side_overlap_percent: float = Field(ge=0, lt=95)
    forward_overlap_percent: float = Field(ge=0, lt=95)
    maximum_gsd_cm: float = Field(gt=0, le=100)
    strip_width_m: float = Field(gt=0, le=5_000)
    maximum_segment_distance_km: float = Field(default=20, gt=0, le=500)
    sensor: SensorProfile


class CoveragePlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    area: CoverageArea
    mission_altitude_m: float = Field(gt=0, le=100_000)
    parameters: SurveyParameters
    exclusions: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    departure_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    landing_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    wind: WindCondition | None = None
    reference_airspeed_kmh: float | None = Field(default=None, gt=0, le=300)
    planning_area_count: int = Field(default=1, ge=1, le=1_000)
    optimization_mode: OptimizationMode = OptimizationMode.OPTIMIZED


class CoveragePlanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: list[WorkSegment] = Field(min_length=1, max_length=10_000)
    computed_gsd_cm: float = Field(gt=0)
    footprint_width_m: float = Field(gt=0)
    footprint_height_m: float = Field(gt=0)
    strip_spacing_m: float = Field(gt=0)
    trigger_spacing_m: float = Field(gt=0)
    selected_flight_direction_degrees: float = Field(ge=0, lt=180)
    angle_algorithm: SweepAngleAlgorithm
