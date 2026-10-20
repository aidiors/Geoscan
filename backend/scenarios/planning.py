"""Reproducible planning requests shared by integration tests and experiments."""

from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SweepDirectionMode
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanRequest


def productive_survey_request(**overrides) -> PlanRequest:
    projection = LocalProjection(37, 55)
    home = FlightSite(site_id="base", name="Base", coordinate=projection.unproject(-500, -500))
    return PlanRequest.model_validate(
        {
            "criterion": OptimizationCriterion.TOTAL_FLIGHT_TIME,
            "productive_routing": True,
            "drones": [
                DroneSpec(
                    drone_id="fast",
                    name="Survey",
                    cruise_speed_kmh=90,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            "coverage_areas": [
                CoverageArea(
                    area_id="field",
                    name="Field",
                    coordinates=[
                        projection.unproject(x, y)
                        for x, y in ((0, 0), (1200, 0), (1200, 600), (0, 600))
                    ],
                )
            ],
            "survey_parameters": SurveyParameters(
                direction_mode=SweepDirectionMode.MANUAL,
                flight_direction_degrees=90,
                side_overlap_percent=60,
                forward_overlap_percent=75,
                maximum_gsd_cm=5,
                strip_width_m=200,
                maximum_segment_distance_km=1.5,
                sensor=SensorProfile(
                    sensor_id="camera",
                    name="Camera",
                    sensor_width_mm=13.2,
                    sensor_height_mm=8.8,
                    focal_length_mm=8.8,
                    image_width_px=5472,
                    image_height_px=3648,
                    minimum_trigger_interval_s=4,
                ),
            ),
            "mission_altitude_m": 120,
            "task": MissionTask.RGB,
            "departure_site": home,
            "landing_site": home,
            "maximum_active_drones": 1,
            **overrides,
        }
    )


def parallel_survey_request(**overrides) -> PlanRequest:
    """Long narrow area where two sorties may beat one resource-feasible flight."""
    base = productive_survey_request()
    projection = LocalProjection(37, 55)
    assert base.departure_site is not None and base.survey_parameters is not None
    site = base.departure_site.model_copy(update={"coordinate": projection.unproject(-1000, 0)})
    area = base.coverage_areas[0].model_copy(
        update={
            "coordinates": [
                projection.unproject(x, y) for x, y in ((0, 0), (10000, 0), (10000, 280), (0, 280))
            ]
        }
    )
    survey = base.survey_parameters.model_copy(
        update={
            "maximum_segment_distance_km": 500,
            "sensor": base.survey_parameters.sensor.model_copy(
                update={"minimum_trigger_interval_s": 0.5}
            ),
        }
    )
    drones = [
        base.drones[0].model_copy(
            update={
                "drone_id": name,
                "cruise_speed_kmh": speed,
                "maximum_flight_minutes": endurance,
            }
        )
        for name, speed, endurance in (("fast", 80, 75), ("slower", 70, 62.5))
    ]
    return PlanRequest.model_validate(
        {
            **base.model_dump(),
            "drones": drones,
            "departure_site": site,
            "landing_site": site,
            "coverage_areas": [area],
            "survey_parameters": survey,
            "criterion": OptimizationCriterion.COMPLETION_TIME,
            "maximum_active_drones": 2,
            **overrides,
        }
    )
