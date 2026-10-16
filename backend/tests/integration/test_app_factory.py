from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies


def test_http_application_does_not_require_frontend_files(tmp_path: Path) -> None:
    missing_directory = tmp_path / "unbuilt-frontend"
    dependencies = replace(create_default_dependencies(), frontend_directory=missing_directory)

    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        assert not missing_directory.exists()
        assert client.get("/health").status_code == 200
        assert "/v1/plans" in client.get("/openapi.json").json()["paths"]
        assert client.get("/").status_code == 404
        assert client.get("/api-config.js").text == "window.GEOPLAN_API_BASE_URL = '';"
