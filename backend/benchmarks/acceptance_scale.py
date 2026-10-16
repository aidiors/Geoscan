from __future__ import annotations

import json
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

import numpy as np
from pydantic import BaseModel, ConfigDict
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from shapely.geometry import Polygon

from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters
from geoplan.domain.airspace import AllowedAirspace, FlightObstacle, NoFlyZone, RestrictionValidity
from geoplan.domain.enums import (
    AltitudeMode,
    AltitudeReference,
    ImportedGeometryType,
    MissionTask,
    OptimizationCriterion,
    SafetyAuditStatus,
    ValidityMode,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import GeoTiffTerrainSource
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.engine import build_plan
from geoplan.planning.models import PlanRequest

AREA_SIDE_M = 10_000.0
EXPECTED_AREA_M2 = AREA_SIDE_M**2
FLEET_SIZE = 10


@dataclass(frozen=True)
class AcceptanceScenario:
    name: str
    request: PlanRequest


class AcceptanceMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    criterion: OptimizationCriterion
    area_km2: float
    fleet_size: int
    runtime_seconds: float
    total_flight_minutes: float
    makespan_minutes: float
    sortie_count: int
    active_drone_count: int
    coverage_percent: float | None
    safety_status: SafetyAuditStatus
    safety_check_statuses: dict[str, SafetyAuditStatus]
    safety_margins: dict[str, float | None]
    export_revalidated: bool


def build_scenarios() -> list[AcceptanceScenario]:
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    area = _square_area(projection)
    base = _base_request(area)
    urban = base.model_copy(
        update={
            "no_fly_zones": [_central_no_fly_zone(projection)],
            "obstacles": [_urban_obstacle(projection)],
            "wind": WindCondition(speed_mps=5, direction_degrees=230),
        }
    )
    start = datetime(2026, 9, 28, 6, tzinfo=UTC)
    complex_terrain = base.model_copy(
        update={
            "altitude_reference": AltitudeReference.AGL,
            "terrain_source": _terrain_source(area.coordinates),
            "terrain_vertical_uncertainty_m": 8,
            "terrain_sampling_interval_m": 100,
            "allowed_airspaces": [
                AllowedAirspace(
                    airspace_id="time-bounded-corridor",
                    coordinates=area.coordinates,
                    minimum_altitude_m=0,
                    maximum_altitude_m=500,
                    validity=RestrictionValidity(
                        mode=ValidityMode.TIME_WINDOW,
                        valid_from=start + timedelta(minutes=15),
                        valid_to=start + timedelta(hours=48),
                    ),
                )
            ],
            "planning_start_time": start,
            "planning_end_time": start + timedelta(hours=48),
            "wind": WindCondition(speed_mps=6, direction_degrees=90),
        }
    )
    scenarios = [
        AcceptanceScenario("flat_territory", base),
        AcceptanceScenario("urban_constraints", urban),
        AcceptanceScenario("terrain_and_time_window", complex_terrain),
    ]
    for scenario in scenarios:
        _assert_acceptance_scale(scenario.request)
    return scenarios


def run_experiment() -> list[AcceptanceMeasurement]:
    return [
        _measure(scenario, criterion)
        for scenario in build_scenarios()
        for criterion in (
            OptimizationCriterion.COMPLETION_TIME,
            OptimizationCriterion.TOTAL_FLIGHT_TIME,
        )
    ]


def _measure(
    scenario: AcceptanceScenario,
    criterion: OptimizationCriterion,
) -> AcceptanceMeasurement:
    request = scenario.request.model_copy(update={"criterion": criterion})
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
    runtime_seconds = perf_counter() - started
    audit = response.safety_audit
    if audit is None:
        raise RuntimeError("Acceptance plan did not produce a safety audit.")
    checks = {check.code.value: check.status for check in audit.checks}
    margins = {
        check.code.value: (
            round(check.measured_value - check.required_value, 3)
            if check.measured_value is not None and check.required_value is not None
            else None
        )
        for check in audit.checks
    }
    coverage = next(
        (
            check.measured_value
            for check in audit.checks
            if check.code.value == "coverage_completeness"
        ),
        None,
    )
    return AcceptanceMeasurement(
        scenario=scenario.name,
        criterion=criterion,
        area_km2=EXPECTED_AREA_M2 / 1_000_000,
        fleet_size=len(request.drones),
        runtime_seconds=round(runtime_seconds, 3),
        total_flight_minutes=response.metrics.total_flight_minutes,
        makespan_minutes=response.metrics.completion_minutes,
        sortie_count=response.metrics.sortie_count,
        active_drone_count=response.metrics.active_drone_count,
        coverage_percent=coverage,
        safety_status=audit.status,
        safety_check_statuses=checks,
        safety_margins=margins,
        export_revalidated=checks.get("export_consistency") is SafetyAuditStatus.PASSED,
    )


def _base_request(area: CoverageArea) -> PlanRequest:
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[_drone(index) for index in range(FLEET_SIZE)],
        coverage_areas=[area],
        survey_parameters=SurveyParameters(
            flight_direction_degrees=0,
            side_overlap_percent=60,
            forward_overlap_percent=70,
            maximum_gsd_cm=20,
            strip_width_m=1_000,
            maximum_segment_distance_km=25,
            sensor=SensorProfile(
                sensor_id="acceptance-camera",
                name="Acceptance camera",
                sensor_width_mm=35.9,
                sensor_height_mm=24,
                focal_length_mm=35,
                image_width_px=7_952,
                image_height_px=5_304,
                minimum_trigger_interval_s=0.5,
            ),
        ),
        mission_altitude_m=120,
        task=MissionTask.RGB,
        maximum_active_drones=FLEET_SIZE,
        minimum_separation_m=80,
        position_uncertainty_m=5,
        start_time_uncertainty_seconds=2,
        speed_uncertainty_percent=5,
        turnaround_minutes=5,
    )


def _drone(index: int) -> DroneSpec:
    return DroneSpec(
        drone_id=f"acceptance-{index + 1:02d}",
        name=f"Acceptance UAV {index + 1:02d}",
        cruise_speed_kmh=80 + index,
        maximum_flight_minutes=240,
        battery_reserve_percent=20,
        maximum_wind_mps=15,
        supported_tasks=[MissionTask.RGB],
        maximum_climb_rate_mps=6,
        maximum_descent_rate_mps=5,
    )


def _square_area(projection: LocalProjection) -> CoverageArea:
    half = AREA_SIDE_M / 2
    coordinates = [
        projection.unproject(-half, -half),
        projection.unproject(half, -half),
        projection.unproject(half, half),
        projection.unproject(-half, half),
    ]
    return CoverageArea(area_id="acceptance-100-km2", name="100 km2", coordinates=coordinates)


def _central_no_fly_zone(projection: LocalProjection) -> NoFlyZone:
    return NoFlyZone(
        zone_id="urban-restricted",
        safety_margin_m=100,
        coordinates=[
            projection.unproject(-800, -1_200),
            projection.unproject(800, -1_200),
            projection.unproject(800, 1_200),
            projection.unproject(-800, 1_200),
        ],
    )


def _urban_obstacle(projection: LocalProjection) -> FlightObstacle:
    return FlightObstacle(
        obstacle_id="urban-tower",
        geometry_type=ImportedGeometryType.POINT,
        coordinates=[projection.unproject(2_500, 2_500)],
        height_m=70,
        altitude_mode=AltitudeMode.ABSOLUTE,
        safety_radius_m=100,
    )


def _terrain_source(area: list[Coordinate]) -> GeoTiffTerrainSource:
    west = min(point.longitude for point in area) - 0.01
    east = max(point.longitude for point in area) + 0.01
    south = min(point.latitude for point in area) - 0.01
    north = max(point.latitude for point in area) + 0.01
    x = np.linspace(-1, 1, 128)
    y = np.linspace(-1, 1, 128)
    xx, yy = np.meshgrid(x, y)
    elevations = (140 + 45 * np.sin(xx * np.pi) + 35 * yy).astype("float32")
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=128,
            height=128,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_bounds(west, south, east, north, 128, 128),
            nodata=-9999,
        ) as dataset:
            dataset.write(elevations, 1)
        content = memory_file.read()
    return GeoTiffTerrainSource(content=b64encode(content))


def _assert_acceptance_scale(request: PlanRequest) -> None:
    if len(request.drones) != FLEET_SIZE:
        raise AssertionError("Acceptance benchmark must contain exactly ten aircraft.")
    area = request.coverage_areas[0]
    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    measured_area = Polygon([projection.project(point) for point in area.coordinates]).area
    if abs(measured_area - EXPECTED_AREA_M2) > 1:
        raise AssertionError("Acceptance benchmark area must be 100 square kilometres.")


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
