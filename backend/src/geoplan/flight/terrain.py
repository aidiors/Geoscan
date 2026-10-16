from __future__ import annotations

from dataclasses import dataclass
from heapq import nsmallest
from itertools import pairwise
from math import ceil, hypot
from typing import Protocol

from rasterio.errors import RasterioError
from rasterio.io import MemoryFile
from rasterio.warp import transform

from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import (
    GeoTiffTerrainSource,
    InlineTerrainSource,
    TerrainSource,
    VerticalProfilePoint,
)
from geoplan.navigation.geometry import projection_for_coordinates


class TerrainDataUnavailableError(RuntimeError):
    def __init__(self, coordinate: Coordinate, nearest_distance_m: float) -> None:
        self.coordinate = coordinate
        self.nearest_distance_m = nearest_distance_m
        super().__init__("Для точки маршрута нет достаточно близкой отметки рельефа.")


class VerticalRateExceededError(RuntimeError):
    def __init__(
        self,
        required_climb_rate_mps: float,
        required_descent_rate_mps: float,
        maximum_climb_rate_mps: float,
        maximum_descent_rate_mps: float,
    ) -> None:
        self.required_climb_rate_mps = required_climb_rate_mps
        self.required_descent_rate_mps = required_descent_rate_mps
        self.maximum_climb_rate_mps = maximum_climb_rate_mps
        self.maximum_descent_rate_mps = maximum_descent_rate_mps
        super().__init__("Вертикальный профиль превышает предел набора или снижения.")


class InvalidTerrainSourceError(RuntimeError):
    pass


class TerrainProvider(Protocol):
    def elevations_m(
        self,
        coordinates: list[Coordinate],
        source: TerrainSource,
        maximum_interpolation_distance_m: float,
    ) -> list[float]: ...


@dataclass(frozen=True)
class InverseDistanceTerrainProvider:
    neighbour_count: int = 4

    def elevations_m(
        self,
        coordinates: list[Coordinate],
        source: TerrainSource,
        maximum_interpolation_distance_m: float,
    ) -> list[float]:
        if not isinstance(source, InlineTerrainSource):
            raise InvalidTerrainSourceError("Inverse-distance provider requires inline samples.")
        samples = source.samples
        projection = projection_for_coordinates(
            [*coordinates, *(sample.coordinate for sample in samples)]
        )
        projected_samples = [
            (*projection.project(sample.coordinate), sample.elevation_m) for sample in samples
        ]
        elevations: list[float] = []
        for coordinate in coordinates:
            x, y = projection.project(coordinate)
            nearest = nsmallest(
                self.neighbour_count,
                (
                    (hypot(sample_x - x, sample_y - y), elevation)
                    for sample_x, sample_y, elevation in projected_samples
                ),
                key=lambda item: item[0],
            )
            if not nearest or nearest[0][0] > maximum_interpolation_distance_m:
                nearest_distance = nearest[0][0] if nearest else float("inf")
                raise TerrainDataUnavailableError(coordinate, nearest_distance)
            exact = next((elevation for distance, elevation in nearest if distance <= 1e-6), None)
            if exact is not None:
                elevations.append(exact)
                continue
            weights = [(1 / distance**2, elevation) for distance, elevation in nearest]
            elevations.append(
                sum(weight * elevation for weight, elevation in weights)
                / sum(weight for weight, _ in weights)
            )
        return elevations


@dataclass(frozen=True)
class GeoTiffTerrainProvider:
    maximum_raster_pixels: int = 25_000_000

    def elevations_m(
        self,
        coordinates: list[Coordinate],
        source: TerrainSource,
        maximum_interpolation_distance_m: float,
    ) -> list[float]:
        del maximum_interpolation_distance_m
        if not isinstance(source, GeoTiffTerrainSource):
            raise InvalidTerrainSourceError("GeoTIFF provider requires a GeoTIFF source.")
        try:
            with MemoryFile(source.content) as memory_file, memory_file.open() as dataset:
                if dataset.driver != "GTiff":
                    raise InvalidTerrainSourceError("Terrain source is not a GeoTIFF dataset.")
                if dataset.crs is None:
                    raise InvalidTerrainSourceError("GeoTIFF terrain source has no CRS.")
                if source.band > dataset.count:
                    raise InvalidTerrainSourceError("Requested terrain band does not exist.")
                if dataset.width * dataset.height > self.maximum_raster_pixels:
                    raise InvalidTerrainSourceError("Decoded GeoTIFF exceeds the pixel limit.")
                xs, ys = transform(
                    "EPSG:4326",
                    dataset.crs,
                    [coordinate.longitude for coordinate in coordinates],
                    [coordinate.latitude for coordinate in coordinates],
                )
                band = dataset.read(source.band, masked=True)
                elevations: list[float] = []
                for coordinate, x, y in zip(coordinates, xs, ys, strict=True):
                    row, column = dataset.index(x, y)
                    if row < 0 or row >= dataset.height or column < 0 or column >= dataset.width:
                        raise TerrainDataUnavailableError(coordinate, float("inf"))
                    value = band[row, column]
                    if bool(getattr(value, "mask", False)):
                        raise TerrainDataUnavailableError(coordinate, float("inf"))
                    elevation = float(value)
                    if not -500 <= elevation <= 10_000:
                        raise InvalidTerrainSourceError(
                            "GeoTIFF elevation is outside supported limits."
                        )
                    elevations.append(elevation)
                return elevations
        except InvalidTerrainSourceError, TerrainDataUnavailableError:
            raise
        except (RasterioError, OSError, ValueError) as error:
            raise InvalidTerrainSourceError("Could not read the GeoTIFF terrain source.") from error


@dataclass(frozen=True)
class CompositeTerrainProvider:
    inline_provider: TerrainProvider
    geotiff_provider: TerrainProvider

    def elevations_m(
        self,
        coordinates: list[Coordinate],
        source: TerrainSource,
        maximum_interpolation_distance_m: float,
    ) -> list[float]:
        provider = (
            self.inline_provider
            if isinstance(source, InlineTerrainSource)
            else self.geotiff_provider
        )
        return provider.elevations_m(
            coordinates,
            source,
            maximum_interpolation_distance_m,
        )


@dataclass(frozen=True)
class VerticalProfileResult:
    points: list[VerticalProfilePoint]
    minimum_agl_m: float
    minimum_terrain_elevation_m: float
    maximum_terrain_elevation_m: float
    maximum_required_climb_rate_mps: float
    maximum_required_descent_rate_mps: float
    vertical_distance_m: float
    vertical_energy_equivalent_minutes: float


class VerticalProfilePlanner(Protocol):
    def plan(
        self,
        route: list[Coordinate],
        target_agl_m: float,
        maximum_ground_speed_kmh: float,
        source: TerrainSource,
        sampling_interval_m: float,
        maximum_interpolation_distance_m: float,
        maximum_climb_rate_mps: float,
        maximum_descent_rate_mps: float,
        terrain_vertical_uncertainty_m: float,
    ) -> VerticalProfileResult: ...


@dataclass(frozen=True)
class TerrainFollowingProfilePlanner:
    terrain_provider: TerrainProvider
    maximum_profile_points: int = 100_000

    def plan(
        self,
        route: list[Coordinate],
        target_agl_m: float,
        maximum_ground_speed_kmh: float,
        source: TerrainSource,
        sampling_interval_m: float,
        maximum_interpolation_distance_m: float,
        maximum_climb_rate_mps: float,
        maximum_descent_rate_mps: float,
        terrain_vertical_uncertainty_m: float,
    ) -> VerticalProfileResult:
        sampled_route, cumulative_distances = _sample_route(
            route,
            sampling_interval_m,
            self.maximum_profile_points,
        )
        elevations = self.terrain_provider.elevations_m(
            sampled_route,
            source,
            maximum_interpolation_distance_m,
        )
        target_altitudes = [
            elevation + target_agl_m + terrain_vertical_uncertainty_m for elevation in elevations
        ]
        climb_rates: list[float] = [0]
        for index in range(1, len(sampled_route)):
            leg_distance_m = cumulative_distances[index] - cumulative_distances[index - 1]
            leg_seconds = leg_distance_m / (maximum_ground_speed_kmh / 3.6)
            climb_rates.append(
                (target_altitudes[index] - target_altitudes[index - 1]) / leg_seconds
            )
        maximum_required_climb = max(climb_rates, default=0)
        maximum_required_descent = max((-rate for rate in climb_rates), default=0)
        if (
            maximum_required_climb > maximum_climb_rate_mps + 1e-9
            or maximum_required_descent > maximum_descent_rate_mps + 1e-9
        ):
            raise VerticalRateExceededError(
                maximum_required_climb,
                maximum_required_descent,
                maximum_climb_rate_mps,
                maximum_descent_rate_mps,
            )
        ascent_m = sum(max(0, end - start) for start, end in pairwise(target_altitudes))
        descent_m = sum(max(0, start - end) for start, end in pairwise(target_altitudes))
        vertical_distance_m = ascent_m + descent_m
        energy_equivalent_minutes = (
            ascent_m / maximum_climb_rate_mps + descent_m / maximum_descent_rate_mps
        ) / 60
        return VerticalProfileResult(
            points=[
                VerticalProfilePoint(
                    coordinate=coordinate.model_copy(update={"altitude_m": altitude}),
                    distance_from_start_m=round(distance, 3),
                    terrain_elevation_m=round(elevation, 3),
                    target_altitude_msl=round(altitude, 3),
                    target_agl_m=target_agl_m + terrain_vertical_uncertainty_m,
                    required_vertical_rate_mps=round(rate, 3),
                )
                for coordinate, distance, elevation, altitude, rate in zip(
                    sampled_route,
                    cumulative_distances,
                    elevations,
                    target_altitudes,
                    climb_rates,
                    strict=True,
                )
            ],
            minimum_agl_m=target_agl_m,
            minimum_terrain_elevation_m=min(elevations),
            maximum_terrain_elevation_m=max(elevations),
            maximum_required_climb_rate_mps=maximum_required_climb,
            maximum_required_descent_rate_mps=maximum_required_descent,
            vertical_distance_m=vertical_distance_m,
            vertical_energy_equivalent_minutes=energy_equivalent_minutes,
        )


def _sample_route(
    route: list[Coordinate],
    sampling_interval_m: float,
    maximum_profile_points: int,
) -> tuple[list[Coordinate], list[float]]:
    projection = projection_for_coordinates(route)
    sampled = [route[0]]
    cumulative = [0.0]
    total_distance = 0.0
    for start, end in pairwise(route):
        start_xy = projection.project(start)
        end_xy = projection.project(end)
        distance_m = hypot(end_xy[0] - start_xy[0], end_xy[1] - start_xy[1])
        step_count = max(1, ceil(distance_m / sampling_interval_m))
        for step in range(1, step_count + 1):
            fraction = step / step_count
            sampled.append(
                Coordinate(
                    longitude=start.longitude + (end.longitude - start.longitude) * fraction,
                    latitude=start.latitude + (end.latitude - start.latitude) * fraction,
                )
            )
            total_distance += distance_m / step_count
            cumulative.append(total_distance)
            if len(sampled) > maximum_profile_points:
                raise ValueError("Вертикальный профиль превышает допустимое число точек.")
    if total_distance <= 0:
        raise ValueError("Для вертикального профиля нужен маршрут ненулевой длины.")
    return sampled, cumulative
