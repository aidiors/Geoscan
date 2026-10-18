from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations, pairwise
from math import hypot
from typing import Protocol

from shapely.geometry import LineString, MultiPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geoplan.coverage.models import WorkSegment
from geoplan.coverage.resolution import SurveyResolution
from geoplan.domain.airspace import AllowedAirspace, FlightObstacle, NoFlyZone
from geoplan.domain.diagnostics import SafetyAuditCheck, SafetyAuditReport
from geoplan.domain.enums import (
    AltitudeReference,
    ImportedGeometryType,
    MissionStageType,
    SafetyAuditStatus,
    SafetyCheckCode,
    ValidityMode,
)
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.domain.terrain import VerticalProfilePoint
from geoplan.flight.trajectory import mission_route_clock
from geoplan.navigation.geometry import (
    LocalProjection,
    polygon_from_coordinates,
    projection_for_coordinates,
)
from geoplan.navigation.routing import coordinate_distance_km, route_is_inside_allowed_airspaces
from geoplan.planning.models import PlanRequest
from geoplan.scheduling.vertical import vertical_interval_gap_m

COVERAGE_REQUIRED_PERCENT = 99.0
COVERAGE_ROUTE_CLEARANCE_M = 0.5
VERTICAL_OBSTACLE_CLEARANCE_M = 30.0
SCHEDULE_TOLERANCE_MINUTES = 0.05


@dataclass(frozen=True)
class ExportVerificationResult:
    consistent: bool
    message: str
    affected_ids: list[str]


class MissionExportVerifier(Protocol):
    def verify(self, missions: list[Mission]) -> ExportVerificationResult: ...


class PlanSafetyAuditor(Protocol):
    def audit(
        self,
        request: PlanRequest,
        segments: list[WorkSegment],
        missions: list[Mission],
    ) -> SafetyAuditReport: ...


@dataclass(frozen=True)
class IndependentPlanSafetyAuditor:
    export_verifier: MissionExportVerifier

    def audit(
        self,
        request: PlanRequest,
        segments: list[WorkSegment],
        missions: list[Mission],
    ) -> SafetyAuditReport:
        checks = [
            _no_fly_clearance_check(request.no_fly_zones, missions),
            _obstacle_clearance_check(request.obstacles, missions),
            _airspace_check(request, missions),
            _flight_envelope_check(request, missions),
            _schedule_integrity_check(request, missions),
            _drone_separation_check(request, missions),
            _vertical_separation_check(request, missions),
            _battery_reserve_check(request, missions),
            _alternate_reserve_check(request, missions),
            _vertical_profile_check(request, missions),
            _coverage_check(request, segments, missions),
            _survey_resolution_check(request, missions),
            _export_consistency_check(self.export_verifier.verify(missions)),
        ]
        status = (
            SafetyAuditStatus.FAILED
            if any(check.status is SafetyAuditStatus.FAILED for check in checks)
            else SafetyAuditStatus.PASSED
        )
        return SafetyAuditReport(
            status=status,
            audited_mission_count=len(missions),
            checks=checks,
            assumptions=_audit_assumptions(request),
        )


def _survey_resolution_check(request: PlanRequest, missions: list[Mission]) -> SafetyAuditCheck:
    survey = request.survey_parameters
    if survey is None:
        return _not_applicable(SafetyCheckCode.SURVEY_RESOLUTION, "Параметры съёмки не заданы.")
    policy = SurveyResolution()
    failed = [m for m in missions if not policy.allows(m, survey.sensor, survey.maximum_gsd_cm)]
    return SafetyAuditCheck(
        code=SafetyCheckCode.SURVEY_RESOLUTION,
        status=SafetyAuditStatus.FAILED if failed else SafetyAuditStatus.PASSED,
        measured_value=max((policy.gsd_cm(m, survey.sensor) for m in missions), default=0),
        required_value=survey.maximum_gsd_cm,
        unit="cm/pixel",
        affected_ids=[_mission_id(m) for m in failed],
        message=(
            "Разрешение съёмки проверено на итоговой высоте."
            if not failed
            else "Итоговая высота нарушает ограничение GSD."
        ),
    )


def _vertical_profile_check(
    request: PlanRequest,
    missions: list[Mission],
) -> SafetyAuditCheck:
    if request.altitude_reference is AltitudeReference.MSL:
        return _not_applicable(
            SafetyCheckCode.VERTICAL_PROFILE,
            "Режим следования рельефу не включён.",
        )
    drones = {drone.drone_id: drone for drone in request.drones}
    affected: list[str] = []
    minimum_agl = float("inf")
    for mission in missions:
        drone = drones[mission.drone_id]
        if not mission.vertical_profile or mission.minimum_agl_m is None:
            affected.append(_mission_id(mission))
            continue
        minimum_agl = min(minimum_agl, mission.minimum_agl_m)
        climb_limit = drone.maximum_climb_rate_mps
        descent_limit = drone.maximum_descent_rate_mps
        if (
            climb_limit is None
            or descent_limit is None
            or mission.maximum_required_climb_rate_mps is None
            or mission.maximum_required_descent_rate_mps is None
            or mission.maximum_required_climb_rate_mps > climb_limit + 1e-9
            or mission.maximum_required_descent_rate_mps > descent_limit + 1e-9
        ):
            affected.append(_mission_id(mission))
    if minimum_agl == float("inf"):
        minimum_agl = 0
    return _margin_check(
        SafetyCheckCode.VERTICAL_PROFILE,
        minimum_agl,
        request.mission_altitude_m,
        "m AGL",
        sorted(affected),
        "Минимальная высота вертикального профиля над рельефом",
    )


def _no_fly_clearance_check(
    zones: list[NoFlyZone],
    missions: list[Mission],
) -> SafetyAuditCheck:
    if not zones:
        return _not_applicable(
            SafetyCheckCode.NO_FLY_CLEARANCE,
            "Запретные зоны не заданы.",
        )
    relevant = [
        (mission, zone)
        for mission in missions
        for zone in zones
        if _no_fly_zone_applies_to_mission(zone, mission)
    ]
    if not relevant:
        return _not_applicable(
            SafetyCheckCode.NO_FLY_CLEARANCE,
            "Запретные зоны не действуют на высоте и во время рассчитанных миссий.",
        )
    minimum_margin = float("inf")
    affected: list[str] = []
    for mission in missions:
        mission_zones = [zone for candidate, zone in relevant if candidate is mission]
        if not mission_zones:
            continue
        mission_margin = min(_no_fly_margin_m(mission, zone) for zone in mission_zones)
        minimum_margin = min(minimum_margin, mission_margin)
        if mission_margin < 0:
            affected.append(_mission_id(mission))
    return _margin_check(
        SafetyCheckCode.NO_FLY_CLEARANCE,
        minimum_margin,
        0.0,
        "m",
        affected,
        "Минимальный горизонтальный запас до буфера запретной зоны",
    )


def _no_fly_zone_applies_to_mission(zone: NoFlyZone, mission: Mission) -> bool:
    if (
        zone.minimum_altitude_m is not None
        and zone.maximum_altitude_m is not None
        and not any(
            zone.minimum_altitude_m <= altitude <= zone.maximum_altitude_m
            for altitude in _mission_altitudes(mission, zone.altitude_reference)
        )
    ):
        return False
    validity = zone.validity
    if validity is None or validity.mode is ValidityMode.PERMANENT:
        return True
    if (
        mission.start_time is None
        or mission.end_time is None
        or validity.valid_from is None
        or validity.valid_to is None
    ):
        return True
    return mission.start_time < validity.valid_to and validity.valid_from < mission.end_time


def _no_fly_margin_m(mission: Mission, zone: NoFlyZone) -> float:
    projection = projection_for_coordinates([*mission.route, *zone.coordinates])
    route = LineString([projection.project(point) for point in mission.route])
    polygon = polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection)
    return float(route.distance(polygon) - zone.safety_margin_m)


def _obstacle_clearance_check(
    obstacles: list[FlightObstacle],
    missions: list[Mission],
) -> SafetyAuditCheck:
    relevant: list[tuple[Mission, FlightObstacle]] = [
        (mission, obstacle)
        for mission in missions
        for obstacle in obstacles
        if min(_mission_altitudes_msl(mission)) <= obstacle.height_m + VERTICAL_OBSTACLE_CLEARANCE_M
    ]
    if not relevant:
        return _not_applicable(
            SafetyCheckCode.OBSTACLE_CLEARANCE,
            "Препятствия на высоте заданий не требуют обхода по горизонтали.",
        )
    minimum_margin = float("inf")
    affected: list[str] = []
    for mission, obstacle in relevant:
        margin = _obstacle_margin_m(mission, obstacle)
        minimum_margin = min(minimum_margin, margin)
        if margin < 0:
            affected.append(_mission_id(mission))
    return _margin_check(
        SafetyCheckCode.OBSTACLE_CLEARANCE,
        minimum_margin,
        0.0,
        "m",
        affected,
        "Минимальный горизонтальный запас до защитного радиуса препятствия",
    )


def _obstacle_margin_m(mission: Mission, obstacle: FlightObstacle) -> float:
    projection = projection_for_coordinates([*mission.route, *obstacle.coordinates])
    route = LineString([projection.project(point) for point in mission.route])
    geometry = _obstacle_geometry(obstacle, projection)
    return float(route.distance(geometry) - obstacle.safety_radius_m)


def _obstacle_geometry(
    obstacle: FlightObstacle,
    projection: LocalProjection,
) -> BaseGeometry:
    projected = [projection.project(point) for point in obstacle.coordinates]
    if obstacle.geometry_type is ImportedGeometryType.POINT or len(projected) == 1:
        return MultiPoint(projected)
    if obstacle.geometry_type is ImportedGeometryType.LINE_STRING or len(projected) == 2:
        return LineString(projected)
    return polygon_from_coordinates(obstacle.coordinates, [], projection)


def _airspace_check(request: PlanRequest, missions: list[Mission]) -> SafetyAuditCheck:
    if not request.allowed_airspaces:
        return _not_applicable(
            SafetyCheckCode.AIRSPACE_COMPLIANCE,
            "Разрешённое воздушное пространство не задано.",
        )
    affected = [
        _mission_id(mission)
        for mission in missions
        if not _mission_is_inside_airspace(mission, request.allowed_airspaces)
    ]
    return SafetyAuditCheck(
        code=SafetyCheckCode.AIRSPACE_COMPLIANCE,
        status=SafetyAuditStatus.FAILED if affected else SafetyAuditStatus.PASSED,
        measured_value=float(len(affected)),
        required_value=0,
        unit="violations",
        message=(
            "Разрешённое пространство и временные окна соблюдены."
            if not affected
            else "Обнаружены маршруты вне разрешённого пространства или времени."
        ),
        affected_ids=affected,
    )


def _mission_is_inside_airspace(
    mission: Mission,
    airspaces: list[AllowedAirspace],
) -> bool:
    altitude_compatible = [
        airspace
        for airspace in airspaces
        if any(
            airspace.minimum_altitude_m <= altitude <= airspace.maximum_altitude_m
            for altitude in _mission_altitudes(mission, airspace.altitude_reference)
        )
    ]
    if not altitude_compatible:
        return False
    timed = [
        airspace
        for airspace in altitude_compatible
        if airspace.validity is not None and airspace.validity.mode is ValidityMode.TIME_WINDOW
    ]
    if not timed:
        return _vertical_route_inside_airspaces(mission, altitude_compatible)
    if mission.start_time is None or mission.end_time is None:
        return False
    boundaries = sorted(
        {
            mission.start_time,
            mission.end_time,
            *[
                boundary
                for airspace in timed
                for boundary in _validity_boundaries(airspace)
                if mission.start_time < boundary < mission.end_time
            ],
        }
    )
    for start, end in pairwise(boundaries):
        active = _active_airspaces(altitude_compatible, _midpoint(start, end))
        if not active or not _vertical_route_inside_airspaces(mission, active):
            return False
    return True


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


def _profile_altitude_m(point: VerticalProfilePoint, reference: AltitudeReference) -> float:
    return point.target_agl_m if reference is AltitudeReference.AGL else point.target_altitude_msl


def _vertical_route_inside_airspaces(
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
    return all(
        bool(compatible)
        and route_is_inside_allowed_airspaces(
            [start.coordinate, end.coordinate],
            compatible,
        )
        for start, end in pairwise(mission.vertical_profile)
        for compatible in [
            [
                airspace
                for airspace in airspaces
                if airspace.minimum_altitude_m
                <= _profile_altitude_m(start, airspace.altitude_reference)
                <= airspace.maximum_altitude_m
                and airspace.minimum_altitude_m
                <= _profile_altitude_m(end, airspace.altitude_reference)
                <= airspace.maximum_altitude_m
            ]
        ]
    )


def _validity_boundaries(airspace: AllowedAirspace) -> list[datetime]:
    validity = airspace.validity
    if validity is None or validity.valid_from is None or validity.valid_to is None:
        return []
    return [validity.valid_from, validity.valid_to]


def _active_airspaces(
    airspaces: list[AllowedAirspace],
    at_time: datetime,
) -> list[AllowedAirspace]:
    return [
        airspace
        for airspace in airspaces
        if airspace.validity is None
        or airspace.validity.mode is ValidityMode.PERMANENT
        or (
            airspace.validity.valid_from is not None
            and airspace.validity.valid_to is not None
            and airspace.validity.valid_from <= at_time <= airspace.validity.valid_to
        )
    ]


def _midpoint(start: datetime, end: datetime) -> datetime:
    return start + (end - start) / 2


def _schedule_integrity_check(
    request: PlanRequest,
    missions: list[Mission],
) -> SafetyAuditCheck:
    affected: set[str] = set()
    drones_by_id = {drone.drone_id: drone for drone in request.drones}
    for mission in missions:
        if mission.start_offset_minutes is None or mission.end_offset_minutes is None:
            affected.add(_mission_id(mission))
            continue
        scheduled_duration = mission.end_offset_minutes - mission.start_offset_minutes
        if abs(scheduled_duration - mission.duration_minutes) > SCHEDULE_TOLERANCE_MINUTES:
            affected.add(_mission_id(mission))
        if request.planning_start_time is not None and not _absolute_times_match(
            mission,
            request.planning_start_time,
        ):
            affected.add(_mission_id(mission))
    for first, second in combinations(missions, 2):
        if first.drone_id != second.drone_id:
            continue
        earlier, later = sorted(
            [first, second],
            key=lambda mission: mission.start_offset_minutes or 0,
        )
        if earlier.end_offset_minutes is None or later.start_offset_minutes is None:
            continue
        drone = drones_by_id[earlier.drone_id]
        required_turnaround = max(
            request.turnaround_minutes,
            drone.minimum_turnaround_minutes,
        )
        # Only absorb floating-point arithmetic noise, not the much larger
        # schedule-duration tolerance or a genuinely short turnaround.
        if earlier.end_offset_minutes + required_turnaround > later.start_offset_minutes + 1e-9:
            affected.update({_mission_id(earlier), _mission_id(later)})
    return SafetyAuditCheck(
        code=SafetyCheckCode.SCHEDULE_INTEGRITY,
        status=SafetyAuditStatus.FAILED if affected else SafetyAuditStatus.PASSED,
        measured_value=float(len(affected)),
        required_value=0,
        unit="violations",
        message=(
            "Расписание, длительности и интервалы обслуживания согласованы."
            if not affected
            else "Обнаружены несогласованные интервалы расписания."
        ),
        affected_ids=sorted(affected),
    )


def _absolute_times_match(mission: Mission, planning_start: datetime) -> bool:
    if (
        mission.start_offset_minutes is None
        or mission.end_offset_minutes is None
        or mission.start_time is None
        or mission.end_time is None
    ):
        return False
    start_delta = (mission.start_time - planning_start).total_seconds() / 60
    end_delta = (mission.end_time - planning_start).total_seconds() / 60
    return (
        abs(start_delta - mission.start_offset_minutes) <= SCHEDULE_TOLERANCE_MINUTES
        and abs(end_delta - mission.end_offset_minutes) <= SCHEDULE_TOLERANCE_MINUTES
    )


@dataclass(frozen=True)
class _TimedLeg:
    start_minutes: float
    end_minutes: float
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]


def _drone_separation_check(
    request: PlanRequest,
    missions: list[Mission],
) -> SafetyAuditCheck:
    minimum_guaranteed_distance = float("inf")
    affected: set[str] = set()
    compared = False
    for first, second in combinations(missions, 2):
        if first.drone_id == second.drone_id or not _missions_overlap(
            first,
            second,
            request.start_time_uncertainty_seconds / 60,
            request.speed_uncertainty_percent / 100,
        ):
            continue
        compared = True
        guaranteed_distance = _guaranteed_mission_distance_m(first, second, request)
        minimum_guaranteed_distance = min(minimum_guaranteed_distance, guaranteed_distance)
        if guaranteed_distance < request.minimum_separation_m:
            affected.update({_mission_id(first), _mission_id(second)})
    if not compared:
        return SafetyAuditCheck(
            code=SafetyCheckCode.DRONE_SEPARATION,
            status=SafetyAuditStatus.PASSED,
            required_value=request.minimum_separation_m,
            unit="m",
            message="Нет одновременных пар БВС, требующих горизонтального разведения.",
        )
    return _margin_check(
        SafetyCheckCode.DRONE_SEPARATION,
        minimum_guaranteed_distance,
        request.minimum_separation_m,
        "m",
        sorted(affected),
        "Гарантированная нижняя граница дистанции между БВС",
    )


def _vertical_separation_check(
    request: PlanRequest,
    missions: list[Mission],
) -> SafetyAuditCheck:
    layered_pairs = [
        (first, second)
        for first, second in combinations(missions, 2)
        if first.drone_id != second.drone_id
        and first.vertical_deconfliction_offset_m != second.vertical_deconfliction_offset_m
        and _missions_overlap(
            first,
            second,
            request.start_time_uncertainty_seconds / 60,
            request.speed_uncertainty_percent / 100,
        )
    ]
    if not layered_pairs:
        return _not_applicable(
            SafetyCheckCode.VERTICAL_SEPARATION,
            "Вертикальное эшелонирование для одновременных миссий не применялось.",
        )
    gaps = [
        (first, second, vertical_interval_gap_m(first, second)) for first, second in layered_pairs
    ]
    minimum_gap = min(gap for _, _, gap in gaps)
    affected = sorted(
        {
            mission_id
            for first, second, gap in gaps
            if gap < request.minimum_vertical_separation_m
            for mission_id in (_mission_id(first), _mission_id(second))
        }
    )
    return _margin_check(
        SafetyCheckCode.VERTICAL_SEPARATION,
        minimum_gap,
        request.minimum_vertical_separation_m,
        "m",
        affected,
        "Минимальный разрыв эшелонов по консервативным MSL-интервалам",
    )


def _missions_overlap(
    first: Mission,
    second: Mission,
    start_time_uncertainty_minutes: float,
    speed_uncertainty_fraction: float,
) -> bool:
    if (
        first.start_offset_minutes is None
        or first.end_offset_minutes is None
        or second.start_offset_minutes is None
        or second.end_offset_minutes is None
    ):
        return False
    return not (
        first.end_offset_minutes
        + start_time_uncertainty_minutes
        + _completion_uncertainty_minutes(first.duration_minutes, speed_uncertainty_fraction)
        < second.start_offset_minutes - start_time_uncertainty_minutes
        or second.end_offset_minutes
        + start_time_uncertainty_minutes
        + _completion_uncertainty_minutes(second.duration_minutes, speed_uncertainty_fraction)
        < first.start_offset_minutes - start_time_uncertainty_minutes
    )


def _guaranteed_mission_distance_m(
    first: Mission,
    second: Mission,
    request: PlanRequest,
) -> float:
    projection = projection_for_coordinates([*first.route, *second.route])
    first_legs = _timed_legs(first, projection)
    second_legs = _timed_legs(second, projection)
    first_speed = _maximum_leg_speed_m_per_minute(first_legs)
    second_speed = _maximum_leg_speed_m_per_minute(second_legs)
    uncertainty_minutes = request.start_time_uncertainty_seconds / 60
    timing_margin = (
        _maximum_leg_speed_m_per_minute(first_legs) + _maximum_leg_speed_m_per_minute(second_legs)
    ) * uncertainty_minutes
    first_legs = _extend_for_start_uncertainty(first_legs, uncertainty_minutes)
    second_legs = _extend_for_start_uncertainty(second_legs, uncertainty_minutes)
    speed_uncertainty_fraction = request.speed_uncertainty_percent / 100
    first_legs = _extend_for_completion_uncertainty(
        first_legs,
        _completion_uncertainty_minutes(first.duration_minutes, speed_uncertainty_fraction),
    )
    second_legs = _extend_for_completion_uncertainty(
        second_legs,
        _completion_uncertainty_minutes(second.duration_minutes, speed_uncertainty_fraction),
    )
    leg_pairs = [(first_leg, second_leg) for first_leg in first_legs for second_leg in second_legs]
    if vertical_interval_gap_m(first, second) >= request.minimum_vertical_separation_m:
        first_phases = _extend_for_completion_uncertainty(
            _extend_for_start_uncertainty(
                _vertical_phase_legs(first, projection),
                uncertainty_minutes,
            ),
            _completion_uncertainty_minutes(first.duration_minutes, speed_uncertainty_fraction),
        )
        second_phases = _extend_for_completion_uncertainty(
            _extend_for_start_uncertainty(
                _vertical_phase_legs(second, projection),
                uncertainty_minutes,
            ),
            _completion_uncertainty_minutes(second.duration_minutes, speed_uncertainty_fraction),
        )
        leg_pairs = [
            *((first_leg, second_leg) for first_leg in first_phases for second_leg in second_legs),
            *((first_leg, second_leg) for first_leg in first_legs for second_leg in second_phases),
        ]
    robust_distances = [
        distance
        - speed_uncertainty_fraction
        * (
            first_speed * max(0, overlap_end - (first.start_offset_minutes or 0))
            + second_speed * max(0, overlap_end - (second.start_offset_minutes or 0))
        )
        for first_leg, second_leg in leg_pairs
        if (measurement := _minimum_leg_distance_m(first_leg, second_leg)) is not None
        for distance, overlap_end in [measurement]
    ]
    minimum_distance = min(robust_distances) if robust_distances else float("inf")
    return minimum_distance - 2 * request.position_uncertainty_m - timing_margin


def _maximum_leg_speed_m_per_minute(legs: list[_TimedLeg]) -> float:
    return max((hypot(*_leg_velocity(leg)) for leg in legs), default=0.0)


def _completion_uncertainty_minutes(
    duration_minutes: float,
    speed_uncertainty_fraction: float,
) -> float:
    if speed_uncertainty_fraction <= 0:
        return 0
    return duration_minutes * speed_uncertainty_fraction / (1 - speed_uncertainty_fraction)


def _extend_for_completion_uncertainty(
    legs: list[_TimedLeg],
    uncertainty_minutes: float,
) -> list[_TimedLeg]:
    if not legs or uncertainty_minutes <= 0:
        return legs
    last = legs[-1]
    return [
        *legs,
        _TimedLeg(
            start_minutes=last.end_minutes,
            end_minutes=last.end_minutes + uncertainty_minutes,
            start_xy=last.end_xy,
            end_xy=last.end_xy,
        ),
    ]


def _extend_for_start_uncertainty(
    legs: list[_TimedLeg],
    uncertainty_minutes: float,
) -> list[_TimedLeg]:
    if not legs or uncertainty_minutes <= 0:
        return legs
    first = legs[0]
    last = legs[-1]
    return [
        _TimedLeg(
            start_minutes=first.start_minutes - uncertainty_minutes,
            end_minutes=first.start_minutes,
            start_xy=first.start_xy,
            end_xy=first.start_xy,
        ),
        *legs,
        _TimedLeg(
            start_minutes=last.end_minutes,
            end_minutes=last.end_minutes + uncertainty_minutes,
            start_xy=last.end_xy,
            end_xy=last.end_xy,
        ),
    ]


def _timed_legs(mission: Mission, projection: LocalProjection) -> list[_TimedLeg]:
    if mission.start_offset_minutes is None or mission.end_offset_minutes is None:
        return []
    offsets = mission_route_clock(mission).offsets_minutes
    base = mission.start_offset_minutes
    horizontal_start = base + mission.vertical_climb_duration_minutes
    legs = []
    if mission.vertical_climb_duration_minutes > 0:
        departure = projection.project(mission.route[0])
        legs.append(_TimedLeg(base, horizontal_start, departure, departure))
    legs.extend(
        _TimedLeg(
            horizontal_start + offsets[i],
            horizontal_start + offsets[i + 1],
            projection.project(a),
            projection.project(b),
        )
        for i, (a, b) in enumerate(pairwise(mission.route))
    )
    if mission.vertical_descent_duration_minutes > 0:
        landing = projection.project(mission.route[-1])
        legs.append(
            _TimedLeg(
                base + mission.duration_minutes - mission.vertical_descent_duration_minutes,
                base + mission.duration_minutes,
                landing,
                landing,
            )
        )
    return legs


def _vertical_phase_legs(
    mission: Mission,
    projection: LocalProjection,
) -> list[_TimedLeg]:
    if mission.start_offset_minutes is None or mission.end_offset_minutes is None:
        return []
    phases: list[_TimedLeg] = []
    if mission.vertical_climb_duration_minutes > 0:
        departure = projection.project(mission.route[0])
        phases.append(
            _TimedLeg(
                mission.start_offset_minutes,
                mission.start_offset_minutes + mission.vertical_climb_duration_minutes,
                departure,
                departure,
            )
        )
    if mission.vertical_descent_duration_minutes > 0:
        landing = projection.project(mission.route[-1])
        phases.append(
            _TimedLeg(
                mission.end_offset_minutes - mission.vertical_descent_duration_minutes,
                mission.end_offset_minutes,
                landing,
                landing,
            )
        )
    return phases


def _minimum_leg_distance_m(
    first: _TimedLeg,
    second: _TimedLeg,
) -> tuple[float, float] | None:
    overlap_start = max(first.start_minutes, second.start_minutes)
    overlap_end = min(first.end_minutes, second.end_minutes)
    if overlap_end < overlap_start:
        return None
    first_position = _leg_position(first, overlap_start)
    second_position = _leg_position(second, overlap_start)
    relative_start = (
        first_position[0] - second_position[0],
        first_position[1] - second_position[1],
    )
    relative_velocity = (
        _leg_velocity(first)[0] - _leg_velocity(second)[0],
        _leg_velocity(first)[1] - _leg_velocity(second)[1],
    )
    duration = overlap_end - overlap_start
    speed_squared = relative_velocity[0] ** 2 + relative_velocity[1] ** 2
    closest_time = 0.0
    if speed_squared > 1e-12:
        closest_time = min(
            duration,
            max(
                0.0,
                -(
                    relative_start[0] * relative_velocity[0]
                    + relative_start[1] * relative_velocity[1]
                )
                / speed_squared,
            ),
        )
    return (
        hypot(
            relative_start[0] + relative_velocity[0] * closest_time,
            relative_start[1] + relative_velocity[1] * closest_time,
        ),
        overlap_end,
    )


def _leg_position(leg: _TimedLeg, at_minutes: float) -> tuple[float, float]:
    duration = leg.end_minutes - leg.start_minutes
    if duration <= 0:
        return leg.end_xy
    progress = min(1.0, max(0.0, (at_minutes - leg.start_minutes) / duration))
    return (
        leg.start_xy[0] + (leg.end_xy[0] - leg.start_xy[0]) * progress,
        leg.start_xy[1] + (leg.end_xy[1] - leg.start_xy[1]) * progress,
    )


def _leg_velocity(leg: _TimedLeg) -> tuple[float, float]:
    duration = leg.end_minutes - leg.start_minutes
    if duration <= 0:
        return (0.0, 0.0)
    return (
        (leg.end_xy[0] - leg.start_xy[0]) / duration,
        (leg.end_xy[1] - leg.start_xy[1]) / duration,
    )


def _battery_reserve_check(request: PlanRequest, missions: list[Mission]) -> SafetyAuditCheck:
    reserve_by_drone = {drone.drone_id: drone.battery_reserve_percent for drone in request.drones}
    margins = [
        (mission, mission.remaining_battery_percent - reserve_by_drone[mission.drone_id])
        for mission in missions
        if mission.remaining_battery_percent is not None
    ]
    if len(margins) != len(missions):
        missing = [
            _mission_id(mission)
            for mission in missions
            if mission.remaining_battery_percent is None
        ]
        return SafetyAuditCheck(
            code=SafetyCheckCode.BATTERY_RESERVE,
            status=SafetyAuditStatus.FAILED,
            message="Для одного или нескольких вылетов не рассчитан остаток заряда АКБ.",
            affected_ids=missing,
        )
    minimum = min(margin for _, margin in margins)
    affected = [_mission_id(mission) for mission, margin in margins if margin < 0]
    return _margin_check(
        SafetyCheckCode.BATTERY_RESERVE,
        minimum,
        0.0,
        "percentage_points",
        affected,
        "Минимальный остаток сверх обязательного резерва АКБ",
    )


def _flight_envelope_check(request: PlanRequest, missions: list[Mission]) -> SafetyAuditCheck:
    drones = {drone.drone_id: drone for drone in request.drones}
    affected: list[str] = []
    applicable = False
    for mission in missions:
        drone = drones[mission.drone_id]
        margins: list[float] = []
        minimum_altitude = drone.minimum_altitude_for(mission.altitude_reference)
        maximum_altitude = drone.maximum_altitude_for(mission.altitude_reference)
        if minimum_altitude is not None:
            applicable = True
            margins.append(mission.altitude_m - minimum_altitude)
        if maximum_altitude is not None:
            applicable = True
            margins.append(maximum_altitude - mission.altitude_m)
        if drone.minimum_cruise_speed_kmh is not None:
            applicable = True
            margins.append(mission.cruise_speed_kmh - drone.minimum_cruise_speed_kmh)
        if drone.maximum_cruise_speed_kmh is not None:
            applicable = True
            margins.append(drone.maximum_cruise_speed_kmh - mission.cruise_speed_kmh)
        if drone.maximum_route_distance_km is not None:
            applicable = True
            margins.append(drone.maximum_route_distance_km - mission.distance_km)
        if drone.maximum_control_range_km is not None:
            applicable = True
            maximum_radius_km = max(
                coordinate_distance_km(mission.route[0], point) for point in mission.route
            )
            margins.append(drone.maximum_control_range_km - maximum_radius_km)
        if request.ambient_temperature_c is not None:
            if drone.minimum_operating_temperature_c is not None:
                applicable = True
                margins.append(
                    request.ambient_temperature_c - drone.minimum_operating_temperature_c
                )
            if drone.maximum_operating_temperature_c is not None:
                applicable = True
                margins.append(
                    drone.maximum_operating_temperature_c - request.ambient_temperature_c
                )
        applicable = True
        if mission.starting_battery_percent > drone.initial_charge_percent + 1e-9:
            margins.append(drone.initial_charge_percent - mission.starting_battery_percent)
        if any(margin < 0 for margin in margins):
            affected.append(_mission_id(mission))
    if not applicable:
        return _not_applicable(
            SafetyCheckCode.FLIGHT_ENVELOPE,
            "Для БВС не заданы дополнительные границы эксплуатационного конверта.",
        )
    return SafetyAuditCheck(
        code=SafetyCheckCode.FLIGHT_ENVELOPE,
        status=SafetyAuditStatus.FAILED if affected else SafetyAuditStatus.PASSED,
        measured_value=float(len(affected)),
        required_value=0,
        unit="violations",
        message=(
            "Маршруты находятся в пределах эксплуатационного конверта."
            if not affected
            else "Обнаружен выход за границы эксплуатационного конверта."
        ),
        affected_ids=affected,
    )


def _alternate_reserve_check(request: PlanRequest, missions: list[Mission]) -> SafetyAuditCheck:
    if not request.alternate_landing_sites:
        return _not_applicable(
            SafetyCheckCode.ALTERNATE_RESERVE,
            "Резервные площадки не заданы.",
        )
    missing = [
        _mission_id(mission)
        for mission in missions
        if mission.minimum_alternate_margin_percent is None
        or mission.alternate_evaluated_checkpoint_count is None
        or mission.alternate_certification_error_minutes is None
    ]
    if missing:
        return SafetyAuditCheck(
            code=SafetyCheckCode.ALTERNATE_RESERVE,
            status=SafetyAuditStatus.FAILED,
            message="Для одного или нескольких вылетов не рассчитан запас аварийного ухода.",
            affected_ids=missing,
        )
    minimum = min(
        mission.minimum_alternate_margin_percent
        for mission in missions
        if mission.minimum_alternate_margin_percent is not None
    )
    affected = [
        _mission_id(mission)
        for mission in missions
        if mission.minimum_alternate_margin_percent is not None
        and mission.minimum_alternate_margin_percent < 0
    ]
    return _margin_check(
        SafetyCheckCode.ALTERNATE_RESERVE,
        minimum,
        0.0,
        "percentage_points",
        affected,
        "Минимальный запас аварийного ухода сверх резерва АКБ",
    )


def _coverage_check(
    request: PlanRequest,
    segments: list[WorkSegment],
    missions: list[Mission],
) -> SafetyAuditCheck:
    if any(segment.coverage_pattern is not None for segment in segments):
        from geoplan.safety.freeform import freeform_coverage_check

        return freeform_coverage_check(request, segments, missions)
    if not request.coverage_areas:
        return _not_applicable(
            SafetyCheckCode.COVERAGE_COMPLETENESS,
            "План не содержит зон площадного покрытия.",
        )
    if request.survey_parameters is None:
        return SafetyAuditCheck(
            code=SafetyCheckCode.COVERAGE_COMPLETENESS,
            status=SafetyAuditStatus.FAILED,
            message="Для плана покрытия не заданы параметры съёмки.",
        )
    sensor = request.survey_parameters.sensor
    footprint_width_m = min(
        request.mission_altitude_m * sensor.sensor_width_mm / sensor.focal_length_mm,
        request.survey_parameters.strip_width_m,
    )
    segment_by_id = {segment.segment_id: segment for segment in segments}
    ratios = [
        _coverage_ratio(
            area.area_id,
            area.coordinates,
            area.interior_rings,
            segment_by_id,
            missions,
            footprint_width_m,
            request.no_fly_zones,
        )
        for area in request.coverage_areas
    ]
    minimum = min(ratios) if ratios else 0.0
    affected = [
        area.area_id
        for area, ratio in zip(request.coverage_areas, ratios, strict=True)
        if ratio < COVERAGE_REQUIRED_PERCENT
    ]
    return SafetyAuditCheck(
        code=SafetyCheckCode.COVERAGE_COMPLETENESS,
        status=SafetyAuditStatus.FAILED if affected else SafetyAuditStatus.PASSED,
        measured_value=round(minimum, 2),
        required_value=COVERAGE_REQUIRED_PERCENT,
        unit="percent",
        message=(
            "Заданные области покрыты съёмочными маршрутами."
            if not affected
            else "Расчётная полнота покрытия ниже допустимого порога."
        ),
        affected_ids=affected,
    )


def _coverage_ratio(
    area_id: str,
    area_coordinates: list[Coordinate],
    interior_rings: list[list[Coordinate]],
    segments: dict[str, WorkSegment],
    missions: list[Mission],
    footprint_width_m: float,
    exclusions: list[NoFlyZone],
) -> float:
    relevant = [
        mission
        for mission in missions
        if mission.segment_id in segments
        and segments[mission.segment_id].coverage_area_id == area_id
    ]
    coordinates = [
        *area_coordinates,
        *(point for ring in interior_rings for point in ring),
        *(point for mission in relevant for point in mission.route),
        *(point for zone in exclusions for point in zone.coordinates),
        *(point for zone in exclusions for ring in zone.interior_rings for point in ring),
    ]
    if not relevant or not coordinates:
        return 0.0
    projection = projection_for_coordinates(coordinates)
    target = polygon_from_coordinates(area_coordinates, interior_rings, projection)
    exclusion_geometries = [
        polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
            zone.safety_margin_m + COVERAGE_ROUTE_CLEARANCE_M,
            join_style="mitre",
        )
        for zone in exclusions
    ]
    if exclusion_geometries:
        target = target.difference(unary_union(exclusion_geometries))
    swaths: list[BaseGeometry] = []
    for mission in relevant:
        segment = segments[mission.segment_id]
        if segment.strip_spacing_m is None:
            continue
        for stage in mission.stages:
            if stage.stage_type is MissionStageType.SURVEY and len(stage.coordinates) >= 2:
                swaths.append(
                    LineString([projection.project(point) for point in stage.coordinates]).buffer(
                        footprint_width_m / 2 + 0.5, cap_style="square"
                    )
                )

    if not swaths or target.area <= 0:
        return 0.0
    covered = unary_union(swaths).intersection(target).area
    return min(100.0, float(covered / target.area * 100))


def _export_consistency_check(result: ExportVerificationResult) -> SafetyAuditCheck:
    return SafetyAuditCheck(
        code=SafetyCheckCode.EXPORT_CONSISTENCY,
        status=SafetyAuditStatus.PASSED if result.consistent else SafetyAuditStatus.FAILED,
        measured_value=0.0 if result.consistent else float(len(result.affected_ids)),
        required_value=0,
        unit="mismatches",
        message=result.message,
        affected_ids=result.affected_ids,
    )


def _margin_check(
    code: SafetyCheckCode,
    measured: float,
    required: float,
    unit: str,
    affected: list[str],
    label: str,
) -> SafetyAuditCheck:
    passed = measured >= required and not affected
    return SafetyAuditCheck(
        code=code,
        status=SafetyAuditStatus.PASSED if passed else SafetyAuditStatus.FAILED,
        measured_value=round(measured, 3),
        required_value=required,
        unit=unit,
        message=f"{label}: {measured:.2f} {_display_unit(unit)}.",
        affected_ids=affected,
    )


def _not_applicable(code: SafetyCheckCode, message: str) -> SafetyAuditCheck:
    return SafetyAuditCheck(
        code=code,
        status=SafetyAuditStatus.NOT_APPLICABLE,
        message=message,
    )


def _display_unit(unit: str) -> str:
    if unit == "m":
        return "м"
    if unit == "m AGL":
        return "м AGL"
    if unit == "percentage_points":
        return "п.п"
    return unit


def _mission_id(mission: Mission) -> str:
    return f"{mission.drone_id}:{mission.segment_id}"


def _audit_assumptions(request: PlanRequest) -> list[str]:
    if request.altitude_reference is AltitudeReference.AGL:
        source_description = (
            "значения GeoTIFF/DSM по CRS и ближайшей ячейке"
            if request.terrain_source is not None
            and request.terrain_source.source_type == "geotiff"
            else "переданные высоты рельефа при интерполяции по обратным расстояниям"
        )
        assumptions = [
            f"Профиль AGL использует {source_description} и постоянный гарантированный просвет.",
            "Расход энергии по вертикали оценён консервативно по времени, без модели двигателя.",
            "Экспорт в системе высот рельефа требует поддержки протокола автопилотом "
            "и данных рельефа на борту.",
        ]
        if request.terrain_vertical_uncertainty_m > 0:
            assumptions.append(
                "Плановая высота включает запас на погрешность рельефа "
                f"{request.terrain_vertical_uncertainty_m:g} м."
            )
    else:
        assumptions = ["Режим MSL не моделирует рельеф; высота задания остаётся постоянной."]
    if (
        request.position_uncertainty_m > 0
        or request.start_time_uncertainty_seconds > 0
        or request.speed_uncertainty_percent > 0
    ):
        assumptions.append(
            "Разведение робастно к независимым ограниченным ошибкам "
            "позиции, времени старта и скорости."
        )
    else:
        assumptions.append("Погрешности GNSS, времени старта и скорости не моделируются.")
    if request.wind is not None:
        assumptions.append("Ветер постоянен по времени и пространству на всём плане.")
    assumptions.append(
        "Каждый повторный вылет начинается при заданном текущем заряде; "
        "перезарядка или замена АКБ входит в межполётное обслуживание."
    )
    assumptions.append(
        "Доступная энергия масштабируется по здоровью АКБ и коэффициенту "
        "энергопотребления; это консервативная модель, не телеметрическая кривая разряда."
    )
    if request.minimum_turn_radius_m == 0:
        assumptions.append(
            "Минимальный радиус разворота отключён и маршрут проверяется как ломаная."
        )
    if request.alternate_landing_sites:
        assumptions.append(
            "Достижимость резервной площадки проверяется адаптивным уточнением интервалов "
            "при консервативной оценке времени между контрольными точками."
        )
    if request.maximum_vertical_deconfliction_m > 0:
        assumptions.append(
            "Вертикальное разведение использует консервативные диапазоны MSL всего маршрута; "
            "конфликты площадок и аварийных коридоров разводятся по времени."
        )
    return assumptions
