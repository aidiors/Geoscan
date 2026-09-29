from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from src.exporters import build_mission_planner_export, build_qgroundcontrol_export
from src.schemas import (
    Coordinate,
    DronePlan,
    ExportRequest,
    Mission,
    MissionStage,
    MissionStageType,
    OptimizationCriterion,
    PlanMetrics,
    PlanResponse,
    QGroundControlPlan,
)


class MissionExportMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    qgroundcontrol_commands: list[int]
    mission_planner_commands: list[int]
    camera_trigger_distance_m: float
    takeoff_altitude_m: float
    landing_altitude_m: float


def run_experiment() -> MissionExportMeasurement:
    request = _export_request()
    qgroundcontrol = QGroundControlPlan.model_validate_json(
        build_qgroundcontrol_export(request).content
    )
    mission_planner = build_mission_planner_export(request)
    mission_planner_commands = [
        int(line.split("\t")[3]) for line in mission_planner.content.splitlines()[1:]
    ]
    items = qgroundcontrol.mission.items
    return MissionExportMeasurement(
        qgroundcontrol_commands=[item.command for item in items],
        mission_planner_commands=mission_planner_commands,
        camera_trigger_distance_m=items[2].params[0] or 0,
        takeoff_altitude_m=items[0].params[6] or 0,
        landing_altitude_m=items[-1].params[6] or 0,
    )


def _export_request() -> ExportRequest:
    start = Coordinate(longitude=37.4, latitude=55.7)
    finish = Coordinate(longitude=37.41, latitude=55.71)
    mission = Mission(
        drone_id="benchmark",
        segment_id="survey",
        duration_minutes=12,
        distance_km=1,
        route=[start, finish],
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
        survey_distance_km=1,
        image_count=11,
        stages=[
            MissionStage(stage_type=MissionStageType.TAKEOFF, coordinates=[start]),
            MissionStage(stage_type=MissionStageType.SURVEY, coordinates=[start, finish]),
            MissionStage(stage_type=MissionStageType.LANDING, coordinates=[finish]),
        ],
    )
    return ExportRequest(
        drone_id="benchmark",
        plan=PlanResponse(
            criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
            drone_plans=[
                DronePlan(
                    drone_id="benchmark",
                    mission_count=1,
                    total_duration_minutes=12,
                    missions=[mission],
                )
            ],
            metrics=PlanMetrics(
                completion_minutes=12,
                total_flight_minutes=12,
                total_distance_km=1,
                average_fleet_utilization_percent=20,
            ),
        ),
    )


if __name__ == "__main__":
    print(json.dumps(run_experiment().model_dump(mode="json"), indent=2))
