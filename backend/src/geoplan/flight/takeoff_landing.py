"""Sequential vertical departure and arrival at known surface heights.

This model is opt-in for multirotors. It assumes a clear vertical column at
each site and does not model acceleration or a fixed-wing approach corridor.
"""

from dataclasses import dataclass

from geoplan.domain.fleet import DroneSpec


@dataclass(frozen=True)
class TakeoffLandingEstimate:
    climb_minutes: float = 0
    descent_minutes: float = 0
    energy_surcharge_minutes: float = 0
    vertical_distance_m: float = 0

    @property
    def duration_minutes(self) -> float:
        return self.climb_minutes + self.descent_minutes

    @property
    def energy_equivalent_minutes(self) -> float:
        return self.duration_minutes + self.energy_surcharge_minutes


@dataclass(frozen=True)
class TakeoffLandingEstimator:
    def estimate(
        self, drone: DroneSpec, departure_height_m: float, landing_height_m: float
    ) -> TakeoffLandingEstimate:
        profile = drone.takeoff_landing_profile
        if profile is None:
            return TakeoffLandingEstimate()
        if min(departure_height_m, landing_height_m) <= 0:
            raise ValueError("Взлёт и посадка требуют положительной высоты над площадкой.")
        climb = departure_height_m / profile.climb_rate_mps / 60
        descent = landing_height_m / profile.descent_rate_mps / 60
        return TakeoffLandingEstimate(
            climb_minutes=climb,
            descent_minutes=descent,
            energy_surcharge_minutes=(
                climb * (profile.climb_energy_multiplier - 1)
                + descent * (profile.descent_energy_multiplier - 1)
            ),
            vertical_distance_m=departure_height_m + landing_height_m,
        )
