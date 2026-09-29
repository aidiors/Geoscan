from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from src.exporters import build_qgroundcontrol_export
from src.schemas import (
    AirframeType,
    AutopilotFirmware,
    AutopilotProfile,
    Coordinate,
    DronePlan,
    ExportRequest,
    Mission,
    OptimizationCriterion,
    PlanMetrics,
    PlanResponse,
    QGroundControlPlan,
)


class ProfileMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    firmware: AutopilotFirmware
    airframe: AirframeType
    qgroundcontrol_firmware_type: int
    qgroundcontrol_vehicle_type: int
    hover_speed_mps: float


def run_experiment() -> list[ProfileMeasurement]:
    measurements: list[ProfileMeasurement] = []
    for firmware in AutopilotFirmware:
        for airframe in AirframeType:
            profile = AutopilotProfile(
                firmware=firmware,
                airframe=airframe,
                hover_speed_mps=5,
            )
            exported = QGroundControlPlan.model_validate_json(
                build_qgroundcontrol_export(_export_request(profile)).content
            )
            measurements.append(
                ProfileMeasurement(
                    firmware=firmware,
                    airframe=airframe,
                    qgroundcontrol_firmware_type=exported.mission.firmware_type,
                    qgroundcontrol_vehicle_type=exported.mission.vehicle_type,
                    hover_speed_mps=exported.mission.hover_speed,
                )
            )
    return measurements


def _export_request(profile: AutopilotProfile) -> ExportRequest:
    start = Coordinate(longitude=37.4, latitude=55.7)
    finish = Coordinate(longitude=37.41, latitude=55.71)
    mission = Mission(
        drone_id="profile-benchmark",
        segment_id="survey",
        duration_minutes=2,
        distance_km=1,
        route=[start, finish],
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=5,
    )
    return ExportRequest(
        drone_id="profile-benchmark",
        plan=PlanResponse(
            criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
            drone_plans=[
                DronePlan(
                    drone_id="profile-benchmark",
                    mission_count=1,
                    total_duration_minutes=2,
                    missions=[mission],
                    autopilot_profile=profile,
                )
            ],
            metrics=PlanMetrics(
                completion_minutes=2,
                total_flight_minutes=2,
                total_distance_km=1,
                average_fleet_utilization_percent=100,
            ),
        ),
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            indent=2,
        )
    )
