"""Apply both aircraft altitude envelopes to canonical flight geometry."""

from dataclasses import dataclass

from geoplan.domain.enums import AltitudeReference
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission


@dataclass(frozen=True)
class FlightAltitudeEnvelope:
    def margins(self, drone: DroneSpec, mission: Mission) -> list[float]:
        lower = drone.minimum_altitude_for(mission.altitude_reference)
        upper = drone.maximum_altitude_for(mission.altitude_reference)
        margins = []
        if lower is not None:
            margins.append(mission.altitude_m - lower)
        if upper is not None:
            margins.append(upper - mission.altitude_m)
        height = mission.height_agl
        if drone.minimum_mission_altitude_agl_m is not None:
            margins.append(height.minimum_m - drone.minimum_mission_altitude_agl_m)
        if drone.maximum_mission_altitude_agl_m is not None:
            margins.append(drone.maximum_mission_altitude_agl_m - height.maximum_m)
        elevations = [p.target_altitude_msl for p in mission.vertical_profile]
        if not elevations and mission.altitude_reference is AltitudeReference.MSL:
            elevations = [mission.altitude_m]
        if drone.minimum_mission_altitude_msl_m is not None:
            margins.append(
                min(elevations) - drone.minimum_mission_altitude_msl_m if elevations else -1
            )
        if drone.maximum_mission_altitude_msl_m is not None:
            margins.append(
                drone.maximum_mission_altitude_msl_m - max(elevations) if elevations else -1
            )
        return margins

    def allows(self, drone: DroneSpec, mission: Mission) -> bool:
        return all(margin >= -1e-9 for margin in self.margins(drone, mission))
