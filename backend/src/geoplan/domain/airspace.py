from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.enums import AltitudeMode, AltitudeReference, ImportedGeometryType, ValidityMode
from geoplan.domain.geo import Coordinate


class RestrictionValidity(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ValidityMode
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def time_window_is_complete(self) -> RestrictionValidity:
        if self.mode is ValidityMode.PERMANENT:
            return self
        if self.valid_from is None or self.valid_to is None:
            raise ValueError("Для временного ограничения нужны начало и окончание действия.")
        if self.valid_from.utcoffset() is None or self.valid_to.utcoffset() is None:
            raise ValueError("Временное ограничение должно содержать часовой пояс.")
        if self.valid_to <= self.valid_from:
            raise ValueError("Окончание ограничения должно быть позже начала.")
        return self


class AllowedAirspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    airspace_id: str = Field(min_length=1, max_length=64)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)
    minimum_altitude_m: float = Field(ge=-500, le=100_000)
    maximum_altitude_m: float = Field(gt=-500, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    validity: RestrictionValidity | None = None

    @model_validator(mode="after")
    def maximum_altitude_exceeds_minimum(self) -> AllowedAirspace:
        if self.maximum_altitude_m <= self.minimum_altitude_m:
            raise ValueError("Максимальная высота должна быть больше минимальной.")
        return self


class NoFlyZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_id: str = Field(min_length=1, max_length=64)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)
    interior_rings: list[list[Coordinate]] = Field(default_factory=list, max_length=1_000)
    safety_margin_m: float = Field(default=30, ge=0, le=5_000)
    minimum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    maximum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    validity: RestrictionValidity | None = None

    @model_validator(mode="after")
    def altitude_range_is_complete(self) -> NoFlyZone:
        if (self.minimum_altitude_m is None) != (self.maximum_altitude_m is None):
            raise ValueError("Высотный диапазон запретной зоны требует нижнюю и верхнюю границы.")
        if (
            self.minimum_altitude_m is not None
            and self.maximum_altitude_m is not None
            and self.maximum_altitude_m <= self.minimum_altitude_m
        ):
            raise ValueError("Верхняя граница запретной зоны должна быть выше нижней.")
        return self


class FlightObstacle(BaseModel):
    model_config = ConfigDict(frozen=True)

    obstacle_id: str = Field(min_length=1, max_length=64)
    geometry_type: ImportedGeometryType
    coordinates: list[Coordinate] = Field(min_length=1, max_length=100_000)
    height_m: float = Field(ge=0, le=100_000)
    altitude_mode: AltitudeMode = AltitudeMode.UNKNOWN
    safety_radius_m: float = Field(default=30, gt=0, le=5_000)
