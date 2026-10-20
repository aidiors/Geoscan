from base64 import b64encode
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import GeoTiffTerrainSource
from geoplan.flight.geotiff import GeoTiffReader
from geoplan.flight.terrain import GeoTiffTerrainProvider, TerrainDataUnavailableError
from geoplan.flight.terrain_errors import InvalidTerrainSourceError
from geoplan.projects.repository import SqliteProjectRepository


def terrain_source(*, unit="m", crs="EPSG:4326", values=None, scale=2, offset=100):
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs=crs,
            transform=from_bounds(36.99, 54.99, 37.01, 55.01, 2, 2),
            nodata=-9999,
        ) as dataset:
            dataset.write(
                np.array(
                    values if values is not None else [[10, 20], [30, -9999]], dtype="float32"
                ),
                1,
            )
            dataset.scales = [scale]
            dataset.offsets = [offset]
            dataset.set_band_unit(1, unit)
        return GeoTiffTerrainSource(content=b64encode(memory.read()))


@pytest.fixture
def client(tmp_path):
    dependencies = replace(
        create_default_dependencies(),
        project_repository=SqliteProjectRepository(tmp_path / "projects.sqlite3"),
    )
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        yield client


def test_inspection_and_planner_use_same_scaled_elevations_and_raw_checksum(client):
    source = terrain_source()
    response = client.post("/v1/terrain/inspect", json=source.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    info = response.json()
    assert info["sha256"] == sha256(source.content).hexdigest()
    assert info["valid_fraction"] == 0.75
    assert info["minimum_elevation_m"] == 120
    assert info["maximum_elevation_m"] == 160
    assert info["bounds_wgs84"] == pytest.approx([36.99, 54.99, 37.01, 55.01])
    assert GeoTiffTerrainProvider().elevations_m(
        [Coordinate(longitude=36.995, latitude=55.005)], source, 5
    ) == [120]
    with pytest.raises(TerrainDataUnavailableError):
        GeoTiffTerrainProvider().elevations_m(
            [Coordinate(longitude=37.005, latitude=54.995)], source, 5000
        )
    saved = client.post(
        "/v1/projects",
        json={
            "name": "DEM",
            "document": {"editor_state": {"terrain": source.model_dump(mode="json")}},
        },
    )
    assert saved.status_code == 200, saved.text
    restored = client.get(f"/v1/projects/{saved.json()['project_id']}")
    assert restored.json()["document"]["editor_state"]["terrain"] == source.model_dump(mode="json")


@pytest.mark.parametrize(
    "source",
    [terrain_source(unit="ft"), terrain_source(crs=None), terrain_source(values=[[-9999] * 2] * 2)],
)
def test_inspection_rejects_incompatible_or_empty_raster(client, source):
    response = client.post("/v1/terrain/inspect", json=source.model_dump(mode="json"))
    assert response.status_code == 422
    assert response.json()["code"] == "terrain_data_unavailable"


def test_inspection_rejects_corrupt_file_or_missing_band(client):
    source = terrain_source()
    for invalid in [
        source.model_copy(update={"band": 2}),
        GeoTiffTerrainSource(content=b64encode(b"bad TIFF")),
    ]:
        response = client.post("/v1/terrain/inspect", json=invalid.model_dump(mode="json"))
        assert response.status_code == 422


def test_provider_rejects_non_metre_units_without_silent_conversion():
    with pytest.raises(InvalidTerrainSourceError, match="метрах"):
        GeoTiffTerrainProvider().elevations_m(
            [Coordinate(longitude=37, latitude=55)], terrain_source(unit="ft"), 5
        )
    with pytest.raises(InvalidTerrainSourceError, match="пикселей"):
        GeoTiffReader(maximum_raster_pixels=3).inspect(terrain_source())
