from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from src.assignment import HybridAssignmentOptimizer
from src.main import create_default_dependencies
from src.schemas import (
    AssignmentOptimizationRequest,
    Coordinate,
    DroneSpec,
    Mission,
    MissionAssignmentCandidate,
    MissionTask,
    OptimizationCriterion,
    PayloadCapability,
    PlanRequest,
    WorkSegment,
)
from src.services import PlanningError, build_plan


@dataclass(frozen=True)
class ReadinessScenario:
    name: str
    drone: DroneSpec
    task: MissionTask = MissionTask.RGB
    ambient_temperature_c: float = 20


class ReadinessMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    status: str
    error_code: str | None
    battery_use_percent: float | None
    remaining_battery_percent: float | None
    elapsed_ms: float


class TurnaroundMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    completion_minutes: float
    total_flight_minutes: float
    fast_service_sorties: int


def run_experiment() -> dict[str, list[dict[str, object]]]:
    return {
        "readiness": [
            _measure_readiness(scenario).model_dump(mode="json")
            for scenario in _readiness_scenarios()
        ],
        "turnaround": [
            _measure_turnaround(0).model_dump(mode="json"),
            _measure_turnaround(10).model_dump(mode="json"),
        ],
    }


def _readiness_scenarios() -> list[ReadinessScenario]:
    baseline = _drone()
    return [
        ReadinessScenario(name="nominal", drone=baseline),
        ReadinessScenario(
            name="low_charge",
            drone=baseline.model_copy(update={"initial_charge_percent": 40}),
        ),
        ReadinessScenario(
            name="aged_battery",
            drone=baseline.model_copy(
                update={"battery_health_percent": 60, "energy_consumption_multiplier": 1.3}
            ),
        ),
        ReadinessScenario(name="temperature_limit", drone=baseline, ambient_temperature_c=-30),
        ReadinessScenario(name="payload_mismatch", drone=baseline, task=MissionTask.THERMAL),
        ReadinessScenario(
            name="route_distance_limit",
            drone=baseline.model_copy(update={"maximum_route_distance_km": 5}),
        ),
    ]


def _drone() -> DroneSpec:
    return DroneSpec(
        drone_id="benchmark",
        name="Benchmark UAV",
        cruise_speed_kmh=60,
        maximum_flight_minutes=60,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB, MissionTask.THERMAL],
        minimum_mission_altitude_m=50,
        maximum_mission_altitude_m=500,
        minimum_operating_temperature_c=-20,
        maximum_operating_temperature_c=40,
        maximum_route_distance_km=50,
        maximum_control_range_km=50,
        payloads=[
            PayloadCapability(
                payload_id="rgb-camera",
                name="RGB camera",
                supported_tasks=[MissionTask.RGB],
            )
        ],
    )


def _measure_readiness(scenario: ReadinessScenario) -> ReadinessMeasurement:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[scenario.drone],
        segments=[_segment()],
        mission_altitude_m=120,
        ambient_temperature_c=scenario.ambient_temperature_c,
        task=scenario.task,
    )
    dependencies = create_default_dependencies()
    started = perf_counter()
    try:
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
        )
    except PlanningError as error:
        return ReadinessMeasurement(
            scenario=scenario.name,
            status="rejected",
            error_code=error.code,
            battery_use_percent=None,
            remaining_battery_percent=None,
            elapsed_ms=round((perf_counter() - started) * 1_000, 2),
        )
    mission = response.drone_plans[0].missions[0]
    return ReadinessMeasurement(
        scenario=scenario.name,
        status="accepted",
        error_code=None,
        battery_use_percent=mission.estimated_battery_percent,
        remaining_battery_percent=mission.remaining_battery_percent,
        elapsed_ms=round((perf_counter() - started) * 1_000, 2),
    )


def _segment() -> WorkSegment:
    return WorkSegment(
        segment_id="survey",
        distance_km=10,
        route=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.4, latitude=55.8),
        ],
        survey_speed_limit_kmh=30,
    )


def _measure_turnaround(turnaround_minutes: float) -> TurnaroundMeasurement:
    groups = [
        [
            _assignment_candidate(f"segment-{index}", "fast-service", 4),
            _assignment_candidate(f"segment-{index}", "steady", 6),
        ]
        for index in range(3)
    ]
    result = HybridAssignmentOptimizer().optimize(
        AssignmentOptimizationRequest(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drone_ids=["fast-service", "steady"],
            candidate_groups=groups,
            turnaround_minutes_by_drone={"fast-service": turnaround_minutes},
        )
    )
    return TurnaroundMeasurement(
        scenario=f"fast_service_turnaround_{turnaround_minutes:g}_minutes",
        completion_minutes=result.completion_minutes,
        total_flight_minutes=result.total_flight_minutes,
        fast_service_sorties=sum(
            assignment.drone_id == "fast-service" for assignment in result.assignments
        ),
    )


def _assignment_candidate(
    segment_id: str,
    drone_id: str,
    duration_minutes: float,
) -> MissionAssignmentCandidate:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.71),
    ]
    return MissionAssignmentCandidate(
        segment_id=segment_id,
        drone_id=drone_id,
        mission=Mission(
            drone_id=drone_id,
            segment_id=segment_id,
            duration_minutes=duration_minutes,
            distance_km=duration_minutes,
            route=route,
            altitude_m=100,
            cruise_speed_kmh=60,
            effective_speed_kmh=60,
            estimated_battery_percent=20,
        ),
    )


if __name__ == "__main__":
    print(json.dumps(run_experiment(), ensure_ascii=False, indent=2))
