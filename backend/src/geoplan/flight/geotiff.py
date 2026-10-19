# ruff: noqa: RUF001
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

import numpy as np
from pydantic import BaseModel
from rasterio.enums import ColorInterp
from rasterio.errors import RasterioError
from rasterio.io import DatasetReader, MemoryFile
from rasterio.warp import transform_bounds

from geoplan.domain.terrain import GeoTiffTerrainSource
from geoplan.flight.terrain_errors import InvalidTerrainSourceError


class TerrainInspection(BaseModel):
    sha256: str
    width: int
    height: int
    band_count: int
    band: int
    crs: str
    unit: str | None
    scale: float
    offset: float
    minimum_elevation_m: float
    maximum_elevation_m: float
    valid_fraction: float
    bounds_wgs84: tuple[float, float, float, float]


@dataclass(frozen=True)
class GeoTiffReader:
    maximum_raster_pixels: int = 25_000_000

    def read_band(self, dataset: DatasetReader, band: int) -> np.ma.MaskedArray:
        if dataset.driver != "GTiff" or dataset.crs is None:
            raise InvalidTerrainSourceError("Нужен GeoTIFF с заданной системой координат CRS.")
        if band > dataset.count:
            raise InvalidTerrainSourceError("Выбранный канал отсутствует в GeoTIFF.")
        if dataset.width * dataset.height > self.maximum_raster_pixels:
            raise InvalidTerrainSourceError("GeoTIFF превышает предел 25 млн пикселей.")
        if dataset.colorinterp[band - 1] in {
            ColorInterp.red,
            ColorInterp.green,
            ColorInterp.blue,
            ColorInterp.alpha,
            ColorInterp.palette,
        }:
            raise InvalidTerrainSourceError(
                "Выбран цветовой канал изображения; нужен канал высот DEM."
            )
        unit = (dataset.units[band - 1] or "").strip().lower()
        if unit not in {"", "m", "metre", "meter", "metres", "meters"}:
            raise InvalidTerrainSourceError(
                "Высоты DEM должны быть в метрах; конверсия единиц отсутствует."
            )
        scale, offset = dataset.scales[band - 1], dataset.offsets[band - 1]
        if not isfinite(scale) or not isfinite(offset) or scale == 0:
            raise InvalidTerrainSourceError("Некорректные scale/offset канала высот.")
        if np.dtype(dataset.dtypes[band - 1]).kind not in "iuf":
            raise InvalidTerrainSourceError("Канал высот должен содержать вещественные числа.")
        # Apply calibration in place to avoid several full raster-sized float64 copies.
        values = dataset.read(band, masked=True, out_dtype="float64")
        if scale != 1:
            values *= scale
        if offset != 0:
            values += offset
        values = np.ma.masked_invalid(values, copy=False)
        if values.count() and (float(values.min()) < -500 or float(values.max()) > 10_000):
            raise InvalidTerrainSourceError(
                "Высоты DEM вне поддерживаемого диапазона −500…10 000 м."
            )
        return values

    def inspect(self, source: GeoTiffTerrainSource) -> TerrainInspection:
        try:
            with MemoryFile(source.content) as memory, memory.open() as dataset:
                values = self.read_band(dataset, source.band)
                if values.count() == 0:
                    raise InvalidTerrainSourceError("Нет действительных отметок в канале высот.")
                bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds)
                if not all(isfinite(value) for value in bounds):
                    raise InvalidTerrainSourceError("Не удалось определить границы GeoTIFF.")
                return TerrainInspection(
                    sha256=sha256(source.content).hexdigest(),
                    width=dataset.width,
                    height=dataset.height,
                    band_count=dataset.count,
                    band=source.band,
                    crs=str(dataset.crs),
                    unit=dataset.units[source.band - 1],
                    scale=dataset.scales[source.band - 1],
                    offset=dataset.offsets[source.band - 1],
                    minimum_elevation_m=float(values.min()),
                    maximum_elevation_m=float(values.max()),
                    valid_fraction=values.count() / values.size,
                    bounds_wgs84=bounds,
                )
        except InvalidTerrainSourceError:
            raise
        except (RasterioError, OSError, ValueError) as error:
            raise InvalidTerrainSourceError("Не удалось прочитать GeoTIFF.") from error
