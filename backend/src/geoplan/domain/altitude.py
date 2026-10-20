"""Height above ground is distinct from an aircraft's absolute elevation."""

from dataclasses import dataclass

from geoplan.domain.enums import AltitudeReference


@dataclass(frozen=True)
class HeightAboveGround:
    minimum_m: float
    maximum_m: float

    def __post_init__(self) -> None:
        if self.minimum_m <= 0 or self.maximum_m < self.minimum_m:
            raise ValueError("Высота над поверхностью должна быть положительной во всём диапазоне.")

    @classmethod
    def for_target(
        cls,
        altitude_m: float,
        reference: AltitudeReference,
        flat_ground_elevation_m: float,
        terrain_uncertainty_m: float,
    ) -> HeightAboveGround:
        if reference is AltitudeReference.AGL:
            # The terrain-following controller adds +u to the nominal command.
            # With ground error +/-u, actual clearance is [H, H + 2u].
            return cls(altitude_m, altitude_m + 2 * terrain_uncertainty_m)
        height = altitude_m - flat_ground_elevation_m
        return cls(height - terrain_uncertainty_m, height + terrain_uncertainty_m)
