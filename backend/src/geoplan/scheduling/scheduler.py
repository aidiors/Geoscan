from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise
from math import ceil, hypot
from typing import Protocol

from geoplan.domain.enums import ScheduleAlgorithm, ScheduleConflictType
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.flight.trajectory import mission_route_clock
from geoplan.navigation.geometry import LocalProjection, projection_for_coordinates
from geoplan.scheduling.models import (
    MissionScheduleRequest,
    MissionScheduleResult,
    ResolvedScheduleConflict,
    ScheduleTimeWindow,
    VerticalLayerOption,
)
from geoplan.scheduling.vertical import (
    apply_vertical_layer,
    vertical_interval_gap_m,
)

SCHEDULE_OUTPUT_RESOLUTION_MINUTES = 0.001


class MissionScheduler(Protocol):
    def schedule(self, request: MissionScheduleRequest) -> MissionScheduleResult: ...


class ScheduleWindowUnavailableError(RuntimeError):
    def __init__(self, segment_id: str) -> None:
        self.segment_id = segment_id
        super().__init__(f"Миссия {segment_id} не помещается в разрешённое временное окно.")


@dataclass(frozen=True)
class TimedLeg:
    start_minutes: float
    end_minutes: float
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]


@dataclass(frozen=True)
class ScheduledEntry:
    mission: Mission
    start_minutes: float
    end_minutes: float


@dataclass(frozen=True)
class ClosestApproach:
    offset_minutes: float
    first_xy: tuple[float, float]
    second_xy: tuple[float, float]
    distance_m: float
    required_distance_m: float


@dataclass(frozen=True)
class ConflictDetection:
    conflict_type: ScheduleConflictType
    conflicting_drone_id: str
    conflicting_segment_id: str
    coordinate: Coordinate
    offset_minutes: float
    distance_m: float
    required_distance_m: float


@dataclass(frozen=True)
class ScheduleChoice:
    mission: Mission
    option: VerticalLayerOption
    start_minutes: float
    conflicts: list[ConflictDetection]
    conflict_delay_minutes: float


@dataclass(frozen=True)
class ConflictAwareMissionScheduler:
    maximum_delay_iterations: int = 100_000

    def schedule(self, request: MissionScheduleRequest) -> MissionScheduleResult:
        queues = _mission_queues(request.missions)
        ready_at = {drone_id: 0.0 for drone_id in queues}
        scheduled: list[ScheduledEntry] = []
        resolved_conflicts: list[ResolvedScheduleConflict] = []
        delay_step_minutes = request.delay_step_seconds / 60
        while any(queues.values()):
            drone_id = min(
                (candidate for candidate, queue in queues.items() if queue),
                key=lambda candidate: (ready_at[candidate], candidate),
            )
            original_mission = queues[drone_id].pop(0)
            choice, base_conflicts = _best_schedule_choice(
                original_mission,
                request.vertical_layers_by_segment_id.get(original_mission.segment_id),
                ready_at[drone_id],
                scheduled,
                request,
                delay_step_minutes,
                self.maximum_delay_iterations,
            )
            mission = choice.mission
            start_minutes = choice.start_minutes
            conflicts = (
                base_conflicts
                if choice.option.offset_m > 0 and choice.conflict_delay_minutes == 0
                else choice.conflicts
            )
            conflict_delay_minutes = choice.conflict_delay_minutes
            end_minutes = start_minutes + mission.duration_minutes
            start_time = (
                request.planning_start_time + timedelta(minutes=start_minutes)
                if request.planning_start_time is not None
                else None
            )
            end_time = (
                request.planning_start_time + timedelta(minutes=end_minutes)
                if request.planning_start_time is not None
                else None
            )
            scheduled_mission = mission.model_copy(
                update={
                    "start_offset_minutes": round(start_minutes, 3),
                    "end_offset_minutes": round(end_minutes, 3),
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )
            scheduled.append(
                ScheduledEntry(
                    mission=scheduled_mission,
                    start_minutes=start_minutes,
                    end_minutes=end_minutes,
                )
            )
            turnaround_minutes = max(
                request.turnaround_minutes,
                request.turnaround_minutes_by_drone.get(drone_id, 0),
            )
            ready_at[drone_id] = _ceil_output_minute(end_minutes + turnaround_minutes)
            resolved_conflicts.extend(
                ResolvedScheduleConflict(
                    conflict_type=conflict.conflict_type,
                    delayed_drone_id=mission.drone_id,
                    delayed_segment_id=mission.segment_id,
                    conflicting_drone_id=conflict.conflicting_drone_id,
                    conflicting_segment_id=conflict.conflicting_segment_id,
                    coordinate=conflict.coordinate,
                    conflict_offset_minutes=round(max(0, conflict.offset_minutes), 3),
                    nominal_distance_m=round(conflict.distance_m, 3),
                    required_distance_m=round(conflict.required_distance_m, 3),
                    applied_delay_minutes=round(conflict_delay_minutes, 3),
                    applied_vertical_offset_m=choice.option.offset_m,
                )
                for conflict in conflicts
            )
        ordered = sorted(
            scheduled,
            key=lambda entry: (
                entry.start_minutes,
                entry.mission.drone_id,
                entry.mission.segment_id,
            ),
        )
        return MissionScheduleResult(
            missions=[entry.mission for entry in ordered],
            makespan_minutes=round(max(entry.end_minutes for entry in ordered), 3),
            resolved_conflict_count=len(resolved_conflicts),
            resolved_conflicts=resolved_conflicts,
            algorithm=ScheduleAlgorithm.PRIORITY_DECONFLICTION,
        )


def _mission_queues(missions: list[Mission]) -> dict[str, list[Mission]]:
    queues: dict[str, list[Mission]] = {}
    for mission in missions:
        queues.setdefault(mission.drone_id, []).append(mission)
    return queues


def _ceil_output_minute(value: float) -> float:
    return (
        ceil(value / SCHEDULE_OUTPUT_RESOLUTION_MINUTES - 1e-9) * SCHEDULE_OUTPUT_RESOLUTION_MINUTES
    )


def _best_schedule_choice(
    mission: Mission,
    options: list[VerticalLayerOption] | None,
    earliest_start_minutes: float,
    scheduled: list[ScheduledEntry],
    request: MissionScheduleRequest,
    delay_step_minutes: float,
    maximum_iterations: int,
) -> tuple[ScheduleChoice, list[ConflictDetection]]:
    available = options or [
        VerticalLayerOption(
            offset_m=0,
            extra_energy_equivalent_minutes=0,
            extra_battery_percent=0,
        )
    ]
    choices: list[ScheduleChoice] = []
    for option in available:
        adjusted = apply_vertical_layer(mission, option)
        start, conflicts, delay = _safe_start_time(
            adjusted,
            earliest_start_minutes,
            scheduled,
            request.minimum_separation_m,
            request.minimum_vertical_separation_m,
            request.position_uncertainty_m,
            request.start_time_uncertainty_seconds / 60,
            request.speed_uncertainty_percent / 100,
            request.flight_site_occupancy_seconds / 60,
            delay_step_minutes,
            maximum_iterations,
            request.time_windows_by_segment_id.get(mission.segment_id),
        )
        choices.append(ScheduleChoice(adjusted, option, start, conflicts, delay))
    best = min(
        choices,
        key=lambda choice: (
            choice.start_minutes + choice.mission.duration_minutes,
            choice.start_minutes,
            choice.option.offset_m,
            choice.option.extra_energy_equivalent_minutes,
        ),
    )
    base_conflicts = next(
        (choice.conflicts for choice in choices if choice.option.offset_m == 0),
        [],
    )
    return best, base_conflicts


def _safe_start_time(
    mission: Mission,
    earliest_start_minutes: float,
    scheduled: list[ScheduledEntry],
    minimum_separation_m: float,
    minimum_vertical_separation_m: float,
    position_uncertainty_m: float,
    start_time_uncertainty_minutes: float,
    speed_uncertainty_fraction: float,
    flight_site_occupancy_minutes: float,
    delay_step_minutes: float,
    maximum_iterations: int,
    time_windows: list[ScheduleTimeWindow] | None,
) -> tuple[float, list[ConflictDetection], float]:
    start_minutes = earliest_start_minutes
    baseline_start_minutes: float | None = None
    conflicting_missions: dict[tuple[str, str, ScheduleConflictType], ConflictDetection] = {}
    for _ in range(maximum_iterations):
        allowed_start = _next_allowed_start(
            mission,
            start_minutes,
            time_windows,
            start_time_uncertainty_minutes,
            speed_uncertainty_fraction,
        )
        if allowed_start is None:
            raise ScheduleWindowUnavailableError(mission.segment_id)
        start_minutes = allowed_start
        if baseline_start_minutes is None:
            baseline_start_minutes = start_minutes
        conflict = next(
            (
                detection
                for entry in scheduled
                if entry.mission.drone_id != mission.drone_id
                for detection in [
                    _mission_conflict(
                        mission,
                        start_minutes,
                        entry,
                        minimum_separation_m,
                        minimum_vertical_separation_m,
                        position_uncertainty_m,
                        start_time_uncertainty_minutes,
                        speed_uncertainty_fraction,
                        flight_site_occupancy_minutes,
                    )
                ]
                if detection is not None
            ),
            None,
        )
        if conflict is None:
            return (
                start_minutes,
                list(conflicting_missions.values()),
                start_minutes - baseline_start_minutes,
            )
        key = (
            conflict.conflicting_drone_id,
            conflict.conflicting_segment_id,
            conflict.conflict_type,
        )
        conflicting_missions.setdefault(key, conflict)
        start_minutes += delay_step_minutes
    raise RuntimeError("Безопасное расписание не найдено в пределах допустимого поиска.")


def _next_allowed_start(
    mission: Mission,
    earliest_start_minutes: float,
    time_windows: list[ScheduleTimeWindow] | None,
    start_time_uncertainty_minutes: float,
    speed_uncertainty_fraction: float,
) -> float | None:
    if time_windows is None:
        return earliest_start_minutes
    for window in time_windows:
        candidate = max(
            earliest_start_minutes,
            window.start_offset_minutes + start_time_uncertainty_minutes,
        )
        if (
            candidate
            + mission.duration_minutes
            + _completion_uncertainty_minutes(mission.duration_minutes, speed_uncertainty_fraction)
            + start_time_uncertainty_minutes
            <= window.end_offset_minutes + 1e-9
        ):
            return candidate
    return None


def _mission_conflict(
    mission: Mission,
    start_minutes: float,
    scheduled: ScheduledEntry,
    minimum_separation_m: float,
    minimum_vertical_separation_m: float,
    position_uncertainty_m: float,
    start_time_uncertainty_minutes: float,
    speed_uncertainty_fraction: float,
    flight_site_occupancy_minutes: float,
) -> ConflictDetection | None:
    site_conflict = _flight_site_conflict(
        mission,
        start_minutes,
        scheduled,
        minimum_separation_m,
        flight_site_occupancy_minutes,
        start_time_uncertainty_minutes,
        speed_uncertainty_fraction,
    )
    if site_conflict is not None:
        return site_conflict
    vertically_separated = (
        vertical_interval_gap_m(mission, scheduled.mission) >= minimum_vertical_separation_m
    )
    end_minutes = start_minutes + mission.duration_minutes
    completion_uncertainty_minutes = _completion_uncertainty_minutes(
        mission.duration_minutes,
        speed_uncertainty_fraction,
    )
    scheduled_completion_uncertainty_minutes = _completion_uncertainty_minutes(
        scheduled.mission.duration_minutes,
        speed_uncertainty_fraction,
    )
    nominal_routes_overlap = not (
        end_minutes + start_time_uncertainty_minutes + completion_uncertainty_minutes
        < scheduled.start_minutes - start_time_uncertainty_minutes
        or scheduled.end_minutes
        + start_time_uncertainty_minutes
        + scheduled_completion_uncertainty_minutes
        < start_minutes - start_time_uncertainty_minutes
    )
    has_emergency_corridor = bool(
        mission.critical_alternate_route or scheduled.mission.critical_alternate_route
    )
    if not nominal_routes_overlap and not has_emergency_corridor:
        return None
    all_coordinates = [*mission.route, *scheduled.mission.route]
    all_coordinates.extend(mission.critical_alternate_route)
    all_coordinates.extend(scheduled.mission.critical_alternate_route)
    projection = projection_for_coordinates(all_coordinates)
    candidate_legs = _timed_legs(mission, start_minutes, projection)
    scheduled_legs = _timed_legs(scheduled.mission, scheduled.start_minutes, projection)
    candidate_speed = _maximum_leg_speed_m_per_minute(candidate_legs)
    scheduled_speed = _maximum_leg_speed_m_per_minute(scheduled_legs)
    robust_separation_m = (
        minimum_separation_m
        + 2 * position_uncertainty_m
        + (candidate_speed + scheduled_speed) * start_time_uncertainty_minutes
        + (candidate_speed + scheduled_speed) * SCHEDULE_OUTPUT_RESOLUTION_MINUTES
    )
    if nominal_routes_overlap:
        extended_candidate = _extend_for_start_uncertainty(
            _extend_for_completion_uncertainty(
                candidate_legs,
                completion_uncertainty_minutes,
            ),
            start_time_uncertainty_minutes,
        )
        extended_scheduled = _extend_for_start_uncertainty(
            _extend_for_completion_uncertainty(
                scheduled_legs,
                scheduled_completion_uncertainty_minutes,
            ),
            start_time_uncertainty_minutes,
        )
        leg_pairs = [(extended_candidate, extended_scheduled)]
        if vertically_separated:
            candidate_phases = _extend_for_start_uncertainty(
                _extend_for_completion_uncertainty(
                    _vertical_phase_legs(mission, start_minutes, projection),
                    completion_uncertainty_minutes,
                ),
                start_time_uncertainty_minutes,
            )
            scheduled_phases = _extend_for_start_uncertainty(
                _extend_for_completion_uncertainty(
                    _vertical_phase_legs(
                        scheduled.mission,
                        scheduled.start_minutes,
                        projection,
                    ),
                    scheduled_completion_uncertainty_minutes,
                ),
                start_time_uncertainty_minutes,
            )
            leg_pairs = [
                (candidate_phases, extended_scheduled),
                (extended_candidate, scheduled_phases),
            ]
        approach = next(
            (
                conflict
                for first_legs, second_legs in leg_pairs
                if first_legs and second_legs
                if (
                    conflict := _routes_conflict(
                        first_legs,
                        second_legs,
                        robust_separation_m,
                        speed_uncertainty_fraction,
                        start_minutes,
                        scheduled.start_minutes,
                        candidate_speed,
                        scheduled_speed,
                    )
                )
                is not None
            ),
            None,
        )
        if approach is not None:
            return _conflict_detection(
                ScheduleConflictType.ROUTE,
                scheduled,
                projection,
                approach,
            )
    emergency_pairs = [
        (
            _emergency_legs(mission, start_minutes, projection),
            scheduled_legs,
            start_minutes + (mission.critical_alternate_start_offset_minutes or 0),
            scheduled.start_minutes,
        ),
        (
            candidate_legs,
            _emergency_legs(scheduled.mission, scheduled.start_minutes, projection),
            start_minutes,
            scheduled.start_minutes
            + (scheduled.mission.critical_alternate_start_offset_minutes or 0),
        ),
    ]
    for first_legs, second_legs, first_start, second_start in emergency_pairs:
        if not first_legs or not second_legs:
            continue
        emergency_approach = _routes_conflict(
            first_legs,
            second_legs,
            robust_separation_m,
            speed_uncertainty_fraction,
            first_start,
            second_start,
            _maximum_leg_speed_m_per_minute(first_legs),
            _maximum_leg_speed_m_per_minute(second_legs),
        )
        if emergency_approach is not None:
            return _conflict_detection(
                ScheduleConflictType.EMERGENCY_CORRIDOR,
                scheduled,
                projection,
                emergency_approach,
            )
    return None


def _conflict_detection(
    conflict_type: ScheduleConflictType,
    scheduled: ScheduledEntry,
    projection: LocalProjection,
    approach: ClosestApproach,
) -> ConflictDetection:
    midpoint = (
        (approach.first_xy[0] + approach.second_xy[0]) / 2,
        (approach.first_xy[1] + approach.second_xy[1]) / 2,
    )
    return ConflictDetection(
        conflict_type=conflict_type,
        conflicting_drone_id=scheduled.mission.drone_id,
        conflicting_segment_id=scheduled.mission.segment_id,
        coordinate=projection.unproject(*midpoint),
        offset_minutes=approach.offset_minutes,
        distance_m=approach.distance_m,
        required_distance_m=approach.required_distance_m,
    )


def _flight_site_conflict(
    mission: Mission,
    start_minutes: float,
    scheduled: ScheduledEntry,
    minimum_separation_m: float,
    occupancy_minutes: float,
    start_time_uncertainty_minutes: float,
    speed_uncertainty_fraction: float,
) -> ConflictDetection | None:
    if occupancy_minutes <= 0:
        return None
    candidate_events = [
        (mission.departure_site, start_minutes, start_time_uncertainty_minutes),
        (
            mission.landing_site,
            start_minutes + mission.duration_minutes,
            start_time_uncertainty_minutes
            + _completion_uncertainty_minutes(
                mission.duration_minutes,
                speed_uncertainty_fraction,
            ),
        ),
    ]
    scheduled_events = [
        (
            scheduled.mission.departure_site,
            scheduled.start_minutes,
            start_time_uncertainty_minutes,
        ),
        (
            scheduled.mission.landing_site,
            scheduled.end_minutes,
            start_time_uncertainty_minutes
            + _completion_uncertainty_minutes(
                scheduled.mission.duration_minutes,
                speed_uncertainty_fraction,
            ),
        ),
    ]
    for candidate_site, candidate_time, candidate_uncertainty in candidate_events:
        if candidate_site is None:
            continue
        for scheduled_site, scheduled_time, scheduled_uncertainty in scheduled_events:
            if scheduled_site is None or candidate_site.site_id != scheduled_site.site_id:
                continue
            if (
                abs(candidate_time - scheduled_time) + 1e-9
                >= occupancy_minutes + candidate_uncertainty + scheduled_uncertainty
            ):
                continue
            return ConflictDetection(
                conflict_type=ScheduleConflictType.FLIGHT_SITE,
                conflicting_drone_id=scheduled.mission.drone_id,
                conflicting_segment_id=scheduled.mission.segment_id,
                coordinate=candidate_site.coordinate,
                offset_minutes=max(candidate_time, scheduled_time),
                distance_m=0,
                required_distance_m=minimum_separation_m,
            )
    return None


def _maximum_leg_speed_m_per_minute(legs: list[TimedLeg]) -> float:
    return max((hypot(*_leg_velocity(leg)) for leg in legs), default=0.0)


def _completion_uncertainty_minutes(
    duration_minutes: float,
    speed_uncertainty_fraction: float,
) -> float:
    if speed_uncertainty_fraction <= 0:
        return 0
    return duration_minutes * speed_uncertainty_fraction / (1 - speed_uncertainty_fraction)


def _extend_for_completion_uncertainty(
    legs: list[TimedLeg],
    uncertainty_minutes: float,
) -> list[TimedLeg]:
    if not legs or uncertainty_minutes <= 0:
        return legs
    last = legs[-1]
    return [
        *legs,
        TimedLeg(
            start_minutes=last.end_minutes,
            end_minutes=last.end_minutes + uncertainty_minutes,
            start_xy=last.end_xy,
            end_xy=last.end_xy,
        ),
    ]


def _extend_for_start_uncertainty(
    legs: list[TimedLeg],
    uncertainty_minutes: float,
) -> list[TimedLeg]:
    if not legs or uncertainty_minutes <= 0:
        return legs
    first = legs[0]
    last = legs[-1]
    return [
        TimedLeg(
            start_minutes=first.start_minutes - uncertainty_minutes,
            end_minutes=first.start_minutes,
            start_xy=first.start_xy,
            end_xy=first.start_xy,
        ),
        *legs,
        TimedLeg(
            start_minutes=last.end_minutes,
            end_minutes=last.end_minutes + uncertainty_minutes,
            start_xy=last.end_xy,
            end_xy=last.end_xy,
        ),
    ]


def _routes_conflict(
    first: list[TimedLeg],
    second: list[TimedLeg],
    minimum_separation_m: float,
    speed_uncertainty_fraction: float,
    first_route_start_minutes: float,
    second_route_start_minutes: float,
    first_route_speed_m_per_minute: float,
    second_route_speed_m_per_minute: float,
) -> ClosestApproach | None:
    first_index = 0
    second_index = 0
    while first_index < len(first) and second_index < len(second):
        first_leg = first[first_index]
        second_leg = second[second_index]
        approach = _timed_legs_conflict(
            first_leg,
            second_leg,
            minimum_separation_m,
            speed_uncertainty_fraction,
            first_route_start_minutes,
            second_route_start_minutes,
            first_route_speed_m_per_minute,
            second_route_speed_m_per_minute,
        )
        if approach is not None:
            return approach
        if first_leg.end_minutes <= second_leg.end_minutes:
            first_index += 1
        else:
            second_index += 1
    return None


def _timed_legs(
    mission: Mission,
    start_minutes: float,
    projection: LocalProjection,
) -> list[TimedLeg]:
    horizontal_start = start_minutes + mission.vertical_climb_duration_minutes
    offsets = mission_route_clock(mission).offsets_minutes
    legs: list[TimedLeg] = []
    if mission.vertical_climb_duration_minutes > 0:
        departure = projection.project(mission.route[0])
        legs.append(
            TimedLeg(
                start_minutes=start_minutes,
                end_minutes=horizontal_start,
                start_xy=departure,
                end_xy=departure,
            )
        )
    legs.extend(
        TimedLeg(
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
            TimedLeg(
                start_minutes=start_minutes
                + mission.duration_minutes
                - mission.vertical_descent_duration_minutes,
                end_minutes=start_minutes + mission.duration_minutes,
                start_xy=landing,
                end_xy=landing,
            )
        )
    return legs


def _vertical_phase_legs(
    mission: Mission,
    start_minutes: float,
    projection: LocalProjection,
) -> list[TimedLeg]:
    phases: list[TimedLeg] = []
    if mission.vertical_climb_duration_minutes > 0:
        departure = projection.project(mission.route[0])
        phases.append(
            TimedLeg(
                start_minutes=start_minutes,
                end_minutes=start_minutes + mission.vertical_climb_duration_minutes,
                start_xy=departure,
                end_xy=departure,
            )
        )
    if mission.vertical_descent_duration_minutes > 0:
        landing = projection.project(mission.route[-1])
        phases.append(
            TimedLeg(
                start_minutes=start_minutes
                + mission.duration_minutes
                - mission.vertical_descent_duration_minutes,
                end_minutes=start_minutes + mission.duration_minutes,
                start_xy=landing,
                end_xy=landing,
            )
        )
    return phases


def _emergency_legs(
    mission: Mission,
    start_minutes: float,
    projection: LocalProjection,
) -> list[TimedLeg]:
    offset = mission.critical_alternate_start_offset_minutes
    duration = mission.critical_alternate_duration_minutes
    if (
        len(mission.critical_alternate_route) < 2
        or offset is None
        or duration is None
        or duration <= 0
    ):
        return []
    return _route_legs(
        mission.critical_alternate_route,
        start_minutes + offset,
        duration,
        projection,
    )


def _route_legs(
    route: list[Coordinate],
    start_minutes: float,
    duration_minutes: float,
    projection: LocalProjection,
) -> list[TimedLeg]:
    projected = [projection.project(coordinate) for coordinate in route]
    lengths = [hypot(end[0] - start[0], end[1] - start[1]) for start, end in pairwise(projected)]
    total_length = sum(lengths)
    if total_length <= 0:
        coordinate = projected[0]
        return [
            TimedLeg(
                start_minutes=start_minutes,
                end_minutes=start_minutes + duration_minutes,
                start_xy=coordinate,
                end_xy=coordinate,
            )
        ]
    legs: list[TimedLeg] = []
    leg_start = start_minutes
    for index, ((start_xy, end_xy), length) in enumerate(
        zip(pairwise(projected), lengths, strict=True)
    ):
        leg_duration = duration_minutes * length / total_length
        leg_end = (
            start_minutes + duration_minutes
            if index == len(lengths) - 1
            else leg_start + leg_duration
        )
        legs.append(
            TimedLeg(
                start_minutes=leg_start,
                end_minutes=leg_end,
                start_xy=start_xy,
                end_xy=end_xy,
            )
        )
        leg_start = leg_end
    return legs


def _timed_legs_conflict(
    first: TimedLeg,
    second: TimedLeg,
    minimum_separation_m: float,
    speed_uncertainty_fraction: float,
    first_route_start_minutes: float,
    second_route_start_minutes: float,
    first_route_speed_m_per_minute: float,
    second_route_speed_m_per_minute: float,
) -> ClosestApproach | None:
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
    if speed_squared <= 1e-12:
        closest_time = 0.0
    else:
        closest_time = (
            -(relative_start[0] * relative_velocity[0] + relative_start[1] * relative_velocity[1])
            / speed_squared
        )
        closest_time = min(duration, max(0.0, closest_time))
    offset_minutes = overlap_start + closest_time
    first_closest = _leg_position(first, offset_minutes)
    second_closest = _leg_position(second, offset_minutes)
    distance_m = hypot(first_closest[0] - second_closest[0], first_closest[1] - second_closest[1])
    speed_margin_m = speed_uncertainty_fraction * (
        first_route_speed_m_per_minute * max(0, overlap_end - first_route_start_minutes)
        + second_route_speed_m_per_minute * max(0, overlap_end - second_route_start_minutes)
    )
    required_distance_m = minimum_separation_m + speed_margin_m
    if distance_m >= required_distance_m:
        return None
    return ClosestApproach(
        offset_minutes=offset_minutes,
        first_xy=first_closest,
        second_xy=second_closest,
        distance_m=distance_m,
        required_distance_m=required_distance_m,
    )


def _leg_position(leg: TimedLeg, at_minutes: float) -> tuple[float, float]:
    duration = leg.end_minutes - leg.start_minutes
    if duration <= 0:
        return leg.end_xy
    progress = min(1.0, max(0.0, (at_minutes - leg.start_minutes) / duration))
    return (
        leg.start_xy[0] + (leg.end_xy[0] - leg.start_xy[0]) * progress,
        leg.start_xy[1] + (leg.end_xy[1] - leg.start_xy[1]) * progress,
    )


def _leg_velocity(leg: TimedLeg) -> tuple[float, float]:
    duration = leg.end_minutes - leg.start_minutes
    if duration <= 0:
        return 0.0, 0.0
    return (
        (leg.end_xy[0] - leg.start_xy[0]) / duration,
        (leg.end_xy[1] - leg.start_xy[1]) / duration,
    )
