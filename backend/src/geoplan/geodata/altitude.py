"""Translate canonical heights using MAVLink frames and QGC's altitude modes.

References:
https://mavlink.io/en/messages/common.html#MAV_FRAME
https://github.com/mavlink/qgroundcontrol/blob/v4.4.5/src/QmlControls/QGroundControlQmlGlobal.h
"""

from dataclasses import dataclass
from typing import Literal

from geoplan.domain.enums import AltitudeReference
from geoplan.domain.mission import Mission


@dataclass(frozen=True)
class AutopilotAltitudePolicy:
    def position_frame(self, mission: Mission) -> Literal[0, 10]:
        # MAV_FRAME_GLOBAL is MSL; frame 3 would reinterpret it relative to home.
        return 10 if mission.altitude_reference is AltitudeReference.AGL else 0

    def flight_altitude_m(self, mission: Mission) -> float:
        if mission.altitude_reference is AltitudeReference.AGL:
            return mission.altitude_m + mission.terrain_vertical_uncertainty_m
        return mission.altitude_m

    def landing_altitude_m(self, mission: Mission) -> float:
        return (
            0
            if mission.altitude_reference is AltitudeReference.AGL
            else mission.flat_ground_elevation_m
        )

    def home_elevation_msl_m(self, mission: Mission) -> float:
        if mission.vertical_profile:
            return mission.vertical_profile[0].terrain_elevation_m
        # Route points describe the flight path, not surveyed ground elevations.
        return mission.flat_ground_elevation_m

    def qgc_altitude_mode(self, frame: int) -> Literal[0, 2, 4]:
        # QGC AltitudeModeAbsolute=2 and AltitudeModeTerrainFrame=4.
        return 4 if frame == 10 else 2 if frame == 0 else 0
