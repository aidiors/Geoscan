"""Horizontal route clock shared by scheduling, independent audit and export.

Explicit per-edge times are authoritative. Complete legacy phase times are
usable when they agree with the route and total; otherwise retain the legacy
uniform-speed interpretation. Vertical climb/descent remain outside this clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import Mission
from geoplan.navigation.routing import coordinate_distance_km


@dataclass(frozen=True)
class RouteClock:
    offsets_minutes: tuple[float, ...]
    phase_timed: bool


def mission_route_clock(mission: Mission) -> RouteClock:
    horizontal = (
        mission.duration_minutes
        - mission.vertical_climb_duration_minutes
        - mission.vertical_descent_duration_minutes
    )
    explicit = mission.route_leg_durations_minutes
    if explicit is not None:
        offsets = [0.0]
        for duration in explicit:
            offsets.append(offsets[-1] + duration)
        return RouteClock(tuple(offsets), True)
    points = [mission.route[0]]
    durations = []
    complete = True
    for stage in mission.stages:
        if stage.coordinates[0] != points[-1]:
            complete = False
            break
        phase_duration = stage.duration_minutes
        if stage.stage_type is MissionStageType.TAKEOFF:
            phase_duration -= mission.vertical_climb_duration_minutes
        if stage.stage_type is MissionStageType.LANDING:
            phase_duration -= mission.vertical_descent_duration_minutes
        if stage.leg_durations_minutes is not None:
            times = stage.leg_durations_minutes
        else:
            lengths = [coordinate_distance_km(a, b) for a, b in pairwise(stage.coordinates)]
            length = sum(lengths)
            if length > 0 and phase_duration > 0:
                times = [phase_duration * d / length for d in lengths]
            elif length > 0:
                complete = False
                break
            else:
                times = [0.0] * len(lengths)
        for point, duration in zip(stage.coordinates[1:], times, strict=True):
            if point != points[-1]:
                points.append(point)
                durations.append(duration)
    # Production routes are deduplicated; reject partial/misaligned authoritative clocks.
    complete = complete and points == mission.route and bool(durations) and sum(durations) > 0
    phase_timed = complete and abs(sum(durations) - horizontal) <= 0.05
    if not phase_timed:
        lengths = [coordinate_distance_km(a, b) for a, b in pairwise(mission.route)]
        total = sum(lengths)
        durations = (
            [horizontal * d / total for d in lengths]
            if total
            else [0.0 for _ in lengths[:-1]] + [horizontal]
        )
    else:
        total = sum(durations)
        durations = [horizontal * d / total for d in durations]
    offsets = [0.0]
    for duration in durations:
        offsets.append(offsets[-1] + duration)
    offsets[-1] = horizontal
    return RouteClock(tuple(offsets), phase_timed)
