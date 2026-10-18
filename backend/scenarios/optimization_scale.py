"""Deterministic synthetic surveys with explicit bases and heterogeneous aircraft."""

from dataclasses import dataclass

from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanRequest
from scenarios.planning import productive_survey_request


@dataclass(frozen=True)
class OptimizationScaleScenario:
    name: str
    split: str
    area_km2: float
    request: PlanRequest


def scale_scenarios() -> list[OptimizationScaleScenario]:
    projection = LocalProjection(37, 55)
    base = productive_survey_request()
    assert base.survey_parameters is not None and base.departure_site is not None
    fleet = [
        base.drones[0].model_copy(
            update={
                "drone_id": f"synthetic-{i}",
                "name": f"Synthetic UAV {i}",
                "cruise_speed_kmh": 60 + i * 5,
                "maximum_flight_minutes": 70 + i * 10,
                "minimum_turnaround_minutes": float(2 + i % 3),
                "maximum_wind_mps": 12,
            }
        )
        for i in range(10)
    ]
    sensor = base.survey_parameters.sensor.model_copy(update={"minimum_trigger_interval_s": 0.5})
    parameters = base.survey_parameters.model_copy(
        update={
            "strip_width_m": 1000,
            "maximum_segment_distance_km": 50,
            "sensor": sensor,
        }
    )
    shapes = (
        ("tuning_rectangle_10", "tuning", 10.0, ((0, 0), (5000, 0), (5000, 2000), (0, 2000)), 0),
        ("flat_100", "holdout", 100.0, ((0, 0), (10000, 0), (10000, 10000), (0, 10000)), 0),
        ("wind_distant_100", "holdout", 100.0, ((0, 0), (10000, 0), (10000, 10000), (0, 10000)), 5),
        (
            "notched_100",
            "holdout",
            100.0,
            (
                (0, 0),
                (14000, 0),
                (14000, 10000),
                (10000, 10000),
                (10000, 5000),
                (2000, 5000),
                (2000, 10000),
                (0, 10000),
            ),
            0,
        ),
        (
            "evaluation_long_windy_100",
            "evaluation",
            100.0,
            ((0, 0), (20000, 0), (20000, 5000), (0, 5000)),
            4,
        ),
        (
            "evaluation_diamond_100",
            "evaluation",
            100.0,
            ((0, 0), (10000, 5000), (20000, 0), (10000, -5000)),
            0,
        ),
    )
    result = []
    for name, split, area, vertices, wind in shapes:
        site = base.departure_site.model_copy(
            update={
                "coordinate": projection.unproject(-4000, -3000)
                if wind
                else projection.unproject(-500, -500),
            }
        )
        request = base.model_copy(
            update={
                "criterion": OptimizationCriterion.COMPLETION_TIME,
                "drones": fleet,
                "maximum_active_drones": 10,
                "turnaround_minutes": 3,
                "survey_parameters": parameters,
                "coverage_areas": [
                    base.coverage_areas[0].model_copy(
                        update={
                            "area_id": name,
                            "coordinates": [projection.unproject(x, y) for x, y in vertices],
                        }
                    )
                ],
                "departure_site": site,
                "landing_site": site,
                "wind": WindCondition(speed_mps=wind, direction_degrees=45) if wind else None,
                "flight_site_occupancy_seconds": 10,
            }
        )
        result.append(OptimizationScaleScenario(name, split, area, request))
    return result
