"""Resolution of the executed mission, including any scheduled altitude shift."""

from dataclasses import dataclass

from geoplan.coverage.models import SensorProfile
from geoplan.domain.mission import Mission


@dataclass(frozen=True)
class SurveyResolution:
    def gsd_cm(self, mission: Mission, sensor: SensorProfile) -> float:
        height = mission.height_agl.maximum_m
        return (
            height
            / sensor.focal_length_mm
            * max(
                sensor.sensor_width_mm / sensor.image_width_px,
                sensor.sensor_height_mm / sensor.image_height_px,
            )
            * 100
        )

    def allows(self, mission: Mission, sensor: SensorProfile, maximum_gsd_cm: float) -> bool:
        return self.gsd_cm(mission, sensor) <= maximum_gsd_cm + 1e-9
