import copy
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import OptimizationMode
from geoplan.projects.repository import SqliteProjectRepository
from scenarios.planning import productive_survey_request


def client_at(path):
    dependencies = replace(
        create_default_dependencies(), project_repository=SqliteProjectRepository(path)
    )
    return TestClient(create_app(dependencies, serve_frontend=False))


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "workspace" / "projects.sqlite3"
    with client_at(path) as client:
        request = productive_survey_request(
            productive_routing=False, optimization_mode=OptimizationMode.FAST_HEURISTIC
        )
        planned = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert planned.status_code == 200
        document = {
            "schema_version": 1,
            "editor_state": {"version": 3, "tag": "first"},
            "source_files": [
                {
                    "name": "границы.kml",
                    "zone_type": "work_area",
                    "content": "<kml>исходные данные</kml>",
                }
            ],
            "request": request.model_dump(mode="json"),
            "plan": planned.json(),
        }
        created = client.post("/v1/projects", json={"name": "Первый проект", "document": document})
        assert created.status_code == 200, created.text
        yield client, path, created.json(), document


def test_revisions_survive_restart_and_old_versions_are_immutable(project):
    client, path, first, document = project
    identifier = first["project_id"]
    assert first["document"]["plan"]["export_authorization"] is None
    assert (
        first["document"]["source_files"][0]["sha256"]
        == hashlib.sha256(document["source_files"][0]["content"].encode()).hexdigest()
    )
    changed = copy.deepcopy(document)
    changed["editor_state"]["tag"] = "second"
    second = client.post(
        f"/v1/projects/{identifier}/revisions",
        json={"name": "Новое имя", "document": changed, "parent_revision": 1},
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2
    with client_at(path) as restarted:
        assert (
            restarted.get(f"/v1/projects/{identifier}").json()["document"]["editor_state"]["tag"]
            == "second"
        )
        old = restarted.get(f"/v1/projects/{identifier}?revision=1").json()
        assert old["name"] == "Первый проект"
        assert old["document"]["editor_state"]["tag"] == "first"
        assert old["document_sha256"] == first["document_sha256"]
        assert [
            r["latest_revision"]
            for r in restarted.get(f"/v1/projects/{identifier}/revisions").json()
        ] == [2, 1]


def test_two_editors_cannot_silently_overwrite_each_other(project):
    client, _, first, document = project
    url = f"/v1/projects/{first['project_id']}/revisions"

    def save(name):
        return client.post(url, json={"name": name, "document": document, "parent_revision": 1})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["Окно A", "Окно B"]))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(client.get(url).json()) == 2


def test_portable_project_roundtrip_preserves_sources_but_never_releases_flight(project, tmp_path):
    client, _, first, document = project
    portable = client.post("/v1/projects/portable", json={"name": "Перенос", "document": document})
    assert portable.status_code == 200, portable.text
    with client_at(tmp_path / "other-server.sqlite3") as other:
        imported = other.post("/v1/projects/import", json=portable.json())
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result["document_sha256"] == portable.json()["document_sha256"]
        assert result["project_id"] != first["project_id"]
        assert (
            result["document"]["source_files"][0]["content"]
            == document["source_files"][0]["content"]
        )
        exported = other.post(
            "/v1/exports/qgroundcontrol",
            json={"plan": result["document"]["plan"], "drone_id": "fast", "sortie_index": 0},
        )
        assert exported.status_code == 422
        assert exported.json()["code"] == "export_release_required"


def test_portable_checksum_survives_browser_number_and_key_normalization(project):
    client, _, _, document = project
    document["editor_state"]["integer_as_float"] = 1.0
    portable = client.post(
        "/v1/projects/portable", json={"name": "Browser", "document": document}
    ).json()
    portable["document"]["editor_state"]["integer_as_float"] = 1
    portable["document"]["editor_state"] = dict(
        reversed(list(portable["document"]["editor_state"].items()))
    )
    imported = client.post("/v1/projects/import", json=portable)
    assert imported.status_code == 200, imported.text


def test_corrupted_input_never_replaces_an_existing_revision(project):
    client, _, first, document = project
    portable = client.post(
        "/v1/projects/portable", json={"name": "Перенос", "document": document}
    ).json()
    portable["document"]["editor_state"]["tag"] = "corrupted"
    assert client.post("/v1/projects/import", json=portable).status_code == 422
    for field in ["request", "source_files"]:
        changed = copy.deepcopy(document)
        if field == "request":
            changed["request"]["mission_altitude_m"] += 10
        else:
            changed["source_files"][0]["sha256"] = "0" * 64
        assert (
            client.post(
                f"/v1/projects/{first['project_id']}/revisions",
                json={"name": "Bad", "document": changed, "parent_revision": 1},
            ).status_code
            == 422
        )
    assert len(client.get("/v1/projects").json()) == 1
    assert client.get(f"/v1/projects/{first['project_id']}").json()["revision"] == 1


def test_transaction_rolls_back_head_when_revision_cannot_be_written(project):
    client, path, first, document = project
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_revision BEFORE INSERT ON revisions "
            "BEGIN SELECT RAISE(ABORT, 'disk-failure-test'); END"
        )
    result = client.post(
        f"/v1/projects/{first['project_id']}/revisions",
        json={"name": "Not committed", "document": document, "parent_revision": 1},
    )
    assert result.status_code == 503
    latest = client.get(f"/v1/projects/{first['project_id']}").json()
    assert latest["revision"] == 1
    assert latest["name"] == "Первый проект"


def test_oversized_revision_does_not_mutate_saved_data(project, monkeypatch):
    client, _, first, document = project
    monkeypatch.setattr("geoplan.projects.repository.MAXIMUM_DOCUMENT_BYTES", 10)
    result = client.post(
        f"/v1/projects/{first['project_id']}/revisions",
        json={"name": "Oversized", "document": document, "parent_revision": 1},
    )
    assert result.status_code == 422
    assert client.get(f"/v1/projects/{first['project_id']}").json()["revision"] == 1
