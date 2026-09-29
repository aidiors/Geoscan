from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from src.adaptive_coverage import AdaptiveCoveragePlanner
from src.assignment import HybridAssignmentOptimizer
from src.curvature import DubinsSweepTransitionPlanner
from src.dubins import ExactDubinsPlanner
from src.exporters import CanonicalMissionExportVerifier
from src.optimizer import BidirectionalRouteOptimizer
from src.safety_audit import IndependentPlanSafetyAuditor
from src.scheduler import ConflictAwareMissionScheduler
from src.schemas import (
    AllowedAirspace,
    Coordinate,
    DroneSpec,
    ErrorCode,
    MissionTask,
    NoFlyZone,
    OptimizationCriterion,
    PlanRequest,
    RestrictionValidity,
    ValidityMode,
    WorkSegment,
)
from src.services import CruiseMissionDurationCalculator, PlanningError, build_plan
from src.terrain import InverseDistanceTerrainProvider, TerrainFollowingProfilePlanner


class AirspaceScenario(StrEnum):
    PERMANENT = "permanent"
    DELAYED = "delayed"
    SECOND_WINDOW = "second_window"
    SPLIT_UNION = "split_union"
    TOO_SHORT = "too_short"
    NO_FLY_ACTIVE = "no_fly_active"
    NO_FLY_ABOVE = "no_fly_above"
    OPERATING_WINDOW_TOO_SHORT = "operating_window_too_short"


class AirspaceWindowMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: AirspaceScenario
    status: str
    start_offset_minutes: float | None
    end_offset_minutes: float | None
    error_code: ErrorCode | None = None


def run_experiment() -> list[AirspaceWindowMeasurement]:
    return [
        _measure(AirspaceScenario.PERMANENT, _request([_airspace("permanent", None)], [], None)),
        _measure(AirspaceScenario.DELAYED, _request([_airspace("delayed", (30, 60))], [], None)),
        _measure(
            AirspaceScenario.SECOND_WINDOW,
            _request(
                [_airspace("short", (0, 1)), _airspace("second", (20, 30))],
                [],
                None,
            ),
        ),
        _measure(
            AirspaceScenario.SPLIT_UNION,
            _request(
                [
                    _airspace("left", (10, 30), minimum_longitude=37.39, maximum_longitude=37.41),
                    _airspace("right", (10, 30), minimum_longitude=37.41, maximum_longitude=37.43),
                ],
                [],
                None,
            ),
        ),
        _measure(
            AirspaceScenario.TOO_SHORT,
            _request([_airspace("too-short", (0, 1))], [], None),
        ),
        _measure(
            AirspaceScenario.NO_FLY_ACTIVE,
            _request([], [_no_fly_zone("active", (0, 30), 100, 150)], None),
        ),
        _measure(
            AirspaceScenario.NO_FLY_ABOVE,
            _request([], [_no_fly_zone("above", (0, 30), 200, 300)], None),
        ),
        _measure(
            AirspaceScenario.OPERATING_WINDOW_TOO_SHORT,
            _request([], [], _planning_start() + timedelta(minutes=1)),
        ),
    ]


def _measure(
    scenario: AirspaceScenario,
    request: PlanRequest,
) -> AirspaceWindowMeasurement:
    try:
        response = build_plan(
            request,
            CruiseMissionDurationCalculator(),
            BidirectionalRouteOptimizer(),
            AdaptiveCoveragePlanner(),
            HybridAssignmentOptimizer(),
            ConflictAwareMissionScheduler(),
            DubinsSweepTransitionPlanner(ExactDubinsPlanner()),
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider()),
        )
    except PlanningError as error:
        return AirspaceWindowMeasurement(
            scenario=scenario,
            status="rejected",
            start_offset_minutes=None,
            end_offset_minutes=None,
            error_code=error.code,
        )
    mission = response.drone_plans[0].missions[0]
    return AirspaceWindowMeasurement(
        scenario=scenario,
        status="scheduled",
        start_offset_minutes=mission.start_offset_minutes,
        end_offset_minutes=mission.end_offset_minutes,
    )


def _request(
    airspaces: list[AllowedAirspace],
    no_fly_zones: list[NoFlyZone],
    planning_end_time: datetime | None,
) -> PlanRequest:
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="benchmark",
                name="Benchmark UAV",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="timed-route",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.42, latitude=55.7),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=_planning_start(),
        planning_end_time=planning_end_time,
        allowed_airspaces=airspaces,
        no_fly_zones=no_fly_zones,
    )


def _airspace(
    airspace_id: str,
    offsets_minutes: tuple[float, float] | None,
    minimum_longitude: float = 37.39,
    maximum_longitude: float = 37.43,
) -> AllowedAirspace:
    validity = None
    if offsets_minutes is not None:
        validity = RestrictionValidity(
            mode=ValidityMode.TIME_WINDOW,
            valid_from=_planning_start() + timedelta(minutes=offsets_minutes[0]),
            valid_to=_planning_start() + timedelta(minutes=offsets_minutes[1]),
        )
    return AllowedAirspace(
        airspace_id=airspace_id,
        coordinates=[
            Coordinate(longitude=minimum_longitude, latitude=55.69),
            Coordinate(longitude=maximum_longitude, latitude=55.69),
            Coordinate(longitude=maximum_longitude, latitude=55.71),
            Coordinate(longitude=minimum_longitude, latitude=55.71),
        ],
        minimum_altitude_m=20,
        maximum_altitude_m=500,
        validity=validity,
    )


def _no_fly_zone(
    zone_id: str,
    offsets_minutes: tuple[float, float],
    minimum_altitude_m: float,
    maximum_altitude_m: float,
) -> NoFlyZone:
    return NoFlyZone(
        zone_id=zone_id,
        coordinates=[
            Coordinate(longitude=37.409, latitude=55.699),
            Coordinate(longitude=37.411, latitude=55.699),
            Coordinate(longitude=37.411, latitude=55.701),
            Coordinate(longitude=37.409, latitude=55.701),
        ],
        safety_margin_m=0,
        minimum_altitude_m=minimum_altitude_m,
        maximum_altitude_m=maximum_altitude_m,
        validity=RestrictionValidity(
            mode=ValidityMode.TIME_WINDOW,
            valid_from=_planning_start() + timedelta(minutes=offsets_minutes[0]),
            valid_to=_planning_start() + timedelta(minutes=offsets_minutes[1]),
        ),
    )


def _planning_start() -> datetime:
    return datetime(2026, 9, 27, 7, 0, tzinfo=UTC)


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            indent=2,
        )
    )
