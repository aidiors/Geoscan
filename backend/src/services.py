from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from math import ceil, cos, hypot, radians
from statistics import median
from typing import Protocol

from pydantic import ValidationError

from src.assignment import AssignmentOptimizer
from src.coverage import CoveragePlanner, CoveragePlanningError
from src.curvature import SweepTransitionPlanner, build_curvature_aware_sweep_route_with_planner
from src.flight_performance import WindPerformanceError, route_duration_minutes
from src.geometry import projection_for_coordinates
from src.optimizer import RouteOptimizer
from src.routing import (
    RouteGeometryError,
    build_safe_route,
    calculate_route_multiplier,
    coordinate_distance_km,
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_allowed_airspaces,
    route_is_inside_coverage_area,
)
from src.safety_audit import PlanSafetyAuditor
from src.scheduler import MissionScheduler, ScheduleWindowUnavailableError
from src.schemas import (
    AllowedAirspace,
    AltitudeReference,
    AssignmentOptimizationRequest,
    Coordinate,
    CoverageAngleCost,
    CoverageArea,
    CoveragePlanRequest,
    DronePlan,
    DroneSpec,
    ErrorCode,
    FeasibilityRecommendation,
    FeasibilityReport,
    FeasibilityStatus,
    FlightObstacle,
    FlightSite,
    InlineTerrainSource,
    MaintenanceReport,
    Mission,
    MissionAssignmentCandidate,
    MissionDurationEstimate,
    MissionDurationRequest,
    MissionScheduleRequest,
    MissionStage,
    MissionStageType,
    MissionTask,
    NoFlyZone,
    OptimizationScope,
    OptimizationSummary,
    PlanMetrics,
    PlanRequest,
    PlanResponse,
    RecommendationCode,
    RecommendationConfidence,
    RestrictionValidity,
    RouteAlgorithm,
    RouteOptimizationRequest,
    RouteOptimizationResult,
    ScheduleSummary,
    ScheduleTimeWindow,
    SweepDirectionMode,
    SweepRouteCurvatureRequest,
    ValidityMode,
    VerticalLayerOption,
    VerticalProfilePoint,
    WindCondition,
    WorkSegment,
)
from src.terrain import (
    InvalidTerrainSourceError,
    TerrainDataUnavailableError,
    VerticalProfilePlanner,
    VerticalProfileResult,
    VerticalRateExceededError,
)
from src.vertical_deconfliction import apply_vertical_layer

GEOMETRY_CLEARANCE_M = 0.5
ALTERNATE_CERTIFICATION_TOLERANCE_MINUTES = 0.1
ALTERNATE_MINIMUM_INTERVAL_M = 1.0
ALTERNATE_MAXIMUM_REFINEMENT_DEPTH = 20


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


@dataclass(frozen=True)
class MissionCandidate:
    drone: DroneSpec
    mission: Mission


@dataclass(frozen=True)
class OptimizedMissionRoute:
    survey_route: list[Coordinate]
    transit_route: list[Coordinate]
    landing_route: list[Coordinate]
    departure_site: FlightSite | None
    landing_site: FlightSite | None
    survey_distance_km: float
    transit_distance_km: float
    total_distance_km: float
    duration_estimate: MissionDurationEstimate
    emergency_reachability: EmergencyReachability | None
    vertical_profile: VerticalProfileResult | None


@dataclass(frozen=True)
class EmergencyReachability:
    minimum_margin_percent: float
    critical_site_id: str
    critical_route: list[Coordinate]
    critical_checkpoint_elapsed_minutes: float
    critical_diversion_minutes: float
    certification_error_minutes: float
    maximum_interval_m: float
    evaluated_checkpoint_count: int


@dataclass(frozen=True)
class FlightPhase:
    route: list[Coordinate]
    distance_km: float
    ground_speed_limit_kmh: float | None


@dataclass(frozen=True)
class EmergencyCheckpoint:
    coordinate: Coordinate
    elapsed_minutes: float


@dataclass(frozen=True)
class EvaluatedEmergencyCheckpoint:
    checkpoint: EmergencyCheckpoint
    site: FlightSite
    diversion_minutes: float
    diversion_route: list[Coordinate]

    @property
    def required_minutes(self) -> float:
        return self.checkpoint.elapsed_minutes + self.diversion_minutes


@dataclass(frozen=True)
class EmergencyIntervalCertificate:
    maximum_required_minutes: float
    maximum_interval_m: float


class PlanningError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        recommendations: list[FeasibilityRecommendation] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.recommendations = list(recommendations or [])
        super().__init__(message)


def build_plan(
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    coverage_planner: CoveragePlanner,
    assignment_optimizer: AssignmentOptimizer,
    mission_scheduler: MissionScheduler,
    sweep_transition_planner: SweepTransitionPlanner,
    safety_auditor: PlanSafetyAuditor,
    vertical_profile_planner: VerticalProfilePlanner,
) -> PlanResponse:
    capable_drones = _drones_capable_of_task(request.drones, request.task)
    envelope_capable_drones = _drones_capable_of_environment(capable_drones, request)
    weather_capable_drones = _drones_capable_of_wind(envelope_capable_drones, request.wind)
    eligible_drones = _drones_with_eligible_sites(weather_capable_drones, request)
    coverage_reference_airspeed_kmh = _coverage_reference_airspeed_kmh(
        request,
        eligible_drones,
    )
    segments = _resolve_segments(
        request,
        coverage_planner,
        sweep_transition_planner,
        coverage_reference_airspeed_kmh,
    )
    candidate_groups = _create_candidate_groups(
        segments,
        eligible_drones,
        request,
        duration_calculator,
        route_optimizer,
        sweep_transition_planner,
        vertical_profile_planner,
    )
    optimization_request = AssignmentOptimizationRequest(
        criterion=request.criterion,
        drone_ids=[drone.drone_id for drone in eligible_drones],
        candidate_groups=candidate_groups,
        maximum_active_drones=request.maximum_active_drones,
        completion_deadline_minutes=request.completion_deadline_minutes,
        optimization_mode=request.optimization_mode,
        turnaround_minutes_by_drone={
            drone.drone_id: max(request.turnaround_minutes, drone.minimum_turnaround_minutes)
            for drone in eligible_drones
        },
    )
    try:
        optimized = assignment_optimizer.optimize(optimization_request)
    except ValueError as error:
        current_limit = request.maximum_active_drones or len(eligible_drones)
        required_limit = min(len(eligible_drones), current_limit + 1)
        if required_limit > current_limit:
            assignment_recommendations = [
                FeasibilityRecommendation(
                    code=RecommendationCode.INCREASE_ACTIVE_DRONE_LIMIT,
                    message=(
                        f"Разрешите использовать не менее {required_limit} БВС "
                        "или сократите число одновременно планируемых участков."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="maximum_active_drones",
                    current_value=current_limit,
                    required_value=required_limit,
                    unit="drones",
                )
            ]
        else:
            assignment_recommendations = [
                FeasibilityRecommendation(
                    code=RecommendationCode.REDUCE_MISSION_SCOPE,
                    message="Разделите набор участков на несколько последовательных планов.",
                    confidence=RecommendationConfidence.ESTIMATE,
                    affected_ids=[segment.segment_id for segment in segments],
                )
            ]
        raise PlanningError(
            code=ErrorCode.UNPLANNABLE_SEGMENT,
            message="Ограничение числа БВС не позволяет распределить все участки.",
            recommendations=assignment_recommendations,
        ) from error
    missions = [selected.mission for selected in optimized.assignments]
    vertical_layers = _vertical_layers_by_segment_id(missions, request)
    schedule_request = MissionScheduleRequest(
        missions=missions,
        minimum_separation_m=request.minimum_separation_m,
        minimum_vertical_separation_m=request.minimum_vertical_separation_m,
        position_uncertainty_m=request.position_uncertainty_m,
        start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
        speed_uncertainty_percent=request.speed_uncertainty_percent,
        flight_site_occupancy_seconds=request.flight_site_occupancy_seconds,
        turnaround_minutes=request.turnaround_minutes,
        turnaround_minutes_by_drone={
            drone.drone_id: drone.minimum_turnaround_minutes for drone in request.drones
        },
        planning_start_time=request.planning_start_time,
        time_windows_by_segment_id=_schedule_time_windows(request, missions),
        vertical_layers_by_segment_id=vertical_layers,
    )
    try:
        schedule = mission_scheduler.schedule(schedule_request)
    except ScheduleWindowUnavailableError as error:
        failed_mission = next(
            mission for mission in missions if mission.segment_id == error.segment_id
        )
        recommendations = [
            FeasibilityRecommendation(
                code=RecommendationCode.RESCHEDULE_OPERATION,
                message="Сдвиньте операцию к следующему периоду действия разрешений.",
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="planning_start_time",
                affected_ids=[error.segment_id],
            )
        ]
        if request.planning_start_time is not None and request.planning_end_time is not None:
            operating_minutes = (
                request.planning_end_time - request.planning_start_time
            ).total_seconds() / 60
            recommendations.append(
                FeasibilityRecommendation(
                    code=RecommendationCode.EXTEND_OPERATING_WINDOW,
                    message=(
                        "Увеличьте рабочее окно как минимум до длительности вылета "
                        f"{failed_mission.duration_minutes:.1f} мин и учтите ожидание ограничений."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="planning_end_time",
                    current_value=round(operating_minutes, 2),
                    required_value=failed_mission.duration_minutes,
                    unit="minutes",
                    affected_ids=[error.segment_id],
                )
            )
        raise PlanningError(
            code=ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE,
            message=(
                f"Вылет участка {error.segment_id} не помещается в допустимое "
                "4D-окно. Сдвиньте начало или окончание работ либо уточните "
                "периоды ограничений."
            ),
            recommendations=recommendations,
        ) from error
    except RuntimeError as error:
        raise PlanningError(
            code=ErrorCode.SCHEDULE_GENERATION_FAILED,
            message="Безопасное расписание вылетов построить не удалось.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.RESCHEDULE_OPERATION,
                    message=(
                        "Увеличьте временной разнос вылетов или перенесите часть миссий "
                        "в другое рабочее окно."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="planning_start_time",
                    affected_ids=[mission.segment_id for mission in missions],
                )
            ],
        ) from error
    assignments = {drone.drone_id: [] for drone in request.drones}
    for mission in schedule.missions:
        assignments[mission.drone_id].append(mission)
    drone_plans = _create_drone_plans(request.drones, assignments)
    metrics = _create_metrics(drone_plans, len(request.drones))
    return PlanResponse(
        criterion=request.criterion,
        drone_plans=drone_plans,
        metrics=metrics,
        feasibility=_create_feasibility_report(
            metrics,
            request,
            len(eligible_drones),
        ),
        optimization=OptimizationSummary(
            algorithm=optimized.algorithm,
            coverage_algorithm=coverage_planner.coverage_algorithm,
            route_algorithm=RouteAlgorithm.BIDIRECTIONAL_VISIBILITY_GRAPH,
            scope=OptimizationScope.GENERATED_ROUTE_ASSIGNMENT,
            optimality=optimized.optimality,
            completion_minutes=optimized.completion_minutes,
            total_flight_minutes=optimized.total_flight_minutes,
            active_drone_count=optimized.active_drone_count,
            survey_turn_radius_m=request.minimum_turn_radius_m,
            coverage_angle_cost=_coverage_angle_cost(request),
            coverage_reference_airspeed_kmh=coverage_reference_airspeed_kmh,
        ),
        schedule=ScheduleSummary(
            algorithm=schedule.algorithm,
            makespan_minutes=schedule.makespan_minutes,
            minimum_separation_m=request.minimum_separation_m,
            minimum_vertical_separation_m=request.minimum_vertical_separation_m,
            vertically_deconflicted_mission_count=sum(
                mission.vertical_deconfliction_offset_m > 0 for mission in schedule.missions
            ),
            position_uncertainty_m=request.position_uncertainty_m,
            start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
            speed_uncertainty_percent=request.speed_uncertainty_percent,
            flight_site_occupancy_seconds=request.flight_site_occupancy_seconds,
            turnaround_minutes=request.turnaround_minutes,
            resolved_conflict_count=schedule.resolved_conflict_count,
            resolved_conflicts=schedule.resolved_conflicts,
        ),
        safety_audit=safety_auditor.audit(request, segments, schedule.missions),
    )


def _vertical_layers_by_segment_id(
    missions: list[Mission],
    request: PlanRequest,
) -> dict[str, list[VerticalLayerOption]]:
    drones = {drone.drone_id: drone for drone in request.drones}
    return {
        mission.segment_id: _vertical_layer_options(mission, drones[mission.drone_id], request)
        for mission in missions
    }


def _vertical_layer_options(
    mission: Mission,
    drone: DroneSpec,
    request: PlanRequest,
) -> list[VerticalLayerOption]:
    baseline = VerticalLayerOption(
        offset_m=0,
        extra_energy_equivalent_minutes=0,
        extra_battery_percent=0,
    )
    climb_rate = drone.maximum_climb_rate_mps
    descent_rate = drone.maximum_descent_rate_mps
    if (
        request.maximum_vertical_deconfliction_m <= 0
        or climb_rate is None
        or descent_rate is None
        or _has_altitude_dependent_temporal_restrictions(request)
    ):
        return [baseline]
    options = [baseline]
    offset = request.vertical_deconfliction_step_m
    while offset <= request.maximum_vertical_deconfliction_m + 1e-9:
        climb_minutes = offset / climb_rate / 60
        descent_minutes = offset / descent_rate / 60
        extra_minutes = climb_minutes + descent_minutes
        extra_battery = extra_minutes / drone.maximum_flight_minutes * 100
        option = VerticalLayerOption(
            offset_m=round(offset, 3),
            climb_duration_minutes=round(climb_minutes, 6),
            descent_duration_minutes=round(descent_minutes, 6),
            extra_energy_equivalent_minutes=round(extra_minutes, 6),
            extra_battery_percent=round(extra_battery, 6),
        )
        shifted = apply_vertical_layer(mission, option)
        if _vertical_layer_is_feasible(shifted, option, request):
            options.append(option)
        offset += request.vertical_deconfliction_step_m
    return options


def _has_altitude_dependent_temporal_restrictions(request: PlanRequest) -> bool:
    timed_airspace = any(
        airspace.validity is not None and airspace.validity.mode is ValidityMode.TIME_WINDOW
        for airspace in request.allowed_airspaces
    )
    selective_no_fly = any(
        zone.minimum_altitude_m is not None
        or zone.maximum_altitude_m is not None
        or (zone.validity is not None and zone.validity.mode is ValidityMode.TIME_WINDOW)
        for zone in request.no_fly_zones
    )
    return timed_airspace or selective_no_fly


def _vertical_layer_is_feasible(
    mission: Mission,
    option: VerticalLayerOption,
    request: PlanRequest,
) -> bool:
    drone = next(drone for drone in request.drones if drone.drone_id == mission.drone_id)
    maximum_altitude = drone.maximum_altitude_for(mission.altitude_reference)
    if maximum_altitude is not None and mission.altitude_m > maximum_altitude:
        return False
    if (
        mission.battery_reserve_margin_percent is None
        or mission.battery_reserve_margin_percent < option.extra_battery_percent
    ):
        return False
    if (
        mission.minimum_alternate_margin_percent is not None
        and mission.minimum_alternate_margin_percent < option.extra_battery_percent
    ):
        return False
    return not request.allowed_airspaces or _mission_route_inside_airspaces(
        mission,
        request.allowed_airspaces,
    )


def _schedule_time_windows(
    request: PlanRequest,
    missions: list[Mission],
) -> dict[str, list[ScheduleTimeWindow]]:
    if request.planning_start_time is None:
        return {}
    has_temporal_constraint = request.planning_end_time is not None or any(
        item.validity is not None and item.validity.mode is ValidityMode.TIME_WINDOW
        for item in [*request.allowed_airspaces, *request.no_fly_zones]
    )
    if not has_temporal_constraint:
        return {}
    horizon_minutes = _planning_horizon_minutes(request, missions)
    windows: dict[str, list[ScheduleTimeWindow]] = {}
    for mission in missions:
        mission_windows = _mission_time_windows(mission, request, horizon_minutes)
        if mission_windows is not None:
            windows[mission.segment_id] = mission_windows
    return windows


def _mission_time_windows(
    mission: Mission,
    request: PlanRequest,
    horizon_minutes: float,
) -> list[ScheduleTimeWindow] | None:
    compatible_airspaces = [
        airspace
        for airspace in request.allowed_airspaces
        if any(
            airspace.minimum_altitude_m <= altitude <= airspace.maximum_altitude_m
            for altitude in _mission_altitudes(mission, airspace.altitude_reference)
        )
    ]
    relevant_no_fly_zones = [
        zone
        for zone in request.no_fly_zones
        if _zone_applies_to_mission(zone, mission)
        and not route_is_clear_of_no_fly_zones(mission.route, [zone])
    ]
    timed_items = [
        item
        for item in [*compatible_airspaces, *relevant_no_fly_zones]
        if item.validity is not None and item.validity.mode is ValidityMode.TIME_WINDOW
    ]
    if not timed_items and request.planning_end_time is None:
        return None
    boundaries = {0.0, horizon_minutes}
    for item in timed_items:
        validity = item.validity
        if validity is None:
            continue
        interval = _validity_interval(validity, request)
        if interval is not None:
            boundaries.update(interval)
    legal: list[ScheduleTimeWindow] = []
    for start, end in pairwise(sorted(boundaries)):
        if end <= start or start >= horizon_minutes:
            continue
        clipped_end = min(end, horizon_minutes)
        midpoint = (start + clipped_end) / 2
        if _mission_is_legal_at_offset(
            mission,
            compatible_airspaces,
            relevant_no_fly_zones,
            midpoint,
            request,
        ):
            _append_or_merge_window(legal, start, clipped_end)
    return legal


def _mission_altitudes_msl(mission: Mission) -> list[float]:
    if mission.vertical_profile:
        return [point.target_altitude_msl for point in mission.vertical_profile]
    return [mission.altitude_m]


def _mission_altitudes(mission: Mission, reference: AltitudeReference) -> list[float]:
    if reference is AltitudeReference.MSL:
        return _mission_altitudes_msl(mission)
    if mission.vertical_profile:
        return [point.target_agl_m for point in mission.vertical_profile]
    if mission.altitude_reference is AltitudeReference.AGL:
        return [mission.altitude_m]
    return []


def _zone_applies_to_mission(zone: NoFlyZone, mission: Mission) -> bool:
    if zone.minimum_altitude_m is None or zone.maximum_altitude_m is None:
        return True
    altitudes = _mission_altitudes(mission, zone.altitude_reference)
    return any(
        zone.minimum_altitude_m <= altitude <= zone.maximum_altitude_m for altitude in altitudes
    )


def _planning_horizon_minutes(request: PlanRequest, missions: list[Mission]) -> float:
    planning_start = request.planning_start_time
    if planning_start is None:
        return 0
    if request.planning_end_time is not None:
        return (request.planning_end_time - planning_start).total_seconds() / 60
    latest_boundary = max(
        (
            (item.validity.valid_to - planning_start).total_seconds() / 60
            for item in [*request.allowed_airspaces, *request.no_fly_zones]
            if item.validity is not None
            and item.validity.mode is ValidityMode.TIME_WINDOW
            and item.validity.valid_to is not None
        ),
        default=0.0,
    )
    serial_workload = sum(mission.duration_minutes for mission in missions)
    turnarounds = request.turnaround_minutes * max(0, len(missions) - 1)
    return max(1.0, latest_boundary + serial_workload + turnarounds + 60)


def _validity_interval(
    validity: RestrictionValidity,
    request: PlanRequest,
) -> tuple[float, float] | None:
    planning_start = request.planning_start_time
    if validity.valid_from is None or validity.valid_to is None or planning_start is None:
        return None
    start = max(0.0, (validity.valid_from - planning_start).total_seconds() / 60)
    end = (validity.valid_to - planning_start).total_seconds() / 60
    return (start, end) if end > start else None


def _mission_is_legal_at_offset(
    mission: Mission,
    airspaces: list[AllowedAirspace],
    no_fly_zones: list[NoFlyZone],
    offset_minutes: float,
    request: PlanRequest,
) -> bool:
    planning_start = request.planning_start_time
    if planning_start is None:
        return True
    at_time = planning_start + timedelta(minutes=offset_minutes)
    active_airspaces = [
        airspace for airspace in airspaces if _validity_is_active(airspace.validity, at_time)
    ]
    if request.allowed_airspaces and (
        not active_airspaces or not _mission_route_inside_airspaces(mission, active_airspaces)
    ):
        return False
    return not any(_validity_is_active(zone.validity, at_time) for zone in no_fly_zones)


def _mission_route_inside_airspaces(
    mission: Mission,
    airspaces: list[AllowedAirspace],
) -> bool:
    if not mission.vertical_profile:
        compatible = [
            airspace
            for airspace in airspaces
            if any(
                airspace.minimum_altitude_m <= altitude <= airspace.maximum_altitude_m
                for altitude in _mission_altitudes(mission, airspace.altitude_reference)
            )
        ]
        return bool(compatible) and route_is_inside_allowed_airspaces(mission.route, compatible)
    for start, end in pairwise(mission.vertical_profile):
        compatible = [
            airspace
            for airspace in airspaces
            if airspace.minimum_altitude_m <= _profile_altitude(start, airspace.altitude_reference)
            and _profile_altitude(start, airspace.altitude_reference) <= airspace.maximum_altitude_m
            and airspace.minimum_altitude_m
            <= _profile_altitude(end, airspace.altitude_reference)
            <= airspace.maximum_altitude_m
        ]
        if not compatible or not route_is_inside_allowed_airspaces(
            [start.coordinate, end.coordinate],
            compatible,
        ):
            return False
    return True


def _profile_altitude(
    point: VerticalProfilePoint,
    reference: AltitudeReference,
) -> float:
    return point.target_agl_m if reference is AltitudeReference.AGL else point.target_altitude_msl


def _validity_is_active(
    validity: RestrictionValidity | None,
    at_time: datetime,
) -> bool:
    if validity is None or validity.mode is ValidityMode.PERMANENT:
        return True
    if validity.valid_from is None or validity.valid_to is None:
        return False
    return validity.valid_from <= at_time <= validity.valid_to


def _append_or_merge_window(
    windows: list[ScheduleTimeWindow],
    start: float,
    end: float,
) -> None:
    if windows and abs(windows[-1].end_offset_minutes - start) <= 1e-9:
        windows[-1] = windows[-1].model_copy(update={"end_offset_minutes": end})
        return
    windows.append(
        ScheduleTimeWindow(
            start_offset_minutes=start,
            end_offset_minutes=end,
        )
    )


def _create_candidate_groups(
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
) -> list[list[MissionAssignmentCandidate]]:
    groups: list[list[MissionAssignmentCandidate]] = []
    for segment in segments:
        constraints = _constraints_for_segment(segment, request)
        candidates: list[MissionCandidate] = []
        evaluated_candidates: list[MissionCandidate] = []
        errors: list[PlanningError] = []
        for drone in drones:
            candidate, error = _candidate_evaluation(
                segment,
                drone,
                request,
                duration_calculator,
                route_optimizer,
                transition_planner,
                vertical_profile_planner,
                constraints,
            )
            if candidate is not None:
                evaluated_candidates.append(candidate)
                if _candidate_is_feasible(candidate):
                    candidates.append(candidate)
            if error is not None:
                errors.append(error)
        if not candidates:
            if evaluated_candidates:
                raise _battery_endurance_error(segment, evaluated_candidates)
            if errors:
                raise errors[0]
            raise PlanningError(
                code=ErrorCode.UNPLANNABLE_SEGMENT,
                message=(
                    f"Участок {segment.segment_id} не помещается "
                    "в безопасную продолжительность полёта."
                ),
            )
        groups.append(
            [
                MissionAssignmentCandidate(
                    segment_id=segment.segment_id,
                    drone_id=candidate.drone.drone_id,
                    mission=candidate.mission,
                )
                for candidate in candidates
            ]
        )
    return groups


def _candidate_evaluation(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
    constraints: list[NoFlyZone],
) -> tuple[MissionCandidate | None, PlanningError | None]:
    try:
        candidate = _create_candidate(
            segment,
            drone,
            request,
            duration_calculator,
            route_optimizer,
            transition_planner,
            vertical_profile_planner,
            constraints,
        )
    except PlanningError as error:
        return None, error
    return candidate, None


def _resolve_segments(
    request: PlanRequest,
    coverage_planner: CoveragePlanner,
    sweep_transition_planner: SweepTransitionPlanner,
    coverage_reference_airspeed_kmh: float | None,
) -> list[WorkSegment]:
    if not request.coverage_areas:
        return request.segments
    parameters = request.survey_parameters
    if parameters is None:
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message="Для построения галсов отсутствуют параметры съёмки.",
        )
    generated: list[WorkSegment] = []
    for area in request.coverage_areas:
        plan_request = CoveragePlanRequest(
            area=area,
            mission_altitude_m=request.mission_altitude_m,
            parameters=parameters,
            exclusions=request.no_fly_zones,
            departure_candidates=[site.coordinate for site in _configured_departure_sites(request)],
            landing_candidates=[
                site.coordinate for site in _configured_primary_landing_sites(request)
            ],
            wind=request.wind,
            reference_airspeed_kmh=coverage_reference_airspeed_kmh,
        )
        try:
            result = coverage_planner.plan(plan_request)
        except CoveragePlanningError as error:
            raise PlanningError(
                code=error.code,
                message=error.message,
                recommendations=_coverage_failure_recommendations(error.code, plan_request),
            ) from error
        if request.minimum_turn_radius_m == 0:
            generated.extend(result.segments)
            continue
        generated.extend(
            _curvature_constrained_segment(segment, area, request, sweep_transition_planner)
            for segment in result.segments
        )
    return [*request.segments, *generated]


def _coverage_failure_recommendations(
    code: ErrorCode,
    request: CoveragePlanRequest,
) -> list[FeasibilityRecommendation]:
    if code is ErrorCode.GSD_REQUIREMENT_UNSATISFIED:
        sensor = request.parameters.sensor
        maximum_altitude_m = (
            request.parameters.maximum_gsd_cm
            / 100
            * sensor.image_width_px
            * sensor.focal_length_mm
            / sensor.sensor_width_mm
        )
        return [
            FeasibilityRecommendation(
                code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                message=(
                    f"Снизьте высоту задания до {maximum_altitude_m:.1f} м или ниже, "
                    "чтобы выполнить требование GSD для выбранной камеры."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="mission_altitude_m",
                current_value=request.mission_altitude_m,
                required_value=round(maximum_altitude_m, 2),
                unit="m",
                affected_ids=[request.area.area_id],
            )
        ]
    return [
        FeasibilityRecommendation(
            code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
            message=(
                "Проверьте пересечение рабочей области и запретов либо разделите "
                "область на независимые доступные части."
            ),
            confidence=RecommendationConfidence.ESTIMATE,
            parameter="coverage_areas/no_fly_zones",
            affected_ids=[request.area.area_id],
        )
    ]


def _coverage_reference_airspeed_kmh(
    request: PlanRequest,
    drones: list[DroneSpec],
) -> float | None:
    if _coverage_angle_cost(request) is CoverageAngleCost.GEOMETRIC_DISTANCE:
        return None
    return float(median(drone.cruise_speed_kmh for drone in drones))


def _coverage_angle_cost(request: PlanRequest) -> CoverageAngleCost:
    if (
        request.coverage_areas
        and request.survey_parameters is not None
        and request.survey_parameters.direction_mode is SweepDirectionMode.AUTOMATIC
        and request.wind is not None
        and request.wind.speed_mps > 0
    ):
        return CoverageAngleCost.WIND_ADJUSTED_DURATION
    return CoverageAngleCost.GEOMETRIC_DISTANCE


def _curvature_constrained_segment(
    segment: WorkSegment,
    area: CoverageArea,
    request: PlanRequest,
    transition_planner: SweepTransitionPlanner,
) -> WorkSegment:
    constraints = _constraints_for_segment(segment, request)
    curved_routes: list[list[Coordinate]] = []
    for route in [segment.route, *segment.alternative_routes]:
        if len(route) % 2 != 0:
            continue
        try:
            curvature_request = SweepRouteCurvatureRequest(
                route=route,
                coverage_area=area,
                no_fly_zones=constraints,
                minimum_turn_radius_m=request.minimum_turn_radius_m,
                sampling_step_m=max(3, min(25, request.minimum_turn_radius_m / 4)),
            )
        except ValidationError as error:
            raise PlanningError(
                code=ErrorCode.TURN_RADIUS_UNSATISFIED,
                message=f"Участок {segment.segment_id} не подходит для проверки радиуса.",
            ) from error
        try:
            curved = build_curvature_aware_sweep_route_with_planner(
                curvature_request,
                transition_planner,
            )
        except RouteGeometryError, ValidationError:
            continue
        if len(curved.route) <= 10_000:
            curved_routes.append(curved.route)
    if not curved_routes:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=(
                f"Для участка {segment.segment_id} не построены безопасные галсы. "
                f"Радиус: {request.minimum_turn_radius_m:g} м."
            ),
        )
    payload = segment.model_dump()
    payload["route"] = curved_routes[0]
    payload["alternative_routes"] = curved_routes[1:]
    payload["distance_km"] = route_distance_km(curved_routes[0])
    try:
        return WorkSegment.model_validate(payload)
    except ValidationError as error:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=f"Криволинейный маршрут участка {segment.segment_id} слишком длинный.",
        ) from error


def _drones_capable_of_task(
    drones: list[DroneSpec],
    task: MissionTask,
) -> list[DroneSpec]:
    capable_drones = [
        drone
        for drone in drones
        if task in drone.supported_tasks
        and (
            not drone.payloads or any(task in payload.supported_tasks for payload in drone.payloads)
        )
    ]
    if capable_drones:
        return capable_drones
    raise PlanningError(
        code=ErrorCode.NO_CAPABLE_DRONE,
        message="Для выбранного типа работ нет подходящего БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ADD_CAPABLE_DRONE,
                message=f"Добавьте или включите БВС, поддерживающий задачу {task.value}.",
                confidence=RecommendationConfidence.EXACT,
                parameter="supported_tasks",
                affected_ids=[task.value],
            )
        ],
    )


def _drones_capable_of_environment(
    drones: list[DroneSpec],
    request: PlanRequest,
) -> list[DroneSpec]:
    capable = [
        drone
        for drone in drones
        if _drone_supports_altitude(drone, request)
        and (
            request.ambient_temperature_c is None
            or drone.minimum_operating_temperature_c is None
            or request.ambient_temperature_c >= drone.minimum_operating_temperature_c
        )
        and (
            request.ambient_temperature_c is None
            or drone.maximum_operating_temperature_c is None
            or request.ambient_temperature_c <= drone.maximum_operating_temperature_c
        )
    ]
    if capable:
        return capable
    temperature = request.ambient_temperature_c
    if temperature is not None and all(
        (
            drone.minimum_operating_temperature_c is not None
            and temperature < drone.minimum_operating_temperature_c
        )
        or (
            drone.maximum_operating_temperature_c is not None
            and temperature > drone.maximum_operating_temperature_c
        )
        for drone in drones
    ):
        raise PlanningError(
            code=ErrorCode.TEMPERATURE_LIMIT_EXCEEDED,
            message="Температура находится вне подтверждённого диапазона доступных БВС.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.RESCHEDULE_OPERATION,
                    message="Перенесите вылет на период допустимой температуры.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="ambient_temperature_c",
                    current_value=temperature,
                    unit="celsius",
                    affected_ids=[drone.drone_id for drone in drones],
                )
            ],
        )
    raise PlanningError(
        code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
        message="Высота задания находится вне подтверждённого диапазона доступных БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                message="Измените высоту задания или добавьте БВС подходящего диапазона.",
                confidence=RecommendationConfidence.EXACT,
                parameter="mission_altitude_m",
                current_value=request.mission_altitude_m,
                unit="m",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _drone_supports_altitude(drone: DroneSpec, request: PlanRequest) -> bool:
    minimum = drone.minimum_altitude_for(request.altitude_reference)
    maximum = drone.maximum_altitude_for(request.altitude_reference)
    return (minimum is None or request.mission_altitude_m >= minimum) and (
        maximum is None or request.mission_altitude_m <= maximum
    )


def _drones_capable_of_wind(
    drones: list[DroneSpec],
    wind: WindCondition | None,
) -> list[DroneSpec]:
    if wind is None:
        return drones
    capable_drones = [drone for drone in drones if wind.speed_mps <= drone.maximum_wind_mps]
    if capable_drones:
        return capable_drones
    raise PlanningError(
        code=ErrorCode.WIND_LIMIT_EXCEEDED,
        message="Скорость ветра превышает допустимое значение для доступных БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.WAIT_FOR_LOWER_WIND,
                message=(
                    "Перенесите вылет до снижения ветра не более чем до "
                    f"{max(drone.maximum_wind_mps for drone in drones):g} m/s."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="wind.speed_mps",
                current_value=wind.speed_mps,
                required_value=max(drone.maximum_wind_mps for drone in drones),
                unit="m/s",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _drones_with_eligible_sites(
    drones: list[DroneSpec],
    request: PlanRequest,
) -> list[DroneSpec]:
    departure_sites = _configured_departure_sites(request)
    landing_sites = _configured_primary_landing_sites(request)
    alternate_sites = request.alternate_landing_sites
    eligible = [
        drone
        for drone in drones
        if _has_eligible_site(departure_sites, drone.drone_id)
        and _has_eligible_site(landing_sites, drone.drone_id)
        and _has_eligible_site(alternate_sites, drone.drone_id)
    ]
    if eligible:
        return eligible
    raise PlanningError(
        code=ErrorCode.NO_ELIGIBLE_FLIGHT_SITE,
        message="Для доступных БВС нет назначенной пары площадок взлёта и посадки.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ASSIGN_FLIGHT_SITE,
                message=(
                    "Назначьте хотя бы одну доступную площадку взлёта и основную "
                    "площадку посадки одному и тому же БВС."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="departure_sites/landing_sites",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _has_eligible_site(sites: list[FlightSite], drone_id: str) -> bool:
    if not sites:
        return True
    return any(_site_supports_drone(site, drone_id) for site in sites)


def _create_candidate(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
    constraints: list[NoFlyZone],
) -> MissionCandidate:
    optimized = _best_route_and_sites(
        segment,
        drone,
        request,
        route_optimizer,
        transition_planner,
        constraints,
        duration_calculator,
        vertical_profile_planner,
    )
    full_route = _combine_routes(
        optimized.transit_route,
        optimized.survey_route,
        optimized.landing_route,
    )
    _validate_geometry(full_route, optimized.survey_route, constraints, segment, request)
    estimate = optimized.duration_estimate
    _validate_route_envelope(drone, optimized, full_route, segment.segment_id)
    vertical_profile = optimized.vertical_profile
    energy_equivalent_minutes = (
        vertical_profile.vertical_energy_equivalent_minutes if vertical_profile is not None else 0
    )
    battery_percent = round(
        _battery_use_percent(estimate.duration_minutes + energy_equivalent_minutes, drone),
        2,
    )
    remaining_battery_percent = round(max(0, drone.initial_charge_percent - battery_percent), 2)
    reserve_margin_percent = round(
        remaining_battery_percent - drone.battery_reserve_percent,
        2,
    )
    emergency = optimized.emergency_reachability
    mission = Mission(
        drone_id=drone.drone_id,
        segment_id=segment.segment_id,
        duration_minutes=estimate.duration_minutes,
        distance_km=round(optimized.total_distance_km, 3),
        route=full_route,
        altitude_m=request.mission_altitude_m,
        altitude_reference=request.altitude_reference,
        vertical_profile=(vertical_profile.points if vertical_profile is not None else []),
        minimum_agl_m=(vertical_profile.minimum_agl_m if vertical_profile is not None else None),
        minimum_terrain_elevation_m=(
            vertical_profile.minimum_terrain_elevation_m if vertical_profile is not None else None
        ),
        maximum_terrain_elevation_m=(
            vertical_profile.maximum_terrain_elevation_m if vertical_profile is not None else None
        ),
        maximum_required_climb_rate_mps=(
            vertical_profile.maximum_required_climb_rate_mps
            if vertical_profile is not None
            else None
        ),
        maximum_required_descent_rate_mps=(
            vertical_profile.maximum_required_descent_rate_mps
            if vertical_profile is not None
            else None
        ),
        vertical_distance_m=(
            round(vertical_profile.vertical_distance_m, 2) if vertical_profile is not None else 0
        ),
        vertical_energy_equivalent_minutes=round(energy_equivalent_minutes, 3),
        terrain_vertical_uncertainty_m=(
            request.terrain_vertical_uncertainty_m if vertical_profile is not None else 0
        ),
        cruise_speed_kmh=drone.cruise_speed_kmh,
        effective_speed_kmh=estimate.effective_speed_kmh,
        estimated_battery_percent=battery_percent,
        starting_battery_percent=drone.initial_charge_percent,
        remaining_battery_percent=remaining_battery_percent,
        battery_reserve_margin_percent=reserve_margin_percent,
        minimum_alternate_margin_percent=(
            round(emergency.minimum_margin_percent, 2) if emergency is not None else None
        ),
        critical_alternate_site_id=(emergency.critical_site_id if emergency is not None else None),
        critical_alternate_route=(emergency.critical_route if emergency is not None else []),
        critical_alternate_start_offset_minutes=(
            emergency.critical_checkpoint_elapsed_minutes if emergency is not None else None
        ),
        critical_alternate_duration_minutes=(
            emergency.critical_diversion_minutes if emergency is not None else None
        ),
        alternate_check_spacing_m=(
            round(emergency.maximum_interval_m, 3) if emergency is not None else None
        ),
        alternate_evaluated_checkpoint_count=(
            emergency.evaluated_checkpoint_count if emergency is not None else None
        ),
        alternate_certification_error_minutes=(
            round(emergency.certification_error_minutes, 6) if emergency is not None else None
        ),
        survey_distance_km=round(optimized.survey_distance_km, 3),
        transit_distance_km=round(optimized.transit_distance_km, 3),
        survey_duration_minutes=estimate.survey_duration_minutes,
        transit_duration_minutes=estimate.transit_duration_minutes,
        useful_flight_percent=round(
            optimized.survey_distance_km / optimized.total_distance_km * 100,
            2,
        ),
        image_count=segment.image_count,
        computed_gsd_cm=segment.computed_gsd_cm,
        strip_spacing_m=segment.strip_spacing_m,
        flight_direction_degrees=segment.flight_direction_degrees,
        departure_site=optimized.departure_site,
        landing_site=optimized.landing_site,
        stages=[
            MissionStage(
                stage_type=MissionStageType.TAKEOFF,
                coordinates=[optimized.transit_route[0]],
            ),
            MissionStage(
                stage_type=MissionStageType.TRANSIT,
                coordinates=optimized.transit_route,
                duration_minutes=max(
                    0,
                    estimate.transit_duration_minutes - estimate.landing_duration_minutes,
                ),
            ),
            MissionStage(
                stage_type=MissionStageType.SURVEY,
                coordinates=optimized.survey_route,
                duration_minutes=estimate.survey_duration_minutes,
            ),
            MissionStage(
                stage_type=MissionStageType.LANDING,
                coordinates=optimized.landing_route,
                duration_minutes=estimate.landing_duration_minutes,
            ),
        ],
    )
    return MissionCandidate(drone=drone, mission=mission)


def _best_route_and_sites(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    constraints: list[NoFlyZone],
    duration_calculator: MissionDurationCalculator,
    vertical_profile_planner: VerticalProfilePlanner,
) -> OptimizedMissionRoute:
    departure_sites = _eligible_departure_sites(request, drone.drone_id)
    landing_sites = _eligible_landing_sites(request, drone.drone_id)
    choices: list[OptimizedMissionRoute] = []
    errors: list[PlanningError] = []
    for departure_site in departure_sites:
        departure = departure_site.coordinate if departure_site is not None else segment.route[0]
        optimization_request = RouteOptimizationRequest(
            route=segment.route,
            alternative_routes=segment.alternative_routes,
            departure=departure,
            landings=[site.coordinate for site in landing_sites if site is not None],
            no_fly_zones=constraints,
        )
        try:
            optimized = _optimize_route(
                route_optimizer,
                optimization_request,
                segment.segment_id,
            )
        except PlanningError as error:
            errors.append(error)
            continue
        if request.minimum_turn_radius_m > 0 and not _preserves_curved_route(
            optimized.route,
            segment,
        ):
            errors.append(
                PlanningError(
                    code=ErrorCode.TURN_RADIUS_UNSATISFIED,
                    message=f"Маршрут участка {segment.segment_id} изменён после проверки радиуса.",
                )
            )
            continue
        try:
            transit_route = _departure_route(
                departure,
                optimized.route,
                segment,
                request,
                constraints,
                transition_planner,
            )
        except PlanningError as error:
            errors.append(error)
            continue
        for landing_site in landing_sites:
            landing = landing_site.coordinate if landing_site is not None else optimized.route[-1]
            try:
                landing_route = _landing_route(
                    optimized.route,
                    landing,
                    segment,
                    request,
                    constraints,
                    transition_planner,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            survey_distance = _survey_distance(segment, optimized)
            departure_distance = route_distance_km(transit_route)
            landing_distance = route_distance_km(landing_route)
            transit_distance = departure_distance + landing_distance
            total_distance = transit_distance + survey_distance
            full_route = _combine_routes(transit_route, optimized.route, landing_route)
            try:
                duration_estimate = duration_calculator.calculate(
                    MissionDurationRequest(
                        distance_km=total_distance,
                        cruise_speed_kmh=drone.cruise_speed_kmh,
                        route=full_route,
                        wind=request.wind,
                        speed_limit_kmh=segment.survey_speed_limit_kmh,
                        transit_route=transit_route,
                        transit_distance_km=departure_distance,
                        survey_route=optimized.route,
                        survey_distance_km=survey_distance,
                        landing_route=landing_route,
                        landing_distance_km=landing_distance,
                    )
                )
            except PlanningError as error:
                errors.append(error)
                continue
            try:
                vertical_profile = _vertical_profile(
                    full_route,
                    drone,
                    request,
                    vertical_profile_planner,
                    segment.segment_id,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            try:
                _validate_vertical_airspace(vertical_profile, request, segment.segment_id)
            except PlanningError as error:
                errors.append(error)
                continue
            try:
                emergency_reachability = _evaluate_emergency_reachability(
                    phases=[
                        FlightPhase(
                            route=transit_route,
                            distance_km=departure_distance,
                            ground_speed_limit_kmh=None,
                        ),
                        FlightPhase(
                            route=optimized.route,
                            distance_km=survey_distance,
                            ground_speed_limit_kmh=segment.survey_speed_limit_kmh,
                        ),
                        FlightPhase(
                            route=landing_route,
                            distance_km=landing_distance,
                            ground_speed_limit_kmh=None,
                        ),
                    ],
                    drone=drone,
                    request=request,
                    constraints=constraints,
                    segment_id=segment.segment_id,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            choices.append(
                OptimizedMissionRoute(
                    survey_route=optimized.route,
                    transit_route=transit_route,
                    landing_route=landing_route,
                    departure_site=departure_site,
                    landing_site=landing_site,
                    survey_distance_km=survey_distance,
                    transit_distance_km=transit_distance,
                    total_distance_km=total_distance,
                    duration_estimate=duration_estimate,
                    emergency_reachability=emergency_reachability,
                    vertical_profile=vertical_profile,
                )
            )
    if choices:
        return min(choices, key=_route_choice_key)
    if errors:
        raise errors[0]
    raise PlanningError(
        code=ErrorCode.NO_ELIGIBLE_FLIGHT_SITE,
        message=f"Для участка {segment.segment_id} нет допустимой пары площадок.",
    )


def _preserves_curved_route(route: list[Coordinate], segment: WorkSegment) -> bool:
    return any(
        route == candidate or route == list(reversed(candidate))
        for candidate in [segment.route, *segment.alternative_routes]
    )


def _route_choice_key(choice: OptimizedMissionRoute) -> tuple[float, str, str]:
    departure_id = choice.departure_site.site_id if choice.departure_site is not None else ""
    landing_id = choice.landing_site.site_id if choice.landing_site is not None else ""
    vertical_minutes = (
        choice.vertical_profile.vertical_energy_equivalent_minutes
        if choice.vertical_profile is not None
        else 0
    )
    return choice.duration_estimate.duration_minutes + vertical_minutes, departure_id, landing_id


def _validate_vertical_airspace(
    profile: VerticalProfileResult | None,
    request: PlanRequest,
    segment_id: str,
) -> None:
    if profile is None or not request.allowed_airspaces:
        return
    for start, end in pairwise(profile.points):
        compatible = [
            airspace
            for airspace in request.allowed_airspaces
            if airspace.minimum_altitude_m <= _profile_altitude(start, airspace.altitude_reference)
            and _profile_altitude(start, airspace.altitude_reference) <= airspace.maximum_altitude_m
            and airspace.minimum_altitude_m
            <= _profile_altitude(end, airspace.altitude_reference)
            <= airspace.maximum_altitude_m
        ]
        if compatible and route_is_inside_allowed_airspaces(
            [start.coordinate, end.coordinate],
            compatible,
        ):
            continue
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message=(
                f"Terrain-following profile for segment {segment_id} leaves allowed 3D airspace."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                    message="Adjust AGL, terrain corridor, or allowed altitude limits.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="mission_altitude_m",
                    current_value=request.mission_altitude_m,
                    affected_ids=[segment_id],
                )
            ],
        )


def _vertical_profile(
    route: list[Coordinate],
    drone: DroneSpec,
    request: PlanRequest,
    planner: VerticalProfilePlanner,
    segment_id: str,
) -> VerticalProfileResult | None:
    if request.altitude_reference is AltitudeReference.MSL:
        return None
    maximum_climb_rate = drone.maximum_climb_rate_mps
    maximum_descent_rate = drone.maximum_descent_rate_mps
    if maximum_climb_rate is None or maximum_descent_rate is None:
        raise PlanningError(
            code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
            message=f"Для БВС {drone.drone_id} не заданы пределы вертикальной скорости.",
        )
    try:
        maximum_ground_speed_kmh = drone.cruise_speed_kmh + (
            request.wind.speed_mps * 3.6 if request.wind is not None else 0
        )
        terrain_source = request.terrain_source or InlineTerrainSource(
            samples=request.terrain_elevation_samples
        )
        return planner.plan(
            route=route,
            target_agl_m=request.mission_altitude_m,
            maximum_ground_speed_kmh=maximum_ground_speed_kmh,
            source=terrain_source,
            sampling_interval_m=request.terrain_sampling_interval_m,
            maximum_interpolation_distance_m=request.terrain_max_interpolation_distance_m,
            maximum_climb_rate_mps=maximum_climb_rate,
            maximum_descent_rate_mps=maximum_descent_rate,
            terrain_vertical_uncertainty_m=request.terrain_vertical_uncertainty_m,
        )
    except (TerrainDataUnavailableError, InvalidTerrainSourceError) as error:
        raise PlanningError(
            code=ErrorCode.TERRAIN_DATA_UNAVAILABLE,
            message=(
                f"Для маршрута участка {segment_id} нет отметки рельефа ближе "
                f"{request.terrain_max_interpolation_distance_m:g} м."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
                    message="Добавьте DEM-отметки вдоль всего маршрута и площадок.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="terrain_elevation_samples",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        ) from error
    except VerticalRateExceededError as error:
        required_climb = error.required_climb_rate_mps
        required_descent = error.required_descent_rate_mps
        raise PlanningError(
            code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
            message=(
                f"Segment {segment_id} requires climb {required_climb:.2f} m/s "
                f"and descent {required_descent:.2f} m/s; aircraft limits are "
                f"{maximum_climb_rate:g}/{maximum_descent_rate:g} m/s."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_CAPABLE_DRONE,
                    message=(
                        "Use an aircraft with sufficient vertical performance or "
                        "reroute around the abrupt terrain change."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_climb_rate_mps",
                    current_value=maximum_climb_rate,
                    required_value=round(required_climb, 3),
                    unit="m/s",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        ) from error
    except ValueError as error:
        raise PlanningError(
            code=ErrorCode.TERRAIN_DATA_UNAVAILABLE,
            message=f"Could not build the vertical profile for segment {segment_id}.",
        ) from error


def _survey_distance(
    segment: WorkSegment,
    optimized: RouteOptimizationResult,
) -> float:
    if optimized.selected_route_index > 0:
        return route_distance_km(optimized.route)
    return segment.distance_km * calculate_route_multiplier(
        segment.route,
        optimized.route,
    )


def _optimize_route(
    optimizer: RouteOptimizer,
    request: RouteOptimizationRequest,
    segment_id: str,
) -> RouteOptimizationResult:
    try:
        return optimizer.optimize(request)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Для участка {segment_id} не найден безопасный маршрут.",
        ) from error


def _safe_route(
    route: list[Coordinate],
    constraints: list[NoFlyZone],
    segment_id: str,
) -> list[Coordinate]:
    try:
        return build_safe_route(route, constraints)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Для участка {segment_id} не найден безопасный транзит.",
        ) from error


def _departure_route(
    departure: Coordinate,
    survey_route: list[Coordinate],
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
) -> list[Coordinate]:
    if request.minimum_turn_radius_m == 0:
        return _safe_route(
            [departure, survey_route[0]],
            constraints,
            segment.segment_id,
        )
    if departure == survey_route[0]:
        return [departure, departure]
    source = [
        _coordinate_before(
            departure,
            survey_route[0],
            request.minimum_turn_radius_m,
        ),
        departure,
    ]
    return _curvature_constrained_connection(
        source,
        survey_route[:2],
        segment,
        request,
        constraints,
        transition_planner,
        phase_name="транзит к зоне съёмки",
    )


def _landing_route(
    survey_route: list[Coordinate],
    landing: Coordinate,
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
) -> list[Coordinate]:
    if request.minimum_turn_radius_m == 0:
        return _safe_route(
            [survey_route[-1], landing],
            constraints,
            segment.segment_id,
        )
    if survey_route[-1] == landing:
        return [landing, landing]
    target = [
        landing,
        _coordinate_beyond(
            survey_route[-1],
            landing,
            request.minimum_turn_radius_m,
        ),
    ]
    return _curvature_constrained_connection(
        survey_route[-2:],
        target,
        segment,
        request,
        constraints,
        transition_planner,
        phase_name="подход к посадке",
    )


def _curvature_constrained_connection(
    source: list[Coordinate],
    target: list[Coordinate],
    segment: WorkSegment,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    transition_planner: SweepTransitionPlanner,
    phase_name: str,
) -> list[Coordinate]:
    coverage_area = _coverage_area(request.coverage_areas, segment.coverage_area_id)
    if coverage_area is None:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=f"Для участка {segment.segment_id} не определена зона съёмки.",
        )
    transition_request = SweepRouteCurvatureRequest(
        route=target,
        coverage_area=coverage_area,
        no_fly_zones=constraints,
        minimum_turn_radius_m=request.minimum_turn_radius_m,
        sampling_step_m=max(3, min(25, request.minimum_turn_radius_m / 4)),
    )
    try:
        transition = transition_planner.connect(source, target, transition_request)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.TURN_RADIUS_UNSATISFIED,
            message=(
                f"Для участка {segment.segment_id} не построен безопасный {phase_name}. "
                f"Радиус: {request.minimum_turn_radius_m:g} м."
            ),
        ) from error
    return transition.route


def _coordinate_before(
    start: Coordinate,
    end: Coordinate,
    distance_m: float,
) -> Coordinate:
    projection = projection_for_coordinates([start, end])
    start_x, start_y = projection.project(start)
    end_x, end_y = projection.project(end)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length = hypot(delta_x, delta_y)
    if length == 0:
        raise ValueError("Нельзя продолжить направление между совпадающими точками.")
    return projection.unproject(
        start_x - delta_x / length * distance_m,
        start_y - delta_y / length * distance_m,
    )


def _coordinate_beyond(
    start: Coordinate,
    end: Coordinate,
    distance_m: float,
) -> Coordinate:
    projection = projection_for_coordinates([start, end])
    start_x, start_y = projection.project(start)
    end_x, end_y = projection.project(end)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length = hypot(delta_x, delta_y)
    if length == 0:
        raise ValueError("Нельзя продолжить направление между совпадающими точками.")
    return projection.unproject(
        end_x + delta_x / length * distance_m,
        end_y + delta_y / length * distance_m,
    )


def _eligible_departure_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite | None]:
    candidates = [
        site
        for site in _configured_departure_sites(request)
        if _site_supports_drone(site, drone_id)
    ]
    eligible: list[FlightSite | None] = []
    eligible.extend(candidates)
    return eligible if eligible else [None]


def _eligible_landing_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite | None]:
    candidates = [
        site
        for site in _configured_primary_landing_sites(request)
        if _site_supports_drone(site, drone_id)
    ]
    eligible: list[FlightSite | None] = []
    eligible.extend(candidates)
    return eligible if eligible else [None]


def _eligible_alternate_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite]:
    return [
        site for site in request.alternate_landing_sites if _site_supports_drone(site, drone_id)
    ]


def _site_supports_drone(site: FlightSite, drone_id: str) -> bool:
    return not site.assigned_drone_ids or drone_id in site.assigned_drone_ids


def _configured_departure_sites(request: PlanRequest) -> list[FlightSite]:
    return [site for site in [request.departure_site, *request.departure_sites] if site is not None]


def _configured_primary_landing_sites(request: PlanRequest) -> list[FlightSite]:
    return [site for site in [request.landing_site, *request.landing_sites] if site is not None]


def _combine_routes(*routes: list[Coordinate]) -> list[Coordinate]:
    combined: list[Coordinate] = []
    for route in routes:
        for coordinate in route:
            if not combined or coordinate != combined[-1]:
                combined.append(coordinate)
    return combined


def _validate_geometry(
    full_route: list[Coordinate],
    survey_route: list[Coordinate],
    constraints: list[NoFlyZone],
    segment: WorkSegment,
    request: PlanRequest,
) -> None:
    if not route_is_clear_of_no_fly_zones(full_route, constraints):
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Маршрут участка {segment.segment_id} пересекает запретную зону.",
        )
    coverage_area = _coverage_area(request.coverage_areas, segment.coverage_area_id)
    if (
        coverage_area is not None
        and request.minimum_turn_radius_m == 0
        and not route_is_inside_coverage_area(survey_route, coverage_area)
    ):
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Галсы участка {segment.segment_id} выходят за целевую зону.",
        )
    if not request.allowed_airspaces:
        return
    if request.altitude_reference is AltitudeReference.AGL:
        if not route_is_inside_allowed_airspaces(full_route, request.allowed_airspaces):
            raise PlanningError(
                code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
                message=f"Route for segment {segment.segment_id} leaves allowed airspace.",
            )
        return
    compatible = [
        airspace
        for airspace in request.allowed_airspaces
        if airspace.altitude_reference is request.altitude_reference
        and airspace.minimum_altitude_m <= request.mission_altitude_m <= airspace.maximum_altitude_m
    ]
    if not compatible:
        nearest_altitude = min(
            (
                min(
                    max(request.mission_altitude_m, airspace.minimum_altitude_m),
                    airspace.maximum_altitude_m,
                )
                for airspace in request.allowed_airspaces
            ),
            key=lambda altitude: abs(altitude - request.mission_altitude_m),
        )
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message="Высота задания не входит в разрешённое воздушное пространство.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                    message=(
                        "Измените высоту задания до ближайшего допустимого уровня "
                        f"{nearest_altitude:g} м."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="mission_altitude_m",
                    current_value=request.mission_altitude_m,
                    required_value=nearest_altitude,
                    unit="m",
                    affected_ids=[segment.segment_id],
                )
            ],
        )
    if not route_is_inside_allowed_airspaces(full_route, compatible):
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message=(
                f"Маршрут участка {segment.segment_id} выходит за границы "
                "разрешённого воздушного пространства."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
                    message=(
                        "Расширьте подтверждённый разрешённый коридор либо перенесите "
                        "площадки и рабочую область внутрь него."
                    ),
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="allowed_airspaces",
                    affected_ids=[segment.segment_id],
                )
            ],
        )


def _coverage_area(
    areas: list[CoverageArea],
    area_id: str | None,
) -> CoverageArea | None:
    if area_id is None:
        return None
    return next((area for area in areas if area.area_id == area_id), None)


def _validate_route_envelope(
    drone: DroneSpec,
    optimized: OptimizedMissionRoute,
    route: list[Coordinate],
    segment_id: str,
) -> None:
    if (
        drone.maximum_route_distance_km is not None
        and optimized.total_distance_km > drone.maximum_route_distance_km
    ):
        raise PlanningError(
            code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
            message=(
                f"Маршрут участка {segment_id} длиннее паспортного предела БВС {drone.drone_id}."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REDUCE_MISSION_SCOPE,
                    message="Разделите участок на более короткие вылеты.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_route_distance_km",
                    current_value=round(optimized.total_distance_km, 3),
                    required_value=drone.maximum_route_distance_km,
                    unit="km",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        )
    if drone.maximum_control_range_km is None:
        return
    departure = route[0]
    maximum_radius_km = max(coordinate_distance_km(departure, point) for point in route)
    if maximum_radius_km <= drone.maximum_control_range_km:
        return
    raise PlanningError(
        code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
        message=(
            f"Маршрут участка {segment_id} выходит за подтверждённую дальность "
            f"управления БВС {drone.drone_id}."
        ),
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.REDUCE_MISSION_SCOPE,
                message="Перенесите площадку ближе к зоне или разделите операцию.",
                confidence=RecommendationConfidence.EXACT,
                parameter="maximum_control_range_km",
                current_value=round(maximum_radius_km, 3),
                required_value=drone.maximum_control_range_km,
                unit="km",
                affected_ids=[segment_id, drone.drone_id],
            )
        ],
    )


def _candidate_is_feasible(candidate: MissionCandidate) -> bool:
    available_percent = (
        candidate.drone.initial_charge_percent - candidate.drone.battery_reserve_percent
    )
    return candidate.mission.estimated_battery_percent <= available_percent


def _effective_endurance_minutes(drone: DroneSpec) -> float:
    return (
        drone.maximum_flight_minutes
        * drone.battery_health_percent
        / 100
        / drone.energy_consumption_multiplier
    )


def _battery_use_percent(energy_equivalent_minutes: float, drone: DroneSpec) -> float:
    return energy_equivalent_minutes / _effective_endurance_minutes(drone) * 100


def _battery_endurance_error(
    segment: WorkSegment,
    candidates: list[MissionCandidate],
) -> PlanningError:
    best = min(
        candidates,
        key=lambda candidate: (
            (
                candidate.mission.duration_minutes
                + candidate.mission.vertical_energy_equivalent_minutes
            )
            / (
                (candidate.drone.initial_charge_percent - candidate.drone.battery_reserve_percent)
                / 100
            )
            - _effective_endurance_minutes(candidate.drone),
            candidate.drone.drone_id,
        ),
    )
    usable_fraction = (best.drone.initial_charge_percent - best.drone.battery_reserve_percent) / 100
    required_effective_endurance = (
        best.mission.duration_minutes + best.mission.vertical_energy_equivalent_minutes
    ) / usable_fraction
    required_endurance = (
        required_effective_endurance
        * best.drone.energy_consumption_multiplier
        / (best.drone.battery_health_percent / 100)
    )
    recommendations: list[FeasibilityRecommendation] = []
    if best.drone.initial_charge_percent < 100:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.RECHARGE_BATTERY,
                message=(
                    f"Зарядите БВС {best.drone.drone_id} выше текущих "
                    f"{best.drone.initial_charge_percent:g}% либо назначьте другой аппарат."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="initial_charge_percent",
                current_value=best.drone.initial_charge_percent,
                required_value=min(
                    100,
                    round(
                        best.mission.estimated_battery_percent + best.drone.battery_reserve_percent,
                        2,
                    ),
                ),
                unit="percent",
                affected_ids=[segment.segment_id, best.drone.drone_id],
            )
        )
    recommendations.extend(
        [
            FeasibilityRecommendation(
                code=RecommendationCode.EXTEND_ENDURANCE,
                message=(
                    f"Для БВС {best.drone.drone_id} требуется паспортная продолжительность "
                    f"не менее {required_endurance:.1f} мин при сохранении резерва "
                    f"{best.drone.battery_reserve_percent:g}%."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="maximum_flight_minutes",
                current_value=best.drone.maximum_flight_minutes,
                required_value=round(required_endurance, 2),
                unit="minutes",
                affected_ids=[segment.segment_id, best.drone.drone_id],
            ),
            FeasibilityRecommendation(
                code=RecommendationCode.REDUCE_MISSION_SCOPE,
                message=("Разделите участок на более короткие вылеты без снижения резерва АКБ."),
                confidence=RecommendationConfidence.ESTIMATE,
                affected_ids=[segment.segment_id],
            ),
        ]
    )
    return PlanningError(
        code=ErrorCode.UNPLANNABLE_SEGMENT,
        message=(
            f"Участок {segment.segment_id} не помещается в безопасную продолжительность полёта."
        ),
        recommendations=recommendations,
    )


def _constraints_for_segment(
    segment: WorkSegment,
    request: PlanRequest,
) -> list[NoFlyZone]:
    coverage_holes = _coverage_hole_zones(segment, request.coverage_areas)
    obstacle_zones = [
        _obstacle_zone(obstacle)
        for obstacle in request.obstacles
        if (
            request.altitude_reference is AltitudeReference.AGL
            or _obstacle_requires_avoidance(obstacle, request.mission_altitude_m)
        )
        and _obstacle_is_near_segment(obstacle, segment)
    ]
    permanent_no_fly_zones = [
        zone
        for zone in request.no_fly_zones
        if (
            zone.altitude_reference is not request.altitude_reference
            or _zone_applies_at_altitude(zone, request.mission_altitude_m)
        )
        and (zone.validity is None or zone.validity.mode is ValidityMode.PERMANENT)
    ]
    return [*permanent_no_fly_zones, *coverage_holes, *obstacle_zones]


def _zone_applies_at_altitude(zone: NoFlyZone, altitude_m: float) -> bool:
    if zone.minimum_altitude_m is None or zone.maximum_altitude_m is None:
        return True
    return zone.minimum_altitude_m <= altitude_m <= zone.maximum_altitude_m


def _coverage_hole_zones(
    segment: WorkSegment,
    coverage_areas: list[CoverageArea],
) -> list[NoFlyZone]:
    area = _coverage_area(coverage_areas, segment.coverage_area_id)
    if area is None:
        return []
    return [
        NoFlyZone(
            zone_id=f"coverage-hole-{area.area_id}-{index + 1}"[:64],
            coordinates=ring,
            safety_margin_m=GEOMETRY_CLEARANCE_M,
        )
        for index, ring in enumerate(area.interior_rings)
        if len(ring) >= 3
    ]


def _obstacle_requires_avoidance(
    obstacle: FlightObstacle,
    mission_altitude_m: float,
) -> bool:
    vertical_clearance_m = 30
    return mission_altitude_m <= obstacle.height_m + vertical_clearance_m


def _obstacle_is_near_segment(obstacle: FlightObstacle, segment: WorkSegment) -> bool:
    segment_longitudes = [coordinate.longitude for coordinate in segment.route]
    segment_latitudes = [coordinate.latitude for coordinate in segment.route]
    obstacle_longitudes = [coordinate.longitude for coordinate in obstacle.coordinates]
    obstacle_latitudes = [coordinate.latitude for coordinate in obstacle.coordinates]
    margin = obstacle.safety_radius_m / 111_000
    return not (
        max(obstacle_longitudes) + margin < min(segment_longitudes)
        or min(obstacle_longitudes) - margin > max(segment_longitudes)
        or max(obstacle_latitudes) + margin < min(segment_latitudes)
        or min(obstacle_latitudes) - margin > max(segment_latitudes)
    )


def _obstacle_zone(obstacle: FlightObstacle) -> NoFlyZone:
    longitudes = [coordinate.longitude for coordinate in obstacle.coordinates]
    latitudes = [coordinate.latitude for coordinate in obstacle.coordinates]
    center_latitude = (min(latitudes) + max(latitudes)) / 2
    latitude_margin = obstacle.safety_radius_m / 111_000
    longitude_scale = max(cos(radians(center_latitude)), 0.2)
    longitude_margin = latitude_margin / longitude_scale
    minimum_longitude = min(longitudes) - longitude_margin
    maximum_longitude = max(longitudes) + longitude_margin
    minimum_latitude = min(latitudes) - latitude_margin
    maximum_latitude = max(latitudes) + latitude_margin
    return NoFlyZone(
        zone_id=f"obstacle-{obstacle.obstacle_id}"[:64],
        coordinates=[
            Coordinate(longitude=minimum_longitude, latitude=minimum_latitude),
            Coordinate(longitude=maximum_longitude, latitude=minimum_latitude),
            Coordinate(longitude=maximum_longitude, latitude=maximum_latitude),
            Coordinate(longitude=minimum_longitude, latitude=maximum_latitude),
        ],
        safety_margin_m=0,
    )


def _evaluate_emergency_reachability(
    phases: list[FlightPhase],
    drone: DroneSpec,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    segment_id: str,
) -> EmergencyReachability | None:
    alternate_sites = _eligible_alternate_sites(request, drone.drone_id)
    if not request.alternate_landing_sites:
        return None
    if not alternate_sites:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=f"Для БВС {drone.drone_id} нет назначенной резервной площадки.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_ALTERNATE_SITE,
                    message=(
                        f"Назначьте БВС {drone.drone_id} резервную площадку, "
                        "доступную из любой части маршрута."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="alternate_landing_sites",
                    affected_ids=[drone.drone_id, segment_id],
                )
            ],
        )

    checkpoints = _emergency_route_nodes(phases, drone, request.wind)
    available_minutes = (
        _effective_endurance_minutes(drone)
        * (drone.initial_charge_percent - drone.battery_reserve_percent)
        / 100
    )
    minimum_ground_speed_kmh = _minimum_possible_ground_speed_kmh(drone, request.wind)
    evaluated: list[EvaluatedEmergencyCheckpoint] = []

    def evaluate(checkpoint: EmergencyCheckpoint) -> EvaluatedEmergencyCheckpoint:
        diversion = _fastest_emergency_diversion(
            checkpoint.coordinate,
            alternate_sites,
            drone,
            request,
            constraints,
            segment_id,
        )
        if diversion is None:
            raise PlanningError(
                code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
                message=(
                    f"Из контрольной точки маршрута участка {segment_id} "
                    "нет безопасного пути к резервной площадке."
                ),
            )
        site, diversion_minutes, diversion_route = diversion
        result = EvaluatedEmergencyCheckpoint(
            checkpoint=checkpoint,
            site=site,
            diversion_minutes=diversion_minutes,
            diversion_route=diversion_route,
        )
        evaluated.append(result)
        return result

    endpoint_evaluations = [evaluate(checkpoint) for checkpoint in checkpoints]
    certificates = [
        _certify_emergency_interval(
            start,
            end,
            evaluate,
            minimum_ground_speed_kmh,
        )
        for start, end in pairwise(endpoint_evaluations)
    ]
    if not evaluated:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=f"Для участка {segment_id} не удалось проверить резервную посадку.",
        )
    critical = max(
        evaluated,
        key=lambda item: (item.required_minutes, item.site.site_id),
    )
    certified_required_minutes = max(
        critical.required_minutes,
        *(certificate.maximum_required_minutes for certificate in certificates),
    )
    minimum_margin_minutes = available_minutes - certified_required_minutes
    certification_error_minutes = certified_required_minutes - critical.required_minutes
    maximum_interval_m = max(
        (certificate.maximum_interval_m for certificate in certificates),
        default=0,
    )
    margin_percent = minimum_margin_minutes / _effective_endurance_minutes(drone) * 100
    if margin_percent < 0:
        usable_fraction = (drone.initial_charge_percent - drone.battery_reserve_percent) / 100
        required_effective_endurance = certified_required_minutes / usable_fraction
        required_endurance = (
            required_effective_endurance
            * drone.energy_consumption_multiplier
            / (drone.battery_health_percent / 100)
        )
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=(
                f"Участок {segment_id}: уход на резервную площадку "
                f"{critical.site.name} нарушает резерв АКБ на "
                f"{abs(margin_percent):.1f} п.п."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.EXTEND_ENDURANCE,
                    message=(
                        f"Для гарантированного ухода требуется продолжительность полёта "
                        f"не менее {required_endurance:.1f} мин при текущем резерве."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_flight_minutes",
                    current_value=drone.maximum_flight_minutes,
                    required_value=round(required_endurance, 2),
                    unit="minutes",
                    affected_ids=[drone.drone_id, segment_id, critical.site.site_id],
                ),
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_ALTERNATE_SITE,
                    message="Добавьте более близкую резервную площадку без уменьшения резерва АКБ.",
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="alternate_landing_sites",
                    affected_ids=[segment_id],
                ),
            ],
        )
    return EmergencyReachability(
        minimum_margin_percent=margin_percent,
        critical_site_id=critical.site.site_id,
        critical_route=critical.diversion_route,
        critical_checkpoint_elapsed_minutes=critical.checkpoint.elapsed_minutes,
        critical_diversion_minutes=critical.diversion_minutes,
        certification_error_minutes=certification_error_minutes,
        maximum_interval_m=maximum_interval_m,
        evaluated_checkpoint_count=len(evaluated),
    )


def _emergency_route_nodes(
    phases: list[FlightPhase],
    drone: DroneSpec,
    wind: WindCondition | None,
) -> list[EmergencyCheckpoint]:
    checkpoints: list[EmergencyCheckpoint] = []
    elapsed_minutes = 0.0
    for phase in phases:
        if phase.distance_km == 0:
            continue
        if not checkpoints or checkpoints[-1].coordinate != phase.route[0]:
            checkpoints.append(
                EmergencyCheckpoint(
                    coordinate=phase.route[0],
                    elapsed_minutes=elapsed_minutes,
                )
            )
        geometric_distance = route_distance_km(phase.route)
        if geometric_distance == 0:
            continue
        for start, end in pairwise(phase.route):
            leg_geometric_distance = coordinate_distance_km(start, end)
            if leg_geometric_distance == 0:
                continue
            leg_distance = phase.distance_km * leg_geometric_distance / geometric_distance
            elapsed_minutes += _route_duration_minutes(
                [start, end],
                leg_distance,
                drone.cruise_speed_kmh,
                wind,
                phase.ground_speed_limit_kmh,
            )
            checkpoints.append(
                EmergencyCheckpoint(
                    coordinate=end,
                    elapsed_minutes=elapsed_minutes,
                )
            )
    return checkpoints


def _certify_emergency_interval(
    start: EvaluatedEmergencyCheckpoint,
    end: EvaluatedEmergencyCheckpoint,
    evaluate: Callable[[EmergencyCheckpoint], EvaluatedEmergencyCheckpoint],
    minimum_ground_speed_kmh: float,
    depth: int = 0,
) -> EmergencyIntervalCertificate:
    distance_km = coordinate_distance_km(
        start.checkpoint.coordinate,
        end.checkpoint.coordinate,
    )
    upper_bound = _emergency_interval_upper_bound(
        start,
        end,
        distance_km,
        minimum_ground_speed_kmh,
    )
    observed = max(start.required_minutes, end.required_minutes)
    if (
        upper_bound - observed <= ALTERNATE_CERTIFICATION_TOLERANCE_MINUTES
        or distance_km * 1_000 <= ALTERNATE_MINIMUM_INTERVAL_M
        or depth >= ALTERNATE_MAXIMUM_REFINEMENT_DEPTH
    ):
        return EmergencyIntervalCertificate(
            maximum_required_minutes=upper_bound,
            maximum_interval_m=distance_km * 1_000,
        )
    midpoint = evaluate(
        EmergencyCheckpoint(
            coordinate=_interpolate_coordinate(
                start.checkpoint.coordinate,
                end.checkpoint.coordinate,
                0.5,
            ),
            elapsed_minutes=(start.checkpoint.elapsed_minutes + end.checkpoint.elapsed_minutes) / 2,
        )
    )
    left = _certify_emergency_interval(
        start,
        midpoint,
        evaluate,
        minimum_ground_speed_kmh,
        depth + 1,
    )
    right = _certify_emergency_interval(
        midpoint,
        end,
        evaluate,
        minimum_ground_speed_kmh,
        depth + 1,
    )
    return EmergencyIntervalCertificate(
        maximum_required_minutes=max(
            left.maximum_required_minutes,
            right.maximum_required_minutes,
        ),
        maximum_interval_m=max(left.maximum_interval_m, right.maximum_interval_m),
    )


def _emergency_interval_upper_bound(
    start: EvaluatedEmergencyCheckpoint,
    end: EvaluatedEmergencyCheckpoint,
    distance_km: float,
    minimum_ground_speed_kmh: float,
) -> float:
    elapsed_delta = end.checkpoint.elapsed_minutes - start.checkpoint.elapsed_minutes
    diversion_variation = distance_km / minimum_ground_speed_kmh * 60
    increasing_slope = elapsed_delta + diversion_variation
    decreasing_origin = end.required_minutes + diversion_variation
    denominator = increasing_slope + diversion_variation
    intersection = (
        min(1.0, max(0.0, (decreasing_origin - start.required_minutes) / denominator))
        if denominator > 0
        else 0
    )
    intersection_bound = min(
        start.required_minutes + increasing_slope * intersection,
        decreasing_origin - diversion_variation * intersection,
    )
    return max(start.required_minutes, end.required_minutes, intersection_bound)


def _minimum_possible_ground_speed_kmh(
    drone: DroneSpec,
    wind: WindCondition | None,
) -> float:
    wind_speed_kmh = wind.speed_mps * 3.6 if wind is not None else 0
    minimum_speed = drone.cruise_speed_kmh - wind_speed_kmh
    if minimum_speed <= 0:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=(
                f"Для БВС {drone.drone_id} нельзя сертифицировать аварийный уход: "
                "скорость ветра не оставляет положительной путевой скорости во всех направлениях."
            ),
        )
    return minimum_speed


def _interpolate_coordinate(start: Coordinate, end: Coordinate, fraction: float) -> Coordinate:
    return Coordinate(
        longitude=start.longitude + (end.longitude - start.longitude) * fraction,
        latitude=start.latitude + (end.latitude - start.latitude) * fraction,
    )


def _fastest_emergency_diversion(
    coordinate: Coordinate,
    alternate_sites: list[FlightSite],
    drone: DroneSpec,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    segment_id: str,
) -> tuple[FlightSite, float, list[Coordinate]] | None:
    choices: list[tuple[FlightSite, float, list[Coordinate]]] = []
    for site in alternate_sites:
        try:
            route = _safe_route(
                [coordinate, site.coordinate],
                constraints,
                segment_id,
            )
        except PlanningError:
            continue
        if not _route_is_inside_configured_airspace(route, request):
            continue
        distance_km = route_distance_km(route)
        if distance_km == 0:
            choices.append((site, 0, route))
            continue
        try:
            duration_minutes = _route_duration_minutes(
                route,
                distance_km,
                drone.cruise_speed_kmh,
                request.wind,
            )
        except PlanningError:
            continue
        choices.append((site, duration_minutes, route))
    if not choices:
        return None
    return min(choices, key=lambda choice: (choice[1], choice[0].site_id))


def _route_is_inside_configured_airspace(
    route: list[Coordinate],
    request: PlanRequest,
) -> bool:
    if not request.allowed_airspaces:
        return True
    if request.altitude_reference is AltitudeReference.AGL:
        return route_is_inside_allowed_airspaces(route, request.allowed_airspaces)
    compatible = [
        airspace
        for airspace in request.allowed_airspaces
        if airspace.altitude_reference is request.altitude_reference
        and airspace.minimum_altitude_m <= request.mission_altitude_m <= airspace.maximum_altitude_m
    ]
    return bool(compatible) and route_is_inside_allowed_airspaces(route, compatible)


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


def _create_drone_plans(
    drones: list[DroneSpec],
    assignments: dict[str, list[Mission]],
) -> list[DronePlan]:
    return [
        DronePlan(
            drone_id=drone.drone_id,
            mission_count=len(assignments[drone.drone_id]),
            total_duration_minutes=round(
                sum(mission.duration_minutes for mission in assignments[drone.drone_id]),
                2,
            ),
            missions=assignments[drone.drone_id],
            autopilot_profile=drone.autopilot_profile,
            maintenance=_maintenance_report(drone, assignments[drone.drone_id]),
        )
        for drone in drones
    ]


def _maintenance_report(drone: DroneSpec, missions: list[Mission]) -> MaintenanceReport:
    projected_flights = drone.completed_flights_since_maintenance + len(missions)
    projected_flight_hours = (
        drone.flight_hours_since_maintenance
        + sum(mission.duration_minutes for mission in missions) / 60
    )
    remaining_flights = (
        max(0, drone.maintenance_interval_flights - projected_flights)
        if drone.maintenance_interval_flights is not None
        else None
    )
    remaining_hours = (
        max(0.0, drone.maintenance_interval_hours - projected_flight_hours)
        if drone.maintenance_interval_hours is not None
        else None
    )
    due_by_flights = remaining_flights == 0 if remaining_flights is not None else False
    due_by_hours = remaining_hours == 0 if remaining_hours is not None else False
    maintenance_due = due_by_flights or due_by_hours
    if drone.maintenance_interval_flights is None and drone.maintenance_interval_hours is None:
        message = "Норматив технического обслуживания для БВС не задан."
    elif maintenance_due:
        message = "После выполнения плана требуется техническое обслуживание."
    else:
        limits = []
        if remaining_flights is not None:
            limits.append(f"{remaining_flights} полётов")
        if remaining_hours is not None:
            limits.append(f"{remaining_hours:.1f} часа налёта")
        message = "До технического обслуживания осталось: " + ", ".join(limits) + "."
    return MaintenanceReport(
        projected_flights=projected_flights,
        projected_flight_hours=round(projected_flight_hours, 2),
        remaining_flights=remaining_flights,
        remaining_hours=round(remaining_hours, 2) if remaining_hours is not None else None,
        maintenance_due_after_plan=maintenance_due,
        message=message,
    )


def _create_metrics(
    drone_plans: list[DronePlan],
    available_drone_count: int,
) -> PlanMetrics:
    durations = [drone_plan.total_duration_minutes for drone_plan in drone_plans]
    scheduled_ends = [
        mission.end_offset_minutes
        for drone_plan in drone_plans
        for mission in drone_plan.missions
        if mission.end_offset_minutes is not None
    ]
    completion_minutes = max(scheduled_ends, default=max(durations))
    total_distance = sum(
        mission.distance_km for drone_plan in drone_plans for mission in drone_plan.missions
    )
    survey_distance = sum(
        mission.survey_distance_km or 0
        for drone_plan in drone_plans
        for mission in drone_plan.missions
    )
    transit_distance = sum(
        mission.transit_distance_km or 0
        for drone_plan in drone_plans
        for mission in drone_plan.missions
    )
    active_drone_count = sum(plan.mission_count > 0 for plan in drone_plans)
    available_fleet_minutes = completion_minutes * available_drone_count
    utilization = sum(durations) / available_fleet_minutes * 100
    return PlanMetrics(
        completion_minutes=round(completion_minutes, 2),
        total_flight_minutes=round(sum(durations), 2),
        total_distance_km=round(total_distance, 3),
        survey_distance_km=round(survey_distance, 3),
        transit_distance_km=round(transit_distance, 3),
        useful_flight_percent=round(
            survey_distance / total_distance * 100 if total_distance > 0 else 0,
            2,
        ),
        average_fleet_utilization_percent=round(utilization, 2),
        active_drone_count=active_drone_count,
        sortie_count=sum(plan.mission_count for plan in drone_plans),
    )


def _create_feasibility_report(
    metrics: PlanMetrics,
    request: PlanRequest,
    eligible_drone_count: int,
) -> FeasibilityReport:
    deadline = request.completion_deadline_minutes
    if deadline is None:
        return FeasibilityReport(
            status=FeasibilityStatus.FEASIBLE,
            message=(
                f"План выполним: используется {metrics.active_drone_count} БВС, "
                f"расчётный срок {metrics.completion_minutes:.1f} мин."
            ),
            maximum_active_drones=request.maximum_active_drones,
            used_drone_count=metrics.active_drone_count,
        )
    deadline_margin = deadline - metrics.completion_minutes
    if deadline_margin >= 0:
        return FeasibilityReport(
            status=FeasibilityStatus.FEASIBLE,
            message=(
                f"План укладывается в срок; резерв {deadline_margin:.1f} мин, "
                f"используется {metrics.active_drone_count} БВС."
            ),
            requested_deadline_minutes=deadline,
            maximum_active_drones=request.maximum_active_drones,
            used_drone_count=metrics.active_drone_count,
            estimated_required_drone_count=metrics.active_drone_count,
            deadline_margin_minutes=round(deadline_margin, 2),
        )
    estimated_required = max(
        metrics.active_drone_count + 1,
        ceil(metrics.total_flight_minutes / deadline),
    )
    if estimated_required <= eligible_drone_count:
        recommendation = f"Оценочно требуется не менее {estimated_required} БВС."
    else:
        recommendation = (
            f"Оценочно требуется не менее {estimated_required} БВС, "
            f"но доступно только {eligible_drone_count}."
        )
    recommendations = [
        FeasibilityRecommendation(
            code=RecommendationCode.EXTEND_DEADLINE,
            message=(
                f"Увеличьте срок минимум до {metrics.completion_minutes:.1f} мин "
                "для текущего рассчитанного плана."
            ),
            confidence=RecommendationConfidence.EXACT,
            parameter="completion_deadline_minutes",
            current_value=deadline,
            required_value=metrics.completion_minutes,
            unit="minutes",
        )
    ]
    if estimated_required <= eligible_drone_count:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.INCREASE_ACTIVE_DRONE_LIMIT,
                message=f"Разрешите оптимизатору использовать до {estimated_required} БВС.",
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="maximum_active_drones",
                current_value=request.maximum_active_drones or metrics.active_drone_count,
                required_value=estimated_required,
                unit="drones",
            )
        )
    else:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.ADD_CAPABLE_DRONE,
                message=(
                    f"Добавьте ориентировочно {estimated_required - eligible_drone_count} "
                    "подходящих БВС либо разделите работы на смены."
                ),
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="drones",
                current_value=eligible_drone_count,
                required_value=estimated_required,
                unit="drones",
            )
        )
    return FeasibilityReport(
        status=FeasibilityStatus.INFEASIBLE,
        message=(
            f"План не укладывается в срок: превышение {-deadline_margin:.1f} мин. {recommendation}"
        ),
        requested_deadline_minutes=deadline,
        maximum_active_drones=request.maximum_active_drones,
        used_drone_count=metrics.active_drone_count,
        estimated_required_drone_count=estimated_required,
        deadline_margin_minutes=round(deadline_margin, 2),
        recommendations=recommendations,
    )
