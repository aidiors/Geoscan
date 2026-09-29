from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from src.coverage_benchmark import default_coverage_benchmark_cases
from src.main import create_default_dependencies
from src.routing import route_bearing_degrees
from src.schemas import (
    Coordinate,
    DroneSpec,
    FlightSite,
    MissionTask,
    OptimizationCriterion,
    PlanRequest,
)
from src.services import build_plan


@dataclass(frozen=True)
class SiteScenario:
    name: str
    departure: Coordinate
    landing: Coordinate


class FlightEnvelopeMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    minimum_turn_radius_m: float
    total_distance_km: float
    transit_distance_km: float
    duration_minutes: float
    maximum_join_heading_error_degrees: float
    transit_point_count: int
    landing_point_count: int
    elapsed_ms: float


def run_experiment() -> list[FlightEnvelopeMeasurement]:
    scenarios = [
        SiteScenario(
            name="west_to_east",
            departure=Coordinate(longitude=37.6, latitude=55.75),
            landing=Coordinate(longitude=37.64, latitude=55.75),
        ),
        SiteScenario(
            name="south_to_north",
            departure=Coordinate(longitude=37.62, latitude=55.74),
            landing=Coordinate(longitude=37.62, latitude=55.76),
        ),
        SiteScenario(
            name="southwest_to_northeast",
            departure=Coordinate(longitude=37.6, latitude=55.74),
            landing=Coordinate(longitude=37.64, latitude=55.76),
        ),
    ]
    return [
        _measure(scenario, minimum_turn_radius_m)
        for scenario in scenarios
        for minimum_turn_radius_m in (0.0, 30.0)
    ]


def _measure(
    scenario: SiteScenario,
    minimum_turn_radius_m: float,
) -> FlightEnvelopeMeasurement:
    coverage = default_coverage_benchmark_cases()[0].request
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="benchmark",
                name="Benchmark UAV",
                cruise_speed_kmh=80,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
        departure_site=FlightSite(
            site_id=f"{scenario.name}-departure",
            name="Departure",
            coordinate=scenario.departure,
        ),
        landing_site=FlightSite(
            site_id=f"{scenario.name}-landing",
            name="Landing",
            coordinate=scenario.landing,
        ),
        minimum_turn_radius_m=minimum_turn_radius_m,
    )
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
    )
    elapsed_ms = (perf_counter() - started) * 1_000
    mission = response.drone_plans[0].missions[0]
    transit_route = mission.stages[1].coordinates
    survey_route = mission.stages[2].coordinates
    landing_route = mission.stages[3].coordinates
    return FlightEnvelopeMeasurement(
        scenario=scenario.name,
        minimum_turn_radius_m=minimum_turn_radius_m,
        total_distance_km=mission.distance_km,
        transit_distance_km=mission.transit_distance_km or 0,
        duration_minutes=mission.duration_minutes,
        maximum_join_heading_error_degrees=max(
            _heading_difference_degrees(
                route_bearing_degrees(transit_route[-2], transit_route[-1]),
                route_bearing_degrees(survey_route[0], survey_route[1]),
            ),
            _heading_difference_degrees(
                route_bearing_degrees(survey_route[-2], survey_route[-1]),
                route_bearing_degrees(landing_route[0], landing_route[1]),
            ),
        ),
        transit_point_count=len(transit_route),
        landing_point_count=len(landing_route),
        elapsed_ms=round(elapsed_ms, 2),
    )


def _heading_difference_degrees(first: float, second: float) -> float:
    return round(abs((first - second + 180) % 360 - 180), 3)


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
