"""Resolution of the executed mission, including any scheduled altitude shift."""

from dataclasses import dataclass

from geoplan.coverage.models import SensorProfile
from geoplan.domain.enums import AltitudeReference
from geoplan.domain.mission import Mission


@dataclass(frozen=True)
class SurveyResolution:
    def gsd_cm(self, mission: Mission, sensor: SensorProfile) -> float:
        # With terrain profiles, use a conservative maximum height over ground.
        # Flat MSL missions use the same ground-zero convention as coverage generation.
        height = max((p.target_agl_m for p in mission.vertical_profile), default=mission.altitude_m)
        if mission.altitude_reference is AltitudeReference.AGL and not mission.vertical_profile:
            height = mission.altitude_m
        return (
            height * sensor.sensor_width_mm / sensor.focal_length_mm / sensor.image_width_px * 100
        )

    def allows(self, mission: Mission, sensor: SensorProfile, maximum_gsd_cm: float) -> bool:
        return self.gsd_cm(mission, sensor) <= maximum_gsd_cm + 1e-9
