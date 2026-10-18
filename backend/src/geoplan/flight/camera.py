from dataclasses import dataclass

from geoplan.domain.mission import Mission


@dataclass(frozen=True)
class DistanceCameraPolicy:
    def spacing_m(self, mission: Mission) -> float | None:
        if mission.photo_capture_route_indices is not None:
            return None
        if mission.camera_trigger_spacing_m is not None:
            return mission.camera_trigger_spacing_m
        if (
            mission.survey_distance_km is None
            or mission.image_count is None
            or mission.image_count <= 1
        ):
            return None
        return mission.survey_distance_km * 1000 / (mission.image_count - 1)
