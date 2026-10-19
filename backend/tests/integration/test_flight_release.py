import copy
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import OptimizationMode
from geoplan.errors import PlanningError
from geoplan.geodata.release import PlanReleaseAuthority
from geoplan.planning.models import PlanResponse
from scenarios.planning import productive_survey_request


@pytest.fixture(scope="module")
def released():
    request = productive_survey_request(
        productive_routing=False,
        optimization_mode=OptimizationMode.FAST_HEURISTIC,
        turnaround_minutes=3,
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["export_authorization"]
        assert len(plan["drone_plans"][0]["missions"]) > 1
        yield client, plan


@pytest.mark.parametrize("format", ["qgroundcontrol", "mission-planner"])
def test_execution_export_requires_unchanged_audited_server_result(released, format):
    client, plan = released
    payload = {"plan": plan, "drone_id": "fast", "sortie_index": 0}
    assert client.post(f"/v1/exports/{format}", json=payload).status_code == 200
    for change in (
        "unsigned",
        "forged-audit",
        "route",
        "battery",
        "deadline",
        "firmware",
        "source",
    ):
        edited = copy.deepcopy(plan)
        if change == "unsigned":
            edited["export_authorization"] = None
        elif change == "forged-audit":
            edited["safety_audit"]["status"] = "failed"
        elif change == "route":

            def move_coordinates(value):
                if isinstance(value, dict):
                    if "longitude" in value:
                        value["longitude"] += 0.001
                    for child in value.values():
                        move_coordinates(child)
                elif isinstance(value, list):
                    for child in value:
                        move_coordinates(child)

            move_coordinates(edited["drone_plans"][0]["missions"][0])
        elif change == "battery":
            edited["drone_plans"][0]["missions"][0]["estimated_battery_percent"] = 1
        elif change == "deadline":
            edited["feasibility"]["status"] = "infeasible"
        elif change == "firmware":
            edited["drone_plans"][0]["autopilot_profile"]["hover_speed_mps"] = 4
        else:
            edited["search"]["request_fingerprint"] = "0" * 64
        response = client.post(f"/v1/exports/{format}", json={**payload, "plan": edited})
        assert response.status_code == 422, change
        assert response.json()["code"] == "export_release_required", change
    assert client.post("/v1/exports/geojson", json={**payload, "plan": edited}).status_code == 200


@pytest.mark.parametrize("format", ["qgroundcontrol", "mission-planner"])
def test_package_preserves_schedule_and_separates_each_sortie(released, format):
    client, plan = released
    payload = {"plan": plan, "drone_id": "fast"}
    response = client.post(f"/v1/exports/{format}", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "export_sortie_required"
    response = client.post("/v1/exports/flight-package", json={**payload, "format": format})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        sheet = archive.read("dispatch.html").decode()
        assert "Порядок выполнения вылетов" in sheet
        assert all(e["file"] in sheet for e in manifest["sorties"])
        assert manifest["execution_policy"] == "operator_dispatch_each_sortie"
        missions = plan["drone_plans"][0]["missions"]
        assert len(manifest["sorties"]) == len(missions)
        for entry in manifest["sorties"]:
            original = missions[entry["sortie_index"]]
            assert entry["start_offset_minutes"] == original["start_offset_minutes"]
            assert entry["end_offset_minutes"] == original["end_offset_minutes"]
            content = archive.read(entry["file"])
            assert hashlib.sha256(content).hexdigest() == entry["sha256"]
            if format == "qgroundcontrol":
                commands = [item["command"] for item in json.loads(content)["mission"]["items"]]
            else:
                commands = [int(line.split()[3]) for line in content.decode().splitlines()[1:]]
            assert commands.count(22) == commands.count(21) == 1
        assert all(e["ground_interval_before_minutes"] >= 3 for e in manifest["sorties"][1:])
        stored = json.loads(archive.read("calculated-plan.json"))
        assert "export_authorization" not in stored


def test_expired_or_other_server_signature_is_rejected(released, monkeypatch):
    _, payload = released
    plan = PlanResponse.model_validate(payload)
    authority = PlanReleaseAuthority()
    authority.verify(plan)
    with pytest.raises(PlanningError, match="Экспорт в автопилот"):
        PlanReleaseAuthority(key=b"other-server-key").verify(plan)
    assert plan.export_authorization is not None
    expires = int(plan.export_authorization.split(".")[0])
    monkeypatch.setattr("geoplan.geodata.release.time", lambda: expires)
    with pytest.raises(PlanningError, match="Экспорт в автопилот"):
        authority.verify(plan)
