from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from src.schemas import Coordinate, InlineTerrainSource, TerrainElevationSample
from src.terrain import (
    InverseDistanceTerrainProvider,
    TerrainDataUnavailableError,
    TerrainFollowingProfilePlanner,
    VerticalRateExceededError,
)


@dataclass(frozen=True)
class TerrainScenario:
    name: str
    elevations_m: tuple[float, float, float]
    maximum_interpolation_distance_m: float
    expected_status: str


class TerrainProfileMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    status: str
    profile_point_count: int
    minimum_agl_m: float | None
    maximum_required_climb_rate_mps: float | None
    maximum_required_descent_rate_mps: float | None
    vertical_distance_m: float | None
    vertical_energy_equivalent_minutes: float | None
    elapsed_ms: float
    expected_status: str
    expectation_met: bool


def run_experiment() -> list[TerrainProfileMeasurement]:
    scenarios = [
        TerrainScenario("flat", (100, 100, 100), 100, "feasible"),
        TerrainScenario("gentle_ridge", (100, 115, 100), 100, "feasible"),
        TerrainScenario("steep_ridge", (100, 220, 100), 100, "vertical_rate_exceeded"),
        TerrainScenario("dem_gap", (100, 100, 100), 10, "terrain_data_unavailable"),
    ]
    return [_measure(scenario) for scenario in scenarios]


def _measure(scenario: TerrainScenario) -> TerrainProfileMeasurement:
    route = [
        Coordinate(longitude=37, latitude=55),
        Coordinate(longitude=37.001, latitude=55),
        Coordinate(longitude=37.002, latitude=55),
    ]
    samples = [
        TerrainElevationSample(coordinate=coordinate, elevation_m=elevation)
        for coordinate, elevation in zip(route, scenario.elevations_m, strict=True)
    ]
    planner = TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider())
    started = perf_counter()
    try:
        profile = planner.plan(
            route=route,
            target_agl_m=120,
            maximum_ground_speed_kmh=10,
            source=InlineTerrainSource(samples=samples),
            sampling_interval_m=20,
            maximum_interpolation_distance_m=scenario.maximum_interpolation_distance_m,
            maximum_climb_rate_mps=3,
            maximum_descent_rate_mps=3,
            terrain_vertical_uncertainty_m=0,
        )
    except TerrainDataUnavailableError:
        return _failed_measurement(scenario, "terrain_data_unavailable", started)
    except VerticalRateExceededError:
        return _failed_measurement(scenario, "vertical_rate_exceeded", started)
    elapsed_ms = (perf_counter() - started) * 1_000
    return TerrainProfileMeasurement(
        scenario=scenario.name,
        status="feasible",
        profile_point_count=len(profile.points),
        minimum_agl_m=round(profile.minimum_agl_m, 3),
        maximum_required_climb_rate_mps=round(profile.maximum_required_climb_rate_mps, 3),
        maximum_required_descent_rate_mps=round(profile.maximum_required_descent_rate_mps, 3),
        vertical_distance_m=round(profile.vertical_distance_m, 3),
        vertical_energy_equivalent_minutes=round(
            profile.vertical_energy_equivalent_minutes,
            3,
        ),
        elapsed_ms=round(elapsed_ms, 3),
        expected_status=scenario.expected_status,
        expectation_met=scenario.expected_status == "feasible",
    )


def _failed_measurement(
    scenario: TerrainScenario,
    status: str,
    started: float,
) -> TerrainProfileMeasurement:
    return TerrainProfileMeasurement(
        scenario=scenario.name,
        status=status,
        profile_point_count=0,
        minimum_agl_m=None,
        maximum_required_climb_rate_mps=None,
        maximum_required_descent_rate_mps=None,
        vertical_distance_m=None,
        vertical_energy_equivalent_minutes=None,
        elapsed_ms=round((perf_counter() - started) * 1_000, 3),
        expected_status=scenario.expected_status,
        expectation_met=status == scenario.expected_status,
    )


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
