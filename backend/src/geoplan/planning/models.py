from __future__ import annotations

from datetime import datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.coverage.models import CoverageArea, CoveragePlanRequest, SurveyParameters, WorkSegment
from geoplan.domain.airspace import AllowedAirspace, FlightObstacle, NoFlyZone
from geoplan.domain.diagnostics import FeasibilityReport, SafetyAuditReport
from geoplan.domain.enums import (
    AltitudeReference,
    AssignmentAlgorithm,
    AssignmentStrategy,
    CoverageAlgorithm,
    CoverageAngleCost,
    MissionTask,
    OptimalityStatus,
    OptimizationCriterion,
    OptimizationMode,
    OptimizationScope,
    RouteAlgorithm,
    TurnRadiusStatus,
    ValidityMode,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import DronePlan
from geoplan.domain.terrain import TerrainElevationSample, TerrainSource
from geoplan.scheduling.models import ScheduleSummary


class PlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drones: list[DroneSpec] = Field(min_length=1, max_length=100)
    segments: list[WorkSegment] = Field(default_factory=list, max_length=10_000)
    coverage_areas: list[CoverageArea] = Field(default_factory=list, max_length=1_000)
    survey_parameters: SurveyParameters | None = None
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    obstacles: list[FlightObstacle] = Field(default_factory=list, max_length=20_000)
    mission_altitude_m: float = Field(gt=0, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    terrain_elevation_samples: list[TerrainElevationSample] = Field(
        default_factory=list,
        max_length=100_000,
    )
    terrain_source: TerrainSource | None = None
    terrain_vertical_uncertainty_m: float = Field(default=0, ge=0, le=500)
    terrain_sampling_interval_m: float = Field(default=25, ge=5, le=1_000)
    terrain_max_interpolation_distance_m: float = Field(default=5_000, gt=0, le=100_000)
    task: MissionTask
    departure_site: FlightSite | None = None
    landing_site: FlightSite | None = None
    departure_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    landing_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    alternate_landing_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    allowed_airspaces: list[AllowedAirspace] = Field(default_factory=list, max_length=1_000)
    wind: WindCondition | None = None
    ambient_temperature_c: float | None = Field(default=None, ge=-100, le=100)
    maximum_active_drones: int | None = Field(default=None, gt=0, le=100)
    completion_deadline_minutes: float | None = Field(default=None, gt=0, le=10_080)
    minimum_separation_m: float = Field(default=50, gt=0, le=10_000)
    minimum_vertical_separation_m: float = Field(default=30, gt=0, le=1_000)
    vertical_deconfliction_step_m: float = Field(default=30, gt=0, le=500)
    maximum_vertical_deconfliction_m: float = Field(default=0, ge=0, le=1_000)
    position_uncertainty_m: float = Field(default=0, ge=0, le=1_000)
    start_time_uncertainty_seconds: float = Field(default=0, ge=0, le=300)
    speed_uncertainty_percent: float = Field(default=0, ge=0, le=50)
    flight_site_occupancy_seconds: float = Field(default=0, ge=0, le=600)
    turnaround_minutes: float = Field(default=0, ge=0, le=1_440)
    minimum_turn_radius_m: float = Field(default=0, ge=0, le=10_000)
    optimization_mode: OptimizationMode = OptimizationMode.OPTIMIZED
    assignment_strategy: AssignmentStrategy = AssignmentStrategy.AUTOMATIC
    productive_routing: bool = False
    planning_time_limit_seconds: float = Field(default=270, ge=1, le=270)
    planning_start_time: datetime | None = None
    planning_end_time: datetime | None = None

    @model_validator(mode="after")
    def has_route_source(self) -> PlanRequest:
        known_areas = {area.area_id for area in self.coverage_areas}
        if any(
            s.survey_track_area_ids is not None and set(s.area_ids) - known_areas
            for s in self.segments
        ):
            raise ValueError("A survey track refers to an unknown coverage area.")
        for label, identifiers in (
            ("БВС", [drone.drone_id for drone in self.drones]),
            ("областей", [area.area_id for area in self.coverage_areas]),
            ("участков", [segment.segment_id for segment in self.segments]),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Идентификаторы {label} должны быть уникальными.")
        if not self.segments and not self.coverage_areas:
            raise ValueError("Нужен хотя бы один участок или целевая зона покрытия.")
        if self.coverage_areas and self.survey_parameters is None:
            raise ValueError("Для целевой зоны нужны параметры съёмки.")
        if self.altitude_reference is AltitudeReference.AGL:
            if not self.terrain_elevation_samples and self.terrain_source is None:
                raise ValueError("Для высоты AGL нужны отметки рельефа.")
            if self.terrain_elevation_samples and self.terrain_source is not None:
                raise ValueError(
                    "Specify either legacy terrain samples or terrain_source, not both."
                )
            if any(
                drone.maximum_climb_rate_mps is None or drone.maximum_descent_rate_mps is None
                for drone in self.drones
                if self.task in drone.supported_tasks
            ):
                raise ValueError("Для высоты AGL задайте пределы набора и снижения каждого БВС.")
        elif any(
            airspace.altitude_reference is AltitudeReference.AGL
            for airspace in self.allowed_airspaces
        ) or any(
            zone.altitude_reference is AltitudeReference.AGL and zone.minimum_altitude_m is not None
            for zone in self.no_fly_zones
        ):
            raise ValueError(
                "Ограничения AGL требуют режима задания AGL и данных рельефа; "
                "direct comparison with MSL is not permitted."
            )
        if (
            self.minimum_turn_radius_m > 0 or any(d.minimum_turn_radius_m > 0 for d in self.drones)
        ) and self.segments:
            raise ValueError("Радиус разворота поддерживается для генерируемых галсов.")
        if self.maximum_active_drones is not None and self.maximum_active_drones > len(self.drones):
            raise ValueError("Ограничение числа БВС превышает размер доступного парка.")
        if (
            self.maximum_vertical_deconfliction_m > 0
            and self.vertical_deconfliction_step_m < self.minimum_vertical_separation_m
        ):
            raise ValueError(
                "Шаг вертикального эшелонирования должен быть не меньше "
                "минимального вертикального разделения."
            )
        if self.planning_start_time is not None and self.planning_start_time.utcoffset() is None:
            raise ValueError("Время начала плана должно содержать часовой пояс.")
        if self.planning_end_time is not None and self.planning_end_time.utcoffset() is None:
            raise ValueError("Время окончания плана должно содержать часовой пояс.")
        if self.planning_end_time is not None and self.planning_start_time is None:
            raise ValueError("Для окончания рабочего окна задайте начало плана.")
        if (
            self.planning_start_time is not None
            and self.planning_end_time is not None
            and self.planning_end_time <= self.planning_start_time
        ):
            raise ValueError("Окончание рабочего окна должно быть позже начала плана.")
        has_timed_airspace = any(
            airspace.validity is not None and airspace.validity.mode is ValidityMode.TIME_WINDOW
            for airspace in self.allowed_airspaces
        )
        if has_timed_airspace and self.planning_start_time is None:
            raise ValueError("Для временного воздушного пространства задайте начало плана.")
        has_timed_no_fly_zone = any(
            zone.validity is not None and zone.validity.mode is ValidityMode.TIME_WINDOW
            for zone in self.no_fly_zones
        )
        if has_timed_no_fly_zone and self.planning_start_time is None:
            raise ValueError("Для временной запретной зоны задайте начало плана.")
        if (
            self.criterion is OptimizationCriterion.BALANCED
            and self.completion_deadline_minutes is None
        ):
            raise ValueError("Для сбалансированного плана необходимо задать срок выполнения.")
        aerial_imaging_tasks = {
            MissionTask.RGB,
            MissionTask.MULTISPECTRAL,
            MissionTask.THERMAL,
        }
        if (
            self.task in aerial_imaging_tasks
            and self.survey_parameters is not None
            and self.survey_parameters.side_overlap_percent < 50
        ):
            raise ValueError("Для аэрофотосъёмки боковое перекрытие должно быть не менее 50%.")
        return self


class PlanMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    total_distance_km: float = Field(ge=0)
    survey_distance_km: float = Field(default=0, ge=0)
    transit_distance_km: float = Field(default=0, ge=0)
    useful_flight_percent: float = Field(default=0, ge=0, le=100)
    average_fleet_utilization_percent: float = Field(ge=0, le=100)
    active_drone_count: int = Field(default=0, ge=0)
    sortie_count: int = Field(default=0, ge=0)


class ProductiveRoutingSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    selected_variant: str
    evaluated_variants: int = Field(ge=0)
    freeform_candidates_checked: int = Field(default=0, ge=0)
    cross_area_variants_evaluated: int = Field(default=0, ge=0)
    cross_area_sortie_count: int = Field(default=0, ge=0)
    baseline_makespan_minutes: float | None = None
    baseline_total_flight_minutes: float | None = None
    baseline_transit_minutes: float | None = None
    pre_cross_objective: list[float] | None = None
    pre_cross_makespan_minutes: float | None = None
    pre_cross_total_flight_minutes: float | None = None
    cross_objective_variants_evaluated: int = Field(default=0, ge=0)
    message: str


class AssignmentSearchSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    solver_statuses: list[str] = Field(default_factory=list)
    runtime_seconds: float = Field(default=0, ge=0)
    integer_assignment_proven: bool = False
    candidate_audited: bool = False
    candidate_accepted: bool = False
    message: str


class OptimizationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: AssignmentAlgorithm
    coverage_algorithm: CoverageAlgorithm
    route_algorithm: RouteAlgorithm
    scope: OptimizationScope
    optimality: OptimalityStatus
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    active_drone_count: int = Field(gt=0)
    survey_turn_radius_m: float = Field(default=0, ge=0, le=10_000)
    turn_radii_by_drone: dict[str, float] = Field(default_factory=dict)
    coverage_angle_cost: CoverageAngleCost = CoverageAngleCost.GEOMETRIC_DISTANCE
    coverage_reference_airspeed_kmh: float | None = Field(default=None, gt=0, le=300)
    productive_routing: ProductiveRoutingSummary | None = None
    assignment_search: AssignmentSearchSummary | None = None


class PlanCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: str
    elapsed_seconds: float = Field(ge=0)
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    sortie_count: int = Field(ge=0)
    objective: list[float]


class PlanPipelineReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    status: str = "running"
    elapsed_seconds: float = Field(default=0, ge=0)
    candidate_events: int = Field(default=0, ge=0)
    audited_events: int = Field(default=0, ge=0)
    improvements: int = Field(default=0, ge=0)
    failed_check_codes: list[str] = Field(default_factory=list, max_length=32)
    error_code: str | None = None


class PlanSearchSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    elapsed_seconds: float = Field(ge=0)
    time_limit_seconds: float = Field(ge=1)
    request_fingerprint: str
    checkpoints: list[PlanCheckpoint] = Field(default_factory=list, max_length=64)
    pipelines_started: int = Field(default=0, ge=0)
    pipelines: list[PlanPipelineReport] = Field(default_factory=list, max_length=8)


class PlanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drone_plans: list[DronePlan]
    metrics: PlanMetrics
    feasibility: FeasibilityReport | None = None
    optimization: OptimizationSummary | None = None
    schedule: ScheduleSummary | None = None
    safety_audit: SafetyAuditReport | None = None
    search: PlanSearchSummary | None = None


class TurnRadiusAnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    coverage: CoveragePlanRequest
    allowed_airspaces: list[AllowedAirspace] = Field(default_factory=list, max_length=1_000)
    radii_m: list[float] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def radii_are_distinct_and_ascending(self) -> TurnRadiusAnalysisRequest:
        if any(not isfinite(radius) or radius < 0 or radius > 10_000 for radius in self.radii_m):
            raise ValueError("Радиусы должны быть в диапазоне [0, 10 000] м.")
        if self.radii_m != sorted(set(self.radii_m)):
            raise ValueError("Радиусы должны быть различными и перечислены по возрастанию.")
        return self


class TurnRadiusScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    radius_m: float = Field(ge=0, le=10_000)
    status: TurnRadiusStatus
    message: str = Field(min_length=1)
    distance_km: float | None = Field(default=None, gt=0)
    uncovered_area_m2: float | None = Field(default=None, ge=0)


class TurnRadiusAnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    area_id: str = Field(min_length=1)
    scenarios: list[TurnRadiusScenario] = Field(min_length=1, max_length=5)
    common_survey_route: list[Coordinate] | None = None
    message: str = Field(min_length=1)
