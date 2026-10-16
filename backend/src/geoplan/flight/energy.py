from __future__ import annotations

from geoplan.domain.fleet import DroneSpec


def _effective_endurance_minutes(drone: DroneSpec) -> float:
    return (
        drone.maximum_flight_minutes
        * drone.battery_health_percent
        / 100
        / drone.energy_consumption_multiplier
    )


def _battery_use_percent(energy_equivalent_minutes: float, drone: DroneSpec) -> float:
    return energy_equivalent_minutes / _effective_endurance_minutes(drone) * 100
