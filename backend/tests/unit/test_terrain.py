from base64 import b64encode

import numpy as np
import pytest
from numpy.typing import NDArray
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import transform

from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import GeoTiffTerrainSource, InlineTerrainSource, TerrainElevationSample
from geoplan.flight.terrain import (
    CompositeTerrainProvider,
    GeoTiffTerrainProvider,
    InverseDistanceTerrainProvider,
    TerrainDataUnavailableError,
    TerrainFollowingProfilePlanner,
    VerticalRateExceededError,
)


def _sample(longitude: float, elevation_m: float) -> TerrainElevationSample:
    return TerrainElevationSample(
        coordinate=Coordinate(longitude=longitude, latitude=55),
        elevation_m=elevation_m,
    )


def _geotiff_source(values: NDArray[np.float32] | None = None) -> GeoTiffTerrainSource:
    xs, ys = transform("EPSG:4326", "EPSG:3857", [36.99, 37.01], [54.99, 55.01])
    raster_transform = from_bounds(xs[0], ys[0], xs[1], ys[1], 2, 2)
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs="EPSG:3857",
            transform=raster_transform,
            nodata=-9999,
        ) as dataset:
            dataset.write(
                values
                if values is not None
                else np.array([[120, 130], [100, 110]], dtype="float32"),
                1,
            )
        content = memory_file.read()
    return GeoTiffTerrainSource(content=b64encode(content))


def test_profile_preserves_agl_and_accounts_for_vertical_energy() -> None:
    planner = TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider())

    profile = planner.plan(
        route=[
            Coordinate(longitude=37, latitude=55),
            Coordinate(longitude=37.001, latitude=55),
        ],
        target_agl_m=120,
        maximum_ground_speed_kmh=10,
        source=InlineTerrainSource(samples=[_sample(37, 100), _sample(37.001, 130)]),
        sampling_interval_m=20,
        maximum_interpolation_distance_m=100,
        maximum_climb_rate_mps=5,
        maximum_descent_rate_mps=5,
        terrain_vertical_uncertainty_m=0,
    )

    assert profile.points[0].target_altitude_msl == 220
    assert profile.points[-1].target_altitude_msl == 250
    assert all(point.target_agl_m == 120 for point in profile.points)
    assert profile.vertical_distance_m == pytest.approx(30)
    assert profile.vertical_energy_equivalent_minutes == pytest.approx(0.1)
    assert profile.maximum_required_climb_rate_mps > 0


def test_profile_rejects_aircraft_vertical_rate_limit() -> None:
    planner = TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider())

    with pytest.raises(VerticalRateExceededError) as error:
        planner.plan(
            route=[
                Coordinate(longitude=37, latitude=55),
                Coordinate(longitude=37.001, latitude=55),
            ],
            target_agl_m=100,
            maximum_ground_speed_kmh=60,
            source=InlineTerrainSource(samples=[_sample(37, 0), _sample(37.001, 100)]),
            sampling_interval_m=20,
            maximum_interpolation_distance_m=100,
            maximum_climb_rate_mps=0.1,
            maximum_descent_rate_mps=0.1,
            terrain_vertical_uncertainty_m=0,
        )

    assert error.value.required_climb_rate_mps > 0.1


def test_profile_rejects_dem_gap() -> None:
    planner = TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider())

    with pytest.raises(TerrainDataUnavailableError):
        planner.plan(
            route=[
                Coordinate(longitude=37, latitude=55),
                Coordinate(longitude=37.01, latitude=55),
            ],
            target_agl_m=100,
            maximum_ground_speed_kmh=60,
            source=InlineTerrainSource(samples=[_sample(37, 0)]),
            sampling_interval_m=50,
            maximum_interpolation_distance_m=100,
            maximum_climb_rate_mps=5,
            maximum_descent_rate_mps=5,
            terrain_vertical_uncertainty_m=0,
        )


def test_geotiff_provider_reprojects_and_adds_vertical_uncertainty() -> None:
    planner = TerrainFollowingProfilePlanner(
        CompositeTerrainProvider(
            inline_provider=InverseDistanceTerrainProvider(),
            geotiff_provider=GeoTiffTerrainProvider(),
        )
    )

    profile = planner.plan(
        route=[
            Coordinate(longitude=36.995, latitude=54.995),
            Coordinate(longitude=37.005, latitude=55.005),
        ],
        target_agl_m=100,
        maximum_ground_speed_kmh=10,
        source=_geotiff_source(),
        sampling_interval_m=2_000,
        maximum_interpolation_distance_m=100,
        maximum_climb_rate_mps=5,
        maximum_descent_rate_mps=5,
        terrain_vertical_uncertainty_m=7,
    )

    assert profile.minimum_agl_m == 100
    assert all(point.target_agl_m == 107 for point in profile.points)
    assert profile.points[0].target_altitude_msl == 207
    assert profile.points[-1].target_altitude_msl == 237


@pytest.mark.parametrize(
    ("coordinate", "source"),
    [
        (Coordinate(longitude=38, latitude=55), _geotiff_source()),
        (
            Coordinate(longitude=37, latitude=55),
            _geotiff_source(np.full((2, 2), -9999, dtype="float32")),
        ),
    ],
)
def test_geotiff_provider_rejects_outside_and_nodata_cells(
    coordinate: Coordinate,
    source: GeoTiffTerrainSource,
) -> None:
    provider = GeoTiffTerrainProvider()

    with pytest.raises(TerrainDataUnavailableError):
        provider.elevations_m([coordinate], source, maximum_interpolation_distance_m=100)
