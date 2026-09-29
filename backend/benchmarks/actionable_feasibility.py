from __future__ import annotations

import json
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
    FeasibilityRecommendation,
    FlightSite,
    MissionTask,
    OptimizationCriterion,
    PlanRequest,
    WindCondition,
    WorkSegment,
)
from src.services import CruiseMissionDurationCalculator, PlanningError, build_plan
from src.terrain import InverseDistanceTerrainProvider, TerrainFollowingProfilePlanner


class FeasibilityScenario(StrEnum):
    DEADLINE = "deadline"
    ENDURANCE = "endurance"
    WIND = "wind"
    TASK_CAPABILITY = "task_capability"
    FLIGHT_SITE = "flight_site"
    ALTITUDE = "altitude"


class FeasibilityMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: FeasibilityScenario
    error_code: ErrorCode | None
    recommendation_codes: list[str]
    exact_required_values: dict[str, float]


def run_experiment() -> list[FeasibilityMeasurement]:
    base = _base_request()
    return [
        _measure(FeasibilityScenario.DEADLINE, _deadline_request()),
        _measure(
            FeasibilityScenario.ENDURANCE,
            base.model_copy(
                update={
                    "segments": [_segment("far", 30)],
                    "drones": [base.drones[0].model_copy(update={"maximum_flight_minutes": 20})],
                }
            ),
        ),
        _measure(
            FeasibilityScenario.WIND,
            base.model_copy(update={"wind": WindCondition(speed_mps=12, direction_degrees=0)}),
        ),
        _measure(
            FeasibilityScenario.TASK_CAPABILITY,
            base.model_copy(update={"task": MissionTask.THERMAL}),
        ),
        _measure(
            FeasibilityScenario.FLIGHT_SITE,
            base.model_copy(
                update={
                    "departure_sites": [_site("start", "other")],
                    "landing_sites": [_site("finish", "other")],
                }
            ),
        ),
        _measure(
            FeasibilityScenario.ALTITUDE,
            base.model_copy(update={"allowed_airspaces": [_low_airspace()]}),
        ),
    ]


def _measure(
    scenario: FeasibilityScenario,
    request: PlanRequest,
) -> FeasibilityMeasurement:
    recommendations: list[FeasibilityRecommendation]
    error_code: ErrorCode | None = None
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
        error_code = error.code
        recommendations = error.recommendations
    else:
        recommendations = response.feasibility.recommendations if response.feasibility else []
    return FeasibilityMeasurement(
        scenario=scenario,
        error_code=error_code,
        recommendation_codes=[recommendation.code.value for recommendation in recommendations],
        exact_required_values={
            recommendation.parameter: recommendation.required_value
            for recommendation in recommendations
            if recommendation.confidence.value == "exact"
            and recommendation.parameter is not None
            and recommendation.required_value is not None
        },
    )


def _base_request() -> PlanRequest:
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Benchmark UAV",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                maximum_wind_mps=10,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[_segment("short", 2)],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )


def _deadline_request() -> PlanRequest:
    base = _base_request()
    return base.model_copy(
        update={
            "segments": [_segment("first", 10), _segment("second", 10)],
            "completion_deadline_minutes": 15,
            "maximum_active_drones": 1,
        }
    )


def _segment(segment_id: str, distance_km: float) -> WorkSegment:
    return WorkSegment(
        segment_id=segment_id,
        distance_km=distance_km,
        route=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.71),
        ],
    )


def _site(site_id: str, drone_id: str) -> FlightSite:
    return FlightSite(
        site_id=site_id,
        name=site_id,
        coordinate=Coordinate(longitude=37.4, latitude=55.7),
        assigned_drone_ids=[drone_id],
    )


def _low_airspace() -> AllowedAirspace:
    return AllowedAirspace(
        airspace_id="low",
        coordinates=[
            Coordinate(longitude=37.39, latitude=55.69),
            Coordinate(longitude=37.42, latitude=55.69),
            Coordinate(longitude=37.42, latitude=55.72),
            Coordinate(longitude=37.39, latitude=55.72),
        ],
        minimum_altitude_m=20,
        maximum_altitude_m=100,
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            indent=2,
        )
    )
