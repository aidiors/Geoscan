"""Finite camera positions on the actual mission clock, shared with export and audit."""

from dataclasses import replace
from itertools import pairwise
from math import inf

from geoplan.coverage.models import CoveragePlanRequest
from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import Mission
from geoplan.navigation.routing import coordinate_distance_km
from geoplan.routing.freeform import GeometryCase, GeometryModel, Leg, evaluate_flights


def with_capture_waypoints(
    mission: Mission, interval_s: float, *, maximum_captures: int = 4_000
) -> Mission:
    """Insert camera positions without changing geometry, phase times or speed.

    One clock survives vertices and short camera-off connections. Captures at
    existing vertices are snapped to them, never duplicated as tiny route edges.
    """
    if mission.route_leg_durations_minutes is None:
        raise ValueError("Explicit photography needs a complete mission clock")
    clock, last_image = 0.0, -inf
    points = [mission.route[0]]
    captures, stages = [], []
    for stage in mission.stages:
        stage_points = [stage.coordinates[0]]
        stage_times = []
        elapsed = 0.0
        for (a, b), duration in zip(
            pairwise(stage.coordinates), stage.leg_durations_minutes or [], strict=True
        ):
            cuts = [(0.0, False)]
            if stage.stage_type is MissionStageType.SURVEY and a != b:
                timestamp = max(clock + elapsed, last_image + interval_s / 60)
                while timestamp <= clock + elapsed + duration + 1e-10:
                    fraction = min(1.0, max(0.0, (timestamp - clock - elapsed) / duration))
                    if fraction < 1e-8:
                        fraction = 0.0
                    if fraction > 1 - 1e-8:
                        fraction = 1.0
                    cuts.append((fraction, True))
                    last_image = timestamp
                    timestamp += interval_s / 60
            cuts.append((1.0, False))
            previous = 0.0
            for fraction, capture in sorted(cuts):
                point = (
                    a
                    if fraction == 0
                    else b
                    if fraction == 1
                    else a.model_copy(
                        update={
                            "longitude": a.longitude + fraction * (b.longitude - a.longitude),
                            "latitude": a.latitude + fraction * (b.latitude - a.latitude),
                        }
                    )
                )
                if point != stage_points[-1]:
                    stage_points.append(point)
                    stage_times.append((fraction - previous) * duration)
                    points.append(point)
                    previous = fraction
                if capture:
                    captures.append(len(points) - 1)
                    if len(captures) > maximum_captures:
                        raise ValueError("Explicit camera plan exceeds the capture limit")
            elapsed += duration
        stages.append(
            stage.model_copy(
                update={
                    "coordinates": stage_points,
                    "leg_durations_minutes": stage_times if len(stage_points) > 1 else None,
                }
            )
        )
        clock += stage.duration_minutes
    if not captures:
        raise ValueError("Mission contains no camera captures")
    data = mission.model_dump()
    data.update(
        route=points,
        stages=[s.model_dump() for s in stages],
        image_count=len(captures),
        photo_capture_route_indices=captures,
    )
    return Mission.model_validate(data)


def screen_mission(request: CoveragePlanRequest, mission: Mission) -> dict:
    """Recheck actual service phases; the normal audit checks energy and vertical flight."""
    probe = GeometryModel(GeometryCase("mission", request, (0, 0), (0, 0)))
    model = GeometryModel(
        replace(
            probe.case,
            home=probe.projection.project(mission.route[0]),
            landing=probe.projection.project(mission.route[-1]),
            cruise_speed_kmh=mission.cruise_speed_kmh,
            endurance_minutes=1_000_000,
            reserve_percent=0,
        )
    )
    legs = []
    started = False
    for stage in mission.stages:
        scan = stage.stage_type is MissionStageType.SURVEY
        phase = (
            "survey"
            if scan
            else "return"
            if stage.stage_type is MissionStageType.LANDING
            else ("connection" if started else "departure")
        )
        started |= scan
        for (a, b), duration in zip(
            pairwise(stage.coordinates), stage.leg_durations_minutes or [], strict=True
        ):
            if a != b:
                legs.append(
                    Leg(
                        model.projection.project(a),
                        model.projection.project(b),
                        duration,
                        coordinate_distance_km(a, b),
                        phase,
                    )
                )
    result = evaluate_flights(model, "mission", (tuple(legs),))
    if mission.photo_capture_route_indices is not None:
        expected = with_capture_waypoints(
            mission, request.parameters.sensor.minimum_trigger_interval_s
        )
        actual_points = [mission.route[i] for i in mission.photo_capture_route_indices]
        expected_points = [expected.route[i] for i in expected.photo_capture_route_indices or []]
        matched = len(actual_points) == len(expected_points) and all(
            coordinate_distance_km(a, b) < 1e-7
            for a, b in zip(actual_points, expected_points, strict=True)
        )
        result["screen_passed"] &= matched and result["images"] == mission.image_count
    return result
