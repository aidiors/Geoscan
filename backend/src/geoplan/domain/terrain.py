from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field

from geoplan.domain.geo import Coordinate


class TerrainElevationSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    elevation_m: float = Field(ge=-500, le=10_000)


class InlineTerrainSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal["inline"] = "inline"
    samples: list[TerrainElevationSample] = Field(min_length=1, max_length=100_000)


class GeoTiffTerrainSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal["geotiff"] = "geotiff"
    content: Base64Bytes = Field(min_length=1, max_length=64 * 1024 * 1024)
    band: int = Field(default=1, ge=1, le=256)


TerrainSource = Annotated[
    InlineTerrainSource | GeoTiffTerrainSource,
    Field(discriminator="source_type"),
]


class VerticalProfilePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    distance_from_start_m: float = Field(ge=0)
    terrain_elevation_m: float = Field(ge=-500, le=10_000)
    target_altitude_msl: float = Field(ge=-500, le=100_000)
    target_agl_m: float = Field(gt=0, le=10_000)
    required_vertical_rate_mps: float = Field(ge=-100, le=100)
