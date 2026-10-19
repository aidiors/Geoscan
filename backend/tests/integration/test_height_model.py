import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.resolution import SurveyResolution
from geoplan.domain.enums import AltitudeReference, OptimizationMode
from geoplan.domain.mission import Mission
from geoplan.domain.terrain import VerticalProfilePoint
from geoplan.flight.altitude import FlightAltitudeEnvelope
from scenarios.neighbor_fields import neighbor_fields_request


@pytest.fixture(scope="module")
def height_plans():
    deps = replace(create_default_dependencies(), alternative_coverage_planner=None)
    rows = []
    with TestClient(create_app(deps, serve_frontend=False)) as client:
        for altitude, ground in ((100, 0), (250, 150)):
            request = neighbor_fields_request(
                mission_altitude_m=altitude,
                flat_ground_elevation_m=ground,
                optimization_mode=OptimizationMode.FAST_HEURISTIC,
                productive_routing=False,
            )
            response = client.post("/v1/plans", json=request.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            plan = response.json()
            assert plan["safety_audit"]["status"] == "passed"
            quality = client.post(
                "/v1/survey-quality",
                json={"request": request.model_dump(mode="json"), "plan": plan},
            ).json()
            rows.append((request, plan, quality))
        yield client, rows


def test_same_agl_has_same_survey_geometry_and_gsd_at_different_msl(height_plans):
    _, rows = height_plans
    low, high = rows
    first = [m for d in low[1]["drone_plans"] for m in d["missions"]]
    second = [m for d in high[1]["drone_plans"] for m in d["missions"]]
    assert len(first) == len(second)
    for a, b in zip(first, second, strict=True):
        assert a["route"] == b["route"]
        assert a["computed_gsd_cm"] == pytest.approx(b["computed_gsd_cm"])
        assert a["camera_trigger_spacing_m"] == b["camera_trigger_spacing_m"]
        assert b["minimum_agl_m"] == 100
    assert low[2]["maximum_gsd_cm"] == pytest.approx(high[2]["maximum_gsd_cm"])
    assert low[2]["covered_percent"] > 99 and high[2]["covered_percent"] > 99


def test_exports_keep_absolute_flight_altitude_and_surface_model(height_plans):
    client, rows = height_plans
    request, plan, _ = rows[1]
    for drone in plan["drone_plans"]:
        if not drone["missions"]:
            continue
        for kind in ("geojson", "kml", "qgroundcontrol", "mission-planner"):
            result = client.post(
                f"/v1/exports/{kind}",
                json={"plan": plan, "drone_id": drone["drone_id"], "sortie_index": 0},
            )
            assert result.status_code == 200, result.text
            if kind == "geojson":
                props = result.json()["features"][0]["properties"]
                assert props["altitude_m"] == request.mission_altitude_m == 250
                assert props["flat_ground_elevation_m"] == 150
                assert props["minimum_agl_m"] == props["maximum_agl_m"] == 100
            elif kind == "qgroundcontrol":
                mission = json.loads(result.json()["content"])["mission"]
                assert mission["plannedHomePosition"][2] == 150
                assert mission["globalPlanAltitudeMode"] == 2
                positions = [i for i in mission["items"] if i["command"] in {16, 21, 22}]
                assert all(i["frame"] == 0 and i["AltitudeMode"] == 2 for i in positions)
                assert all(
                    i["params"][6] == (150 if i["command"] == 21 else 250) for i in positions
                )


def test_gsd_error_recommendation_keeps_the_flight_datum(height_plans):
    client, rows = height_plans
    request = rows[1][0].model_dump(mode="json")
    request["survey_parameters"]["maximum_gsd_cm"] = 0.1
    response = client.post("/v1/plans", json=request)
    assert response.status_code == 422
    assert response.json()["code"] == "gsd_requirement_unsatisfied"
    advice = response.json()["recommendations"][0]
    assert advice["current_value"] == 250
    assert 150 < advice["required_value"] < 160
    assert "MSL" in advice["message"]


def test_turn_radius_analysis_separates_optical_height_from_airspace_elevation(height_plans):
    client, rows = height_plans
    request = rows[1][0]
    area = request.coverage_areas[0]
    assert request.survey_parameters is not None
    body = {
        "coverage": {
            "area": area.model_dump(mode="json"),
            "mission_altitude_m": 100,
            "parameters": request.survey_parameters.model_dump(mode="json"),
        },
        "flight_altitude_msl_m": 250,
        "radii_m": [0],
        "allowed_airspaces": [
            {
                "airspace_id": "ceiling",
                "coordinates": [c.model_dump(mode="json") for c in area.coordinates],
                "minimum_altitude_m": 200,
                "maximum_altitude_m": 300,
            }
        ],
    }
    response = client.post("/v1/turn-radius-analysis", json=body)
    assert response.status_code == 200
    assert response.json()["scenarios"][0]["status"] == "feasible"
    body["flight_altitude_msl_m"] = 100
    response = client.post("/v1/turn-radius-analysis", json=body)
    assert response.json()["scenarios"][0]["status"] == "no_route_for_selected_sweeps"


def test_terrain_uncertainty_uses_minimum_height_for_coverage_and_maximum_for_gsd(height_plans):
    _, rows = height_plans
    request, plan, _ = rows[0]
    base = Mission.model_validate(next(m for d in plan["drone_plans"] for m in d["missions"]))
    profile = [
        VerticalProfilePoint(
            coordinate=base.route[0],
            distance_from_start_m=0,
            terrain_elevation_m=100,
            target_altitude_msl=207,
            target_agl_m=107,
            required_vertical_rate_mps=0,
        )
    ]
    mission = base.model_copy(
        update={
            "altitude_reference": AltitudeReference.AGL,
            "vertical_profile": profile,
            "terrain_vertical_uncertainty_m": 7,
        }
    )
    assert mission.height_agl.minimum_m == 100
    assert mission.height_agl.maximum_m == 114
    assert request.survey_parameters is not None
    camera = request.survey_parameters.sensor
    assert SurveyResolution().gsd_cm(mission, camera) == pytest.approx(
        SurveyResolution().gsd_cm(base, camera) * 1.14
    )
    flat = base.model_copy(update={"terrain_vertical_uncertainty_m": 10})
    assert flat.height_agl.minimum_m == 90 and flat.height_agl.maximum_m == 110


def test_agl_flight_still_respects_the_aircraft_msl_ceiling(height_plans):
    _, rows = height_plans
    request, plan, _ = rows[0]
    base = Mission.model_validate(next(m for d in plan["drone_plans"] for m in d["missions"]))
    profile = [
        VerticalProfilePoint(
            coordinate=base.route[0],
            distance_from_start_m=0,
            terrain_elevation_m=3900,
            target_altitude_msl=4100,
            target_agl_m=200,
            required_vertical_rate_mps=0,
        )
    ]
    mission = base.model_copy(
        update={
            "altitude_m": 200,
            "altitude_reference": AltitudeReference.AGL,
            "vertical_profile": profile,
        }
    )
    drone = request.drones[0].model_copy(
        update={"maximum_mission_altitude_agl_m": 500, "maximum_mission_altitude_msl_m": 4000}
    )
    assert not FlightAltitudeEnvelope().allows(drone, mission)


def test_flat_msl_input_cannot_put_the_route_below_ground_or_silently_ignore_dem(height_plans):
    client, rows = height_plans
    request = rows[1][0].model_dump(mode="json")
    for update in (
        {"mission_altitude_m": 150},
        {"mission_altitude_m": 160, "terrain_vertical_uncertainty_m": 10},
        {
            "terrain_source": {
                "source_type": "inline",
                "samples": [{"coordinate": {"longitude": 37, "latitude": 55}, "elevation_m": 150}],
            }
        },
    ):
        response = client.post("/v1/plans", json={**request, **update})
        assert response.status_code == 422
