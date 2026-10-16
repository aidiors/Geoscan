from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.enums import ScheduleAlgorithm, ScheduleConflictType
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission


class ScheduleTimeWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_offset_minutes: float = Field(ge=0)
    end_offset_minutes: float = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> ScheduleTimeWindow:
        if self.end_offset_minutes <= self.start_offset_minutes:
            raise ValueError("Окончание окна должно быть позже начала.")
        return self


class VerticalLayerOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    offset_m: float = Field(ge=0, le=1_000)
    climb_duration_minutes: float = Field(default=0, ge=0)
    descent_duration_minutes: float = Field(default=0, ge=0)
    extra_energy_equivalent_minutes: float = Field(ge=0)
    extra_battery_percent: float = Field(ge=0, le=100)


class MissionScheduleRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[Mission] = Field(min_length=1, max_length=10_000)
    minimum_separation_m: float = Field(gt=0, le=10_000)
    minimum_vertical_separation_m: float = Field(default=30, gt=0, le=1_000)
    position_uncertainty_m: float = Field(default=0, ge=0, le=1_000)
    start_time_uncertainty_seconds: float = Field(default=0, ge=0, le=300)
    speed_uncertainty_percent: float = Field(default=0, ge=0, le=50)
    flight_site_occupancy_seconds: float = Field(default=0, ge=0, le=600)
    turnaround_minutes: float = Field(ge=0, le=1_440)
    turnaround_minutes_by_drone: dict[str, float] = Field(default_factory=dict)
    delay_step_seconds: float = Field(default=5, gt=0, le=300)
    planning_start_time: datetime | None = None
    time_windows_by_segment_id: dict[str, list[ScheduleTimeWindow]] = Field(default_factory=dict)
    vertical_layers_by_segment_id: dict[str, list[VerticalLayerOption]] = Field(
        default_factory=dict
    )


class ResolvedScheduleConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    conflict_type: ScheduleConflictType
    delayed_drone_id: str = Field(min_length=1, max_length=64)
    delayed_segment_id: str = Field(min_length=1, max_length=64)
    conflicting_drone_id: str = Field(min_length=1, max_length=64)
    conflicting_segment_id: str = Field(min_length=1, max_length=64)
    coordinate: Coordinate
    conflict_offset_minutes: float = Field(ge=0)
    nominal_distance_m: float = Field(ge=0)
    required_distance_m: float = Field(gt=0)
    applied_delay_minutes: float = Field(ge=0)
    applied_vertical_offset_m: float = Field(default=0, ge=0, le=1_000)


class MissionScheduleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[Mission] = Field(min_length=1, max_length=10_000)
    makespan_minutes: float = Field(gt=0)
    resolved_conflict_count: int = Field(ge=0)
    resolved_conflicts: list[ResolvedScheduleConflict] = Field(
        default_factory=list, max_length=10_000
    )
    algorithm: ScheduleAlgorithm


class ScheduleSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: ScheduleAlgorithm
    makespan_minutes: float = Field(gt=0)
    minimum_separation_m: float = Field(gt=0)
    minimum_vertical_separation_m: float = Field(default=30, gt=0)
    vertically_deconflicted_mission_count: int = Field(default=0, ge=0)
    position_uncertainty_m: float = Field(ge=0)
    start_time_uncertainty_seconds: float = Field(ge=0)
    speed_uncertainty_percent: float = Field(ge=0)
    flight_site_occupancy_seconds: float = Field(ge=0)
    turnaround_minutes: float = Field(ge=0)
    resolved_conflict_count: int = Field(ge=0)
    resolved_conflicts: list[ResolvedScheduleConflict] = Field(
        default_factory=list, max_length=10_000
    )
