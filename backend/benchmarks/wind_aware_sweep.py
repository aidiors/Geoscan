from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from geoplan.assignment.solver import HybridAssignmentOptimizer
from geoplan.coverage.adaptive import AdaptiveCoveragePlanner
from geoplan.coverage.models import (
    CoverageArea,
    CoveragePlanRequest,
    SensorProfile,
    SurveyParameters,
)
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SweepDirectionMode
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.flight.terrain import InverseDistanceTerrainProvider, TerrainFollowingProfilePlanner
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.navigation.curvature import DubinsSweepTransitionPlanner
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.optimizer import BidirectionalRouteOptimizer
from geoplan.planning.engine import build_plan
from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler


class WindSweepMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    wind_from_degrees: float
    geometric_angle_degrees: float
    wind_aware_angle_degrees: float
    geometric_duration_minutes: float
    wind_aware_duration_minutes: float
    duration_improvement_percent: float


def run_experiment() -> list[WindSweepMeasurement]:
    area = _coverage_area()
    parameters = _survey_parameters()
    geometric = BoustrophedonCoveragePlanner().plan(
        CoveragePlanRequest(
            area=area,
            mission_altitude_m=100,
            parameters=parameters,
        )
    )
    measurements: list[WindSweepMeasurement] = []
    for wind_from_degrees in (0.0, 45.0, 90.0, 135.0):
        wind = WindCondition(speed_mps=10, direction_degrees=wind_from_degrees)
        geometric_plan = _plan(
            area,
            parameters.model_copy(
                update={
                    "direction_mode": SweepDirectionMode.MANUAL,
                    "flight_direction_degrees": geometric.selected_flight_direction_degrees,
                }
            ),
            wind,
        )
        wind_aware_plan = _plan(area, parameters, wind)
        geometric_duration = geometric_plan.metrics.total_flight_minutes
        wind_aware_duration = wind_aware_plan.metrics.total_flight_minutes
        wind_aware_mission = next(
            mission for drone_plan in wind_aware_plan.drone_plans for mission in drone_plan.missions
        )
        measurements.append(
            WindSweepMeasurement(
                wind_from_degrees=wind_from_degrees,
                geometric_angle_degrees=geometric.selected_flight_direction_degrees,
                wind_aware_angle_degrees=wind_aware_mission.flight_direction_degrees or 0,
                geometric_duration_minutes=geometric_duration,
                wind_aware_duration_minutes=wind_aware_duration,
                duration_improvement_percent=round(
                    (geometric_duration - wind_aware_duration) / geometric_duration * 100,
                    2,
                ),
            )
        )
    return measurements


def _plan(
    area: CoverageArea,
    parameters: SurveyParameters,
    wind: WindCondition,
) -> PlanResponse:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="benchmark",
                name="Benchmark UAV",
                cruise_speed_kmh=55,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                maximum_wind_mps=15,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[area],
        survey_parameters=parameters,
        mission_altitude_m=100,
        task=MissionTask.RGB,
        wind=wind,
    )
    return build_plan(
        request,
        CruiseMissionDurationCalculator(),
        BidirectionalRouteOptimizer(),
        AdaptiveCoveragePlanner(),
        HybridAssignmentOptimizer(),
        ConflictAwareMissionScheduler(),
        DubinsSweepTransitionPlanner(ExactDubinsPlanner()),
        IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
        TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider()),
        AuditedFreeformRouteOptimizer(),
    )


def _coverage_area() -> CoverageArea:
    return CoverageArea(
        area_id="wind-field",
        name="Rectangular wind field",
        coordinates=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.42, latitude=55.7),
            Coordinate(longitude=37.42, latitude=55.712),
            Coordinate(longitude=37.4, latitude=55.712),
        ],
    )


def _survey_parameters() -> SurveyParameters:
    return SurveyParameters(
        direction_mode=SweepDirectionMode.AUTOMATIC,
        flight_direction_degrees=0,
        side_overlap_percent=60,
        forward_overlap_percent=75,
        maximum_gsd_cm=5,
        strip_width_m=200,
        maximum_segment_distance_km=100,
        sensor=SensorProfile(
            sensor_id="benchmark-camera",
            name="Benchmark camera",
            sensor_width_mm=13.2,
            sensor_height_mm=8.8,
            focal_length_mm=8.8,
            image_width_px=5_472,
            image_height_px=3_648,
            minimum_trigger_interval_s=1,
        ),
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            indent=2,
        )
    )
