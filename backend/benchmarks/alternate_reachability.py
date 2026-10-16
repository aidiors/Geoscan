from __future__ import annotations

import json
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import MissionTask, OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.planning.engine import build_plan
from geoplan.planning.models import PlanRequest


class AlternateReachabilityMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    status: str
    minimum_margin_percent: float | None
    duration_minutes: float | None
    elapsed_ms: float
    error_code: str | None
    maximum_certified_interval_m: float | None
    evaluated_checkpoint_count: int | None
    certification_error_minutes: float | None
    critical_checkpoint_longitude: float | None


def run_experiment() -> list[AlternateReachabilityMeasurement]:
    return [
        _measure("reachable_10km_calm", distance_km=10, latitude_delta=0.09, wind=None),
        _measure(
            "reachable_10km_headwind",
            distance_km=10,
            latitude_delta=0.09,
            wind=WindCondition(speed_mps=5, direction_degrees=0),
        ),
        _measure("unreachable_30km_calm", distance_km=30, latitude_delta=0.27, wind=None),
        _measure_request("adaptive_obstacle_refinement", _adaptive_obstacle_request()),
    ]


def _measure(
    scenario: str,
    distance_km: float,
    latitude_delta: float,
    wind: WindCondition | None,
) -> AlternateReachabilityMeasurement:
    start = Coordinate(longitude=37.4, latitude=55.7)
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
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
                segment_id=scenario,
                distance_km=distance_km,
                route=[
                    start,
                    Coordinate(longitude=37.4, latitude=55.7 + latitude_delta),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        wind=wind,
        alternate_landing_sites=[FlightSite(site_id="reserve", name="Reserve", coordinate=start)],
    )
    return _measure_request(scenario, request)


def _measure_request(
    scenario: str,
    request: PlanRequest,
) -> AlternateReachabilityMeasurement:
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
        return AlternateReachabilityMeasurement(
            scenario=scenario,
            status="infeasible",
            minimum_margin_percent=None,
            duration_minutes=None,
            elapsed_ms=round((perf_counter() - started) * 1_000, 2),
            error_code=error.code.value,
            maximum_certified_interval_m=None,
            evaluated_checkpoint_count=None,
            certification_error_minutes=None,
            critical_checkpoint_longitude=None,
        )
    mission = response.drone_plans[0].missions[0]
    return AlternateReachabilityMeasurement(
        scenario=scenario,
        status="feasible",
        minimum_margin_percent=mission.minimum_alternate_margin_percent,
        duration_minutes=mission.duration_minutes,
        elapsed_ms=round((perf_counter() - started) * 1_000, 2),
        error_code=None,
        maximum_certified_interval_m=mission.alternate_check_spacing_m,
        evaluated_checkpoint_count=mission.alternate_evaluated_checkpoint_count,
        certification_error_minutes=mission.alternate_certification_error_minutes,
        critical_checkpoint_longitude=(
            mission.critical_alternate_route[0].longitude
            if mission.critical_alternate_route
            else None
        ),
    )


def _adaptive_obstacle_request() -> PlanRequest:
    start = Coordinate(longitude=37.39, latitude=55.7)
    end = Coordinate(longitude=37.405, latitude=55.7)
    return PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="benchmark",
                name="Benchmark UAV",
                cruise_speed_kmh=60,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="adaptive-obstacle",
                distance_km=0.96,
                route=[start, end],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        no_fly_zones=[
            NoFlyZone(
                zone_id="thin-obstacle",
                coordinates=[
                    Coordinate(longitude=37.3935, latitude=55.701),
                    Coordinate(longitude=37.3945, latitude=55.701),
                    Coordinate(longitude=37.3945, latitude=55.707),
                    Coordinate(longitude=37.3935, latitude=55.707),
                ],
                safety_margin_m=0,
            )
        ],
        alternate_landing_sites=[
            FlightSite(
                site_id="reserve",
                name="Reserve",
                coordinate=Coordinate(longitude=37.394, latitude=55.708),
            )
        ],
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
