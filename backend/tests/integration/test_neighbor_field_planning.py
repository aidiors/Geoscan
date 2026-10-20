import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import OptimizationCriterion
from geoplan.errors import PlanningError
from geoplan.planning.routes import _validate_geometry
from scenarios.neighbor_fields import neighbor_fields_request


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
@pytest.mark.parametrize("radius", [0, 40])
def test_neighbor_fields_share_a_real_sortie_and_keep_transfers_out_of_survey(criterion, radius):
    request = neighbor_fields_request(
        criterion=criterion, completion_deadline_minutes=30, minimum_turn_radius_m=radius
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["safety_audit"]["status"] == "passed"
        assert plan["metrics"]["sortie_count"] == 1
        mission = plan["drone_plans"][0]["missions"][0]
        assert set(mission["coverage_area_ids"]) == {"field-0", "field-1"}
        assert any(s["stage_type"] == "transit" for s in mission["stages"][2:-1])
        assert (
            plan["search"]["checkpoints"][-1]["objective"]
            <= plan["search"]["checkpoints"][0]["objective"]
        )
        for kind in ("geojson", "kml", "qgroundcontrol", "mission-planner"):
            exported = client.post(
                f"/v1/exports/{kind}", json={"plan": plan, "drone_id": mission["drone_id"]}
            )
            assert exported.status_code == 200, exported.text
        quality = client.post(
            "/v1/survey-quality", json={"request": request.model_dump(mode="json"), "plan": plan}
        )
        assert quality.status_code == 200
        assert quality.json()["covered_percent"] > 99


def test_one_battery_can_cover_neighbor_fields_when_individual_sorties_cannot():
    request = neighbor_fields_request()
    request = request.model_copy(
        update={"drones": [request.drones[0].model_copy(update={"available_battery_sets": 1})]}
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["metrics"]["sortie_count"] == 1
    assert plan["safety_audit"]["status"] == "passed"
    assert plan["optimization"]["productive_routing"]["cross_area_sortie_count"] == 1


def test_track_ownership_is_checked_spatially_instead_of_trusting_its_label():
    request = neighbor_fields_request()
    tracks = [tuple(area.coordinates[:2]) for area in request.coverage_areas]
    route = [p for track in tracks for p in track]
    source = WorkSegment(
        segment_id="owned",
        coverage_area_id="field-0",
        distance_km=1,
        route=route,
        survey_tracks=tracks,
        survey_track_area_ids=["field-0", "field-1"],
    )
    _validate_geometry(route, route, [], source, request)
    mislabeled = source.model_copy(update={"survey_track_area_ids": ["field-0", "field-0"]})
    with pytest.raises(PlanningError):
        _validate_geometry(route, route, [], mislabeled, request)
