from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from src.exporters import CanonicalMissionExportVerifier
from src.safety_audit import ExportVerificationResult, IndependentPlanSafetyAuditor
from src.schemas import (
    Coordinate,
    DroneSpec,
    Mission,
    MissionTask,
    NoFlyZone,
    OptimizationCriterion,
    PlanRequest,
    SafetyCheckCode,
    WorkSegment,
)


class SafetyAuditMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    expected_failure: SafetyCheckCode | None
    detected: bool
    failed_checks: list[SafetyCheckCode]
    elapsed_ms: float


@dataclass(frozen=True)
class RejectingExportVerifier:
    def verify(self, missions: list[Mission]) -> ExportVerificationResult:
        return ExportVerificationResult(
            consistent=False,
            message="Injected export mismatch.",
            affected_ids=[f"{mission.drone_id}:{mission.segment_id}" for mission in missions],
        )


def run_experiment() -> list[SafetyAuditMeasurement]:
    first = _mission()
    no_fly_zone = NoFlyZone(
        zone_id="restricted",
        safety_margin_m=30,
        coordinates=[
            Coordinate(longitude=37.004, latitude=54.999),
            Coordinate(longitude=37.006, latitude=54.999),
            Coordinate(longitude=37.006, latitude=55.001),
            Coordinate(longitude=37.004, latitude=55.001),
        ],
    )
    scenarios = [
        (
            "safe_baseline",
            _request(),
            [first],
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            None,
        ),
        (
            "no_fly_intrusion",
            _request().model_copy(update={"no_fly_zones": [no_fly_zone]}),
            [first],
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            SafetyCheckCode.NO_FLY_CLEARANCE,
        ),
        (
            "battery_reserve_deficit",
            _request(),
            [first.model_copy(update={"remaining_battery_percent": 10})],
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            SafetyCheckCode.BATTERY_RESERVE,
        ),
        (
            "simultaneous_crossing",
            _request(include_second_drone=True),
            [
                first,
                _mission(
                    drone_id="drone-2",
                    segment_id="reverse",
                    route=list(reversed(first.route)),
                ),
            ],
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            SafetyCheckCode.DRONE_SEPARATION,
        ),
        (
            "export_mismatch",
            _request(),
            [first],
            IndependentPlanSafetyAuditor(RejectingExportVerifier()),
            SafetyCheckCode.EXPORT_CONSISTENCY,
        ),
    ]
    return [
        _measure(name, request, missions, auditor, expected)
        for name, request, missions, auditor, expected in scenarios
    ]


def _measure(
    scenario: str,
    request: PlanRequest,
    missions: list[Mission],
    auditor: IndependentPlanSafetyAuditor,
    expected_failure: SafetyCheckCode | None,
) -> SafetyAuditMeasurement:
    started = perf_counter()
    report = auditor.audit(request, [_segment()], missions)
    elapsed_ms = (perf_counter() - started) * 1_000
    failed = [check.code for check in report.checks if check.status.value == "failed"]
    detected = len(failed) == 0 if expected_failure is None else expected_failure in failed
    return SafetyAuditMeasurement(
        scenario=scenario,
        expected_failure=expected_failure,
        detected=bool(detected),
        failed_checks=failed,
        elapsed_ms=round(elapsed_ms, 3),
    )


def _request(include_second_drone: bool = False) -> PlanRequest:
    drones = [_drone("drone-1")]
    if include_second_drone:
        drones.append(_drone("drone-2"))
    return PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=drones,
        segments=[_segment()],
        mission_altitude_m=100,
        task=MissionTask.RGB,
        minimum_separation_m=50,
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


def _segment() -> WorkSegment:
    return WorkSegment(segment_id="segment", distance_km=0.64, route=_route())


def _route() -> list[Coordinate]:
    return [
        Coordinate(longitude=37.0, latitude=55.0),
        Coordinate(longitude=37.01, latitude=55.0),
    ]


def _mission(
    drone_id: str = "drone-1",
    segment_id: str = "segment",
    route: list[Coordinate] | None = None,
) -> Mission:
    return Mission(
        drone_id=drone_id,
        segment_id=segment_id,
        duration_minutes=10,
        distance_km=0.64,
        route=route or _route(),
        altitude_m=100,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=30,
        remaining_battery_percent=70,
        battery_reserve_margin_percent=50,
        start_offset_minutes=0,
        end_offset_minutes=10,
    )


if __name__ == "__main__":
    print(json.dumps([item.model_dump(mode="json") for item in run_experiment()], indent=2))
