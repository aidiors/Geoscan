"""Resolve installed camera and conservative common survey grid for a mixed fleet."""

from dataclasses import dataclass

from geoplan.coverage.models import SurveyParameters
from geoplan.domain.fleet import DroneSpec
from geoplan.planning.models import PlanRequest


@dataclass(frozen=True)
class FleetSurveyPolicy:
    def for_drone(self, request: PlanRequest, drone: DroneSpec) -> PlanRequest:
        survey = request.survey_parameters
        return request.model_copy(
            update={
                "minimum_turn_radius_m": max(
                    request.minimum_turn_radius_m, drone.minimum_turn_radius_m
                ),
                "survey_parameters": survey.model_copy(update={"sensor": drone.installed_sensor})
                if survey is not None and drone.installed_sensor is not None
                else survey,
            }
        )

    def grid_parameters(self, request: PlanRequest, drones: list[DroneSpec]) -> SurveyParameters:
        survey = request.survey_parameters
        assert survey is not None
        cameras = [drone.installed_sensor or survey.sensor for drone in drones]
        if not any(drone.installed_sensor is not None for drone in drones):
            return survey
        # One shared grid preserves transferability of strips between aircraft.
        # Larger footprints deliberately oversample it; camera-specific speeds and
        # triggers are resolved later when constructing each physical mission.
        reference = min(
            cameras,
            key=lambda camera: (
                camera.sensor_width_mm / camera.focal_length_mm / camera.image_width_px
            ),
        )
        narrowest_swath = min(
            request.survey_height_agl.minimum_m * camera.sensor_width_mm / camera.focal_length_mm
            for camera in cameras
        )
        return survey.model_copy(
            update={
                "sensor": reference,
                "strip_width_m": min(survey.strip_width_m, narrowest_swath),
            }
        )
