from __future__ import annotations

import json
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SafetyCheckCode
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.planning.models import PlanRequest
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.models import MissionScheduleRequest
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler


class RobustSeparationMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    position_uncertainty_m: float
    start_time_uncertainty_seconds: float
    makespan_minutes: float
    delayed_start_minutes: float
    guaranteed_distance_m: float | None
    required_distance_m: float
    audit_passed: bool
    resolved_conflict_count: int
    elapsed_ms: float


def run_experiment() -> list[RobustSeparationMeasurement]:
    close_parallel = _parallel_missions(latitude_gap=0.00055)
    far_parallel = _parallel_missions(latitude_gap=0.01)
    crossing = _crossing_missions()
    return [
        _measure("close_nominal", close_parallel, 0, 0),
        _measure("close_position_robust", close_parallel, 10, 0),
        _measure("crossing_nominal", crossing, 0, 0),
        _measure("crossing_time_robust", crossing, 0, 3),
        _measure("far_combined_robust", far_parallel, 10, 3),
    ]


def _measure(
    scenario: str,
    missions: list[Mission],
    position_uncertainty_m: float,
    start_time_uncertainty_seconds: float,
) -> RobustSeparationMeasurement:
    started = perf_counter()
    scheduled = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=missions,
            minimum_separation_m=50,
            position_uncertainty_m=position_uncertainty_m,
            start_time_uncertainty_seconds=start_time_uncertainty_seconds,
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )
    request = _request(
        position_uncertainty_m,
        start_time_uncertainty_seconds,
        [mission.segment_id for mission in missions],
    )
    report = IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()).audit(
        request,
        request.segments,
        scheduled.missions,
    )
    separation = next(
        check for check in report.checks if check.code is SafetyCheckCode.DRONE_SEPARATION
    )
    starts = [mission.start_offset_minutes or 0 for mission in scheduled.missions]
    return RobustSeparationMeasurement(
        scenario=scenario,
        position_uncertainty_m=position_uncertainty_m,
        start_time_uncertainty_seconds=start_time_uncertainty_seconds,
        makespan_minutes=scheduled.makespan_minutes,
        delayed_start_minutes=max(starts),
        guaranteed_distance_m=separation.measured_value,
        required_distance_m=separation.required_value or 50,
        audit_passed=separation.status.value == "passed",
        resolved_conflict_count=scheduled.resolved_conflict_count,
        elapsed_ms=round((perf_counter() - started) * 1_000, 3),
    )


def _parallel_missions(latitude_gap: float) -> list[Mission]:
    return [
        _mission(
            "alpha",
            "first",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        _mission(
            "bravo",
            "second",
            [
                Coordinate(longitude=37.39, latitude=55.7 + latitude_gap),
                Coordinate(longitude=37.41, latitude=55.7 + latitude_gap),
            ],
        ),
    ]


def _crossing_missions() -> list[Mission]:
    return [
        _mission(
            "alpha",
            "west-east",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        _mission(
            "bravo",
            "south-north",
            [
                Coordinate(longitude=37.4, latitude=55.694),
                Coordinate(longitude=37.4, latitude=55.706),
            ],
        ),
    ]


def _mission(
    drone_id: str,
    segment_id: str,
    route: list[Coordinate],
) -> Mission:
    return Mission(
        drone_id=drone_id,
        segment_id=segment_id,
        duration_minutes=10,
        distance_km=1,
        route=route,
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
        remaining_battery_percent=80,
        battery_reserve_margin_percent=60,
    )


def _request(
    position_uncertainty_m: float,
    start_time_uncertainty_seconds: float,
    segment_ids: list[str],
) -> PlanRequest:
    route = [
        Coordinate(longitude=37.39, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[_drone("alpha"), _drone("bravo")],
        segments=[
            WorkSegment(segment_id=segment_id, distance_km=1, route=route)
            for segment_id in segment_ids
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        minimum_separation_m=50,
        position_uncertainty_m=position_uncertainty_m,
        start_time_uncertainty_seconds=start_time_uncertainty_seconds,
    )


def _drone(drone_id: str) -> DroneSpec:
    return DroneSpec(
        drone_id=drone_id,
        name=drone_id,
        cruise_speed_kmh=60,
        maximum_flight_minutes=60,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
    )


if __name__ == "__main__":
    print(json.dumps([item.model_dump(mode="json") for item in run_experiment()], indent=2))
