import json

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies


@pytest.mark.parametrize("geometry_issue", ["outer", "hole", "missing_hole"])
def test_kml_rejects_malformed_geometry_without_partial_import(geometry_issue: str) -> None:
    boundary = "37.4,55.7 37.5,55.7 37.5,55.8 37.4,55.7"
    invalid = "37.42,55.72 37.43,INVALID 37.43,55.73"

    def polygon(outer: str, hole: str = "") -> str:
        inner = (
            f"<innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates>"
            "</LinearRing></innerBoundaryIs>"
            if hole
            else ""
        )
        return (
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates>"
            f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
        )

    malformed = polygon(boundary, invalid) if geometry_issue == "hole" else polygon(invalid)
    if geometry_issue == "missing_hole":
        malformed = polygon(boundary).replace(
            "</Polygon>", "<innerBoundaryIs><LinearRing/></innerBoundaryIs></Polygon>"
        )
    content = (
        f"<kml><Placemark>{polygon(boundary)}</Placemark><Placemark>{malformed}</Placemark></kml>"
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post(
            "/v1/imports/kml",
            json={
                "source_file_name": "restrictions.kml",
                "zone_type": "no_fly_zone",
                "content": content,
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_import"


@pytest.mark.parametrize("geometry_type", ["Polygon", "MultiPolygon"])
def test_geojson_import_normalizes_long_names(geometry_type: str) -> None:
    ring = [[37.4, 55.7], [37.5, 55.7], [37.5, 55.8], [37.4, 55.7]]
    coordinates = [ring] if geometry_type == "Polygon" else [[ring], [ring]]
    content = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Область " * 32},
                "geometry": {"type": geometry_type, "coordinates": coordinates},
            }
        ],
    }
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post(
            "/v1/imports/geojson",
            json={
                "source_file_name": "areas.geojson",
                "zone_type": "work_area",
                "content": json.dumps(content),
            },
        )
    assert response.status_code == 200, response.text
    zones = response.json()["zones"]
    assert len(zones) == (1 if geometry_type == "Polygon" else 2)
    assert all(0 < len(zone["name"]) <= 128 for zone in zones)
    if geometry_type == "MultiPolygon":
        assert zones[0]["name"] != zones[1]["name"]
