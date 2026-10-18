from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.enums import AltitudeReference, MissionStageType
from geoplan.domain.fleet import AutopilotProfile, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.survey import SensorProfile
from geoplan.domain.terrain import VerticalProfilePoint


class Mission(BaseModel):
    model_config = ConfigDict(frozen=True)
    coverage_area_ids: list[str] = Field(default_factory=list, max_length=1_000)

    drone_id: str
    segment_id: str
    duration_minutes: float = Field(gt=0)
    distance_km: float = Field(gt=0)
    route: list[Coordinate] = Field(min_length=2)
    altitude_m: float = Field(gt=0)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    vertical_profile: list[VerticalProfilePoint] = Field(default_factory=list, max_length=100_000)
    minimum_agl_m: float | None = Field(default=None, gt=0)
    minimum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    maximum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    maximum_required_climb_rate_mps: float | None = Field(default=None, ge=0, le=100)
    maximum_required_descent_rate_mps: float | None = Field(default=None, ge=0, le=100)
    vertical_distance_m: float = Field(default=0, ge=0)
    vertical_energy_equivalent_minutes: float = Field(default=0, ge=0)
    vertical_climb_duration_minutes: float = Field(default=0, ge=0)
    vertical_descent_duration_minutes: float = Field(default=0, ge=0)
    terrain_vertical_uncertainty_m: float = Field(default=0, ge=0, le=500)
    vertical_deconfliction_offset_m: float = Field(default=0, ge=0, le=1_000)
    cruise_speed_kmh: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    estimated_battery_percent: float = Field(gt=0)
    starting_battery_percent: float = Field(default=100, gt=0, le=100)
    remaining_battery_percent: float | None = Field(default=None, ge=0, le=100)
    battery_reserve_margin_percent: float | None = None
    minimum_alternate_margin_percent: float | None = None
    critical_alternate_site_id: str | None = Field(default=None, min_length=1, max_length=64)
    critical_alternate_route: list[Coordinate] = Field(default_factory=list, max_length=100_000)
    critical_alternate_start_offset_minutes: float | None = Field(default=None, ge=0)
    critical_alternate_duration_minutes: float | None = Field(default=None, ge=0)
    alternate_check_spacing_m: float | None = Field(default=None, gt=0)
    alternate_evaluated_checkpoint_count: int | None = Field(default=None, gt=0)
    alternate_certification_error_minutes: float | None = Field(default=None, ge=0)
    survey_distance_km: float | None = Field(default=None, ge=0)
    transit_distance_km: float | None = Field(default=None, ge=0)
    survey_duration_minutes: float | None = Field(default=None, ge=0)
    transit_duration_minutes: float | None = Field(default=None, ge=0)
    useful_flight_percent: float | None = Field(default=None, ge=0, le=100)
    image_count: int | None = Field(default=None, ge=1)
    sensor: SensorProfile | None = None
    camera_trigger_spacing_m: float | None = Field(default=None, gt=0)
    minimum_turn_radius_m: float = Field(default=0, ge=0, le=10_000)
    computed_gsd_cm: float | None = Field(default=None, gt=0)
    strip_spacing_m: float | None = Field(default=None, gt=0)
    flight_direction_degrees: float | None = Field(default=None, ge=0, lt=180)
    departure_site: FlightSite | None = None
    landing_site: FlightSite | None = None
    start_offset_minutes: float | None = Field(default=None, ge=0)
    end_offset_minutes: float | None = Field(default=None, gt=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    stages: list[MissionStage] = Field(default_factory=list)
    photo_capture_route_indices: list[int] | None = Field(
        default=None, min_length=1, max_length=4_000
    )

    @property
    def route_leg_durations_minutes(self) -> list[float] | None:
        """Validate a complete explicit horizontal clock; None denotes legacy input."""
        if not any(s.leg_durations_minutes is not None for s in self.stages):
            return None
        points, durations = [self.route[0]], []
        for stage in self.stages:
            if stage.coordinates[0] != points[-1]:
                raise ValueError("Explicit stages must form a continuous route.")
            times = stage.leg_durations_minutes
            if times is None:
                if any(a != b for a, b in pairwise(stage.coordinates)):
                    raise ValueError("All moving stages require explicit leg times.")
                times = [0.0] * (len(stage.coordinates) - 1)
            vertical = (
                self.vertical_climb_duration_minutes
                if stage.stage_type is MissionStageType.TAKEOFF
                else self.vertical_descent_duration_minutes
                if stage.stage_type is MissionStageType.LANDING
                else 0.0
            )
            if abs(stage.duration_minutes - sum(times) - vertical) > 1e-7:
                raise ValueError("Stage duration must match its legs and vertical time.")
            for point, duration in zip(stage.coordinates[1:], times, strict=True):
                if point != points[-1]:
                    points.append(point)
                    durations.append(duration)
                elif duration > 0:
                    raise ValueError(
                        "Stationary dwell must be represented separately from route edges."
                    )
        horizontal = (
            self.duration_minutes
            - self.vertical_climb_duration_minutes
            - self.vertical_descent_duration_minutes
        )
        if points != self.route or abs(sum(durations) - horizontal) > 1e-7:
            raise ValueError(
                "Explicit clock must cover the complete horizontal route and duration."
            )
        return durations

    @model_validator(mode="after")
    def schedule_offsets_are_complete(self) -> Mission:
        _ = self.route_leg_durations_minutes
        captures = self.photo_capture_route_indices
        if captures is not None:
            if (
                captures != sorted(set(captures))
                or captures[0] < 0
                or captures[-1] >= len(self.route)
            ):
                raise ValueError("Photo captures require distinct ordered route indices.")
            if self.image_count != len(captures) or self.route_leg_durations_minutes is None:
                raise ValueError("Photo captures require an exact clock and matching image count.")
        if (
            self.vertical_climb_duration_minutes + self.vertical_descent_duration_minutes
            >= self.duration_minutes
        ):
            raise ValueError(
                "Суммарная длительность вертикальных фаз должна быть меньше длительности миссии."
            )
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("Абсолютные начало и окончание миссии должны быть заданы вместе.")
        if self.start_offset_minutes is None and self.end_offset_minutes is None:
            if self.start_time is not None:
                raise ValueError("Абсолютное расписание требует относительных смещений.")
            return self
        if self.start_offset_minutes is None or self.end_offset_minutes is None:
            raise ValueError("Начало и окончание миссии должны быть заданы вместе.")
        if self.end_offset_minutes <= self.start_offset_minutes:
            raise ValueError("Окончание миссии должно быть позже начала.")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("Абсолютное окончание миссии должно быть позже начала.")
        return self


class MissionStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_type: MissionStageType
    coordinates: list[Coordinate] = Field(min_length=1)
    duration_minutes: float = Field(default=0, ge=0)
    leg_durations_minutes: list[float] | None = Field(default=None, max_length=100_000)

    @model_validator(mode="after")
    def leg_times_match_geometry(self) -> MissionStage:
        if self.leg_durations_minutes is None:
            return self
        if len(self.leg_durations_minutes) != len(self.coordinates) - 1:
            raise ValueError("Each stage edge requires one duration.")
        if any(not isfinite(t) or t < 0 for t in self.leg_durations_minutes):
            raise ValueError("Leg durations must be finite and nonnegative.")
        if any(
            t == 0 and (a.latitude, a.longitude) != (b.latitude, b.longitude)
            for a, b, t in zip(
                self.coordinates, self.coordinates[1:], self.leg_durations_minutes, strict=False
            )
        ):
            raise ValueError("A moving leg must have positive duration.")
        if sum(self.leg_durations_minutes) > self.duration_minutes + 1e-7:
            raise ValueError("Leg durations exceed their stage duration.")
        return self


class DronePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    mission_count: int = Field(ge=0)
    total_duration_minutes: float = Field(ge=0)
    missions: list[Mission]
    autopilot_profile: AutopilotProfile = Field(default_factory=AutopilotProfile)
    maintenance: MaintenanceReport | None = None


class MaintenanceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    projected_flights: int = Field(ge=0)
    projected_flight_hours: float = Field(ge=0)
    remaining_flights: int | None = Field(default=None, ge=0)
    remaining_hours: float | None = Field(default=None, ge=0)
    maintenance_due_after_plan: bool
    message: str = Field(min_length=1, max_length=256)
