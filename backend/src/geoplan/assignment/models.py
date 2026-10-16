from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.enums import (
    AssignmentAlgorithm,
    OptimalityStatus,
    OptimizationCriterion,
    OptimizationMode,
)
from geoplan.domain.mission import Mission


class MissionAssignmentCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1, max_length=64)
    drone_id: str = Field(min_length=1, max_length=64)
    mission: Mission


class AssignmentOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drone_ids: list[str] = Field(min_length=1, max_length=100)
    candidate_groups: list[list[MissionAssignmentCandidate]] = Field(
        min_length=1,
        max_length=10_000,
    )
    maximum_active_drones: int | None = Field(default=None, gt=0, le=100)
    completion_deadline_minutes: float | None = Field(default=None, gt=0, le=10_080)
    optimization_mode: OptimizationMode = OptimizationMode.OPTIMIZED
    turnaround_minutes_by_drone: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def candidate_groups_match_problem(self) -> AssignmentOptimizationRequest:
        known_drones = set(self.drone_ids)
        if len(known_drones) != len(self.drone_ids):
            raise ValueError("Идентификаторы БВС в задаче распределения должны быть уникальны.")
        if any(not group for group in self.candidate_groups):
            raise ValueError("Для каждого участка нужен хотя бы один допустимый кандидат.")
        if any(
            candidate.drone_id not in known_drones
            for group in self.candidate_groups
            for candidate in group
        ):
            raise ValueError("Кандидат ссылается на неизвестный БВС.")
        if self.maximum_active_drones is not None and self.maximum_active_drones > len(
            known_drones
        ):
            raise ValueError("Лимит активных БВС превышает размер парка.")
        if any(
            drone_id not in known_drones or minutes < 0 or minutes > 1_440
            for drone_id, minutes in self.turnaround_minutes_by_drone.items()
        ):
            raise ValueError("Время между вылетами задано неверно.")
        return self


class AssignmentOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assignments: list[MissionAssignmentCandidate] = Field(min_length=1, max_length=10_000)
    algorithm: AssignmentAlgorithm
    optimality: OptimalityStatus
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    active_drone_count: int = Field(gt=0)
