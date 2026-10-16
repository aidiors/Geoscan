from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.coverage.models import CoveragePlanRequest
from geoplan.domain.enums import SweepAngleAlgorithm
from geoplan.planning.models import TurnRadiusScenario


class BenchmarkDrone(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str = Field(min_length=1, max_length=64)
    cruise_speed_kmh: float = Field(gt=0, le=300)


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=64)
    distance_km: float = Field(gt=0, le=5_000)
    transit_minutes_by_drone: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def transit_minutes_are_non_negative(self) -> BenchmarkTask:
        if any(value < 0 for value in self.transit_minutes_by_drone.values()):
            raise ValueError("Время транзита не может быть отрицательным.")
        return self


class OptimizationBenchmarkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    drones: list[BenchmarkDrone] = Field(min_length=2, max_length=20)
    tasks: list[BenchmarkTask] = Field(min_length=2, max_length=20)
    random_seed: int = Field(default=42, ge=0)


class BenchmarkAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    task_ids: list[str]
    duration_minutes: float = Field(ge=0)


class OptimizationBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: str
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    runtime_ms: float = Field(ge=0)
    assignments: list[BenchmarkAssignment]


class CoverageBenchmarkCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=256)
    request: CoveragePlanRequest
    partitioned_requests: list[CoveragePlanRequest] = Field(default_factory=list, max_length=100)


class CoverageBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    algorithm: SweepAngleAlgorithm
    selected_angle_degrees: float | None = Field(default=None, ge=0, lt=180)
    route_distance_km: float | None = Field(default=None, gt=0)
    turn_count: int | None = Field(default=None, ge=0)
    segment_count: int = Field(ge=0)
    runtime_ms: float = Field(ge=0)
    gap_to_reference_percent: float | None = Field(default=None, ge=0)
    geometry_valid: bool
    error_message: str | None = Field(default=None, min_length=1, max_length=512)


class SmallRadiusBenchmarkMethod(StrEnum):
    DUBINS = "dubins"
    HYBRID_ASTAR = "hybrid_astar"


class SmallRadiusBenchmarkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: list[CoverageBenchmarkCase] = Field(min_length=1, max_length=100)
    radii_m: list[float] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def radii_are_distinct_and_ascending(self) -> SmallRadiusBenchmarkRequest:
        if any(not isfinite(radius) or radius <= 0 or radius > 10_000 for radius in self.radii_m):
            raise ValueError("Радиусы benchmark должны быть в диапазоне (0, 10 000] м.")
        if self.radii_m != sorted(set(self.radii_m)):
            raise ValueError("Радиусы benchmark должны возрастать без повторений.")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Идентификаторы сценариев benchmark должны быть уникальны.")
        return self


class SmallRadiusBenchmarkCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    method: SmallRadiusBenchmarkMethod
    scenarios: list[TurnRadiusScenario] = Field(min_length=2, max_length=5)
    runtime_ms: float = Field(ge=0)


class SmallRadiusBenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_ids: list[str] = Field(min_length=1, max_length=100)
    radii_m: list[float] = Field(min_length=2, max_length=5)
    methods: list[SmallRadiusBenchmarkMethod] = Field(min_length=1, max_length=2)
    results: list[SmallRadiusBenchmarkCaseResult] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def contains_complete_result_matrix(self) -> SmallRadiusBenchmarkReport:
        expected = {(case_id, method) for case_id in self.case_ids for method in self.methods}
        observed = {(result.case_id, result.method) for result in self.results}
        if len(self.results) != len(expected) or observed != expected:
            raise ValueError("Отчёт benchmark должен содержать каждую пару сценария и метода.")
        if any(
            [scenario.radius_m for scenario in result.scenarios] != self.radii_m
            for result in self.results
        ):
            raise ValueError("Each experiment row must match the report radii.")
        return self


class UrbanObstacleOrderMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: Literal["original", "reordered_blocks"]
    radius_m: float = Field(gt=0, le=10_000)
    status: Literal["FEASIBLE", "NO_ROUTE_FOR_SELECTED_SWEEPS"]
    distance_km: float | None = Field(default=None, gt=0)
    uncovered_area_m2: float | None = Field(default=None, ge=0)
    selected_survey_order_changed: bool | None = None
    error: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def result_fields_match_status(self) -> UrbanObstacleOrderMeasurement:
        if self.status == "FEASIBLE":
            if self.distance_km is None or self.uncovered_area_m2 is None:
                raise ValueError("Для построенного маршрута нужны длина и площадь непокрытия.")
            if self.error is not None:
                raise ValueError("Для построенного маршрута ошибка должна отсутствовать.")
            return self
        if self.distance_km is not None or self.uncovered_area_m2 is not None:
            raise ValueError("Для отсутствующего маршрута нельзя указывать измерения.")
        return self


class UrbanObstacleOrderBenchmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    coverage_angle_degrees: float = Field(ge=0, lt=180)
    survey_leg_count: int = Field(ge=1, le=10_000)
    comparison: str = Field(min_length=1, max_length=256)
    planner: str = Field(min_length=1, max_length=256)
    results: list[UrbanObstacleOrderMeasurement] = Field(min_length=1, max_length=100)


class CoverageExperimentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(min_length=1, max_length=64)
    generated_at_utc: datetime
    scenario_ids: list[str] = Field(min_length=1, max_length=1_000)
    algorithms: list[SweepAngleAlgorithm] = Field(min_length=1, max_length=100)
    results: list[CoverageBenchmarkResult] = Field(min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def contains_complete_result_matrix(self) -> CoverageExperimentReport:
        expected = len(self.scenario_ids) * len(self.algorithms)
        if len(self.results) != expected:
            raise ValueError("The report must contain every scenario and algorithm pair.")
        pairs = {(result.case_id, result.algorithm) for result in self.results}
        expected_pairs = {
            (scenario_id, algorithm)
            for scenario_id in self.scenario_ids
            for algorithm in self.algorithms
        }
        if pairs != expected_pairs:
            raise ValueError("The report contains missing, duplicate, or unexpected result pairs.")
        return self


class CoverageExperimentArtifacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(min_length=1, max_length=64)
    directory: str = Field(min_length=1)
    csv_path: str = Field(min_length=1)
    json_path: str = Field(min_length=1)
    markdown_path: str = Field(min_length=1)
