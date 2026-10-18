from __future__ import annotations

import json
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.airspace import AllowedAirspace
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SafetyAuditStatus
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.planning.engine import build_plan
from geoplan.planning.models import PlanRequest


class VerticalDeconflictionMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    runtime_ms: float
    makespan_minutes: float
    total_flight_minutes: float
    active_drone_count: int
    vertically_deconflicted_mission_count: int
    maximum_vertical_offset_m: float
    total_vertical_phase_minutes: float
    maximum_applied_delay_minutes: float
    minimum_battery_reserve_margin_percent: float
    safety_status: SafetyAuditStatus


def run_experiment() -> list[VerticalDeconflictionMeasurement]:
    return [
        _measure("temporal_only", maximum_vertical_offset_m=0, long_conflict=True),
        _measure("vertical_layers", maximum_vertical_offset_m=60, long_conflict=True),
        _measure("short_conflict_prefers_delay", maximum_vertical_offset_m=60),
        _measure(
            "ceiling_blocks_layer",
            maximum_vertical_offset_m=60,
            ceiling_m=140,
            long_conflict=True,
        ),
    ]


def _measure(
    scenario: str,
    maximum_vertical_offset_m: float,
    ceiling_m: float | None = None,
    long_conflict: bool = False,
) -> VerticalDeconflictionMeasurement:
    request = _request(maximum_vertical_offset_m, ceiling_m, long_conflict)
    dependencies = create_default_dependencies()
    started = perf_counter()
    response = build_plan(
        request,
        dependencies.duration_calculator,
        dependencies.route_optimizer,
        dependencies.coverage_planner,
        dependencies.assignment_optimizer,
        dependencies.mission_scheduler,
        dependencies.sweep_transition_planner,
        dependencies.safety_auditor,
        dependencies.vertical_profile_planner,
        dependencies.freeform_optimizer,
    )
    elapsed_ms = (perf_counter() - started) * 1_000
    missions = [mission for plan in response.drone_plans for mission in plan.missions]
    schedule = response.schedule
    audit = response.safety_audit
    if schedule is None or audit is None:
        raise RuntimeError("Production plan did not return schedule and safety audit.")
    return VerticalDeconflictionMeasurement(
        scenario=scenario,
        runtime_ms=round(elapsed_ms, 3),
        makespan_minutes=response.metrics.completion_minutes,
        total_flight_minutes=response.metrics.total_flight_minutes,
        active_drone_count=response.metrics.active_drone_count,
        vertically_deconflicted_mission_count=(schedule.vertically_deconflicted_mission_count),
        maximum_vertical_offset_m=max(
            mission.vertical_deconfliction_offset_m for mission in missions
        ),
        total_vertical_phase_minutes=round(
            sum(
                mission.vertical_climb_duration_minutes + mission.vertical_descent_duration_minutes
                for mission in missions
            ),
            6,
        ),
        maximum_applied_delay_minutes=max(
            (conflict.applied_delay_minutes for conflict in schedule.resolved_conflicts),
            default=0,
        ),
        minimum_battery_reserve_margin_percent=min(
            mission.battery_reserve_margin_percent
            for mission in missions
            if mission.battery_reserve_margin_percent is not None
        ),
        safety_status=audit.status,
    )


def _request(
    maximum_vertical_offset_m: float,
    ceiling_m: float | None,
    long_conflict: bool,
) -> PlanRequest:
    second_route = (
        [
            Coordinate(longitude=37.41, latitude=55.7),
            Coordinate(longitude=37.39, latitude=55.7),
        ]
        if long_conflict
        else [
            Coordinate(longitude=37.4, latitude=55.694),
            Coordinate(longitude=37.4, latitude=55.706),
        ]
    )
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[_drone("alpha"), _drone("bravo")],
        segments=[
            WorkSegment(
                segment_id="west-east",
                distance_km=1.28,
                route=[
                    Coordinate(longitude=37.39, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.7),
                ],
            ),
            WorkSegment(
                segment_id="opposing-or-crossing",
                distance_km=1.28 if long_conflict else 1.33,
                route=second_route,
            ),
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        maximum_active_drones=2,
        minimum_separation_m=50,
        minimum_vertical_separation_m=30,
        vertical_deconfliction_step_m=30,
        maximum_vertical_deconfliction_m=maximum_vertical_offset_m,
        allowed_airspaces=(
            [
                AllowedAirspace(
                    airspace_id="low-ceiling",
                    coordinates=[
                        Coordinate(longitude=37.38, latitude=55.69),
                        Coordinate(longitude=37.42, latitude=55.69),
                        Coordinate(longitude=37.42, latitude=55.71),
                        Coordinate(longitude=37.38, latitude=55.71),
                    ],
                    minimum_altitude_m=0,
                    maximum_altitude_m=ceiling_m,
                )
            ]
            if ceiling_m is not None
            else []
        ),
    )


def _drone(drone_id: str) -> DroneSpec:
    return DroneSpec(
        drone_id=drone_id,
        name=drone_id,
        cruise_speed_kmh=60,
        maximum_flight_minutes=60,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
        maximum_climb_rate_mps=3,
        maximum_descent_rate_mps=2,
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
