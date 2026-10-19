from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.fixed_wing import FixedWingPhasePlanner
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.planning.models import PlanRequest
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.vertical import altitude_interval_msl
from scenarios.planning import productive_survey_request


def request(enabled=True):
    base = productive_survey_request().model_dump(mode="json")
    base.update(
        mission_altitude_m=250,
        flat_ground_elevation_m=150,
        optimization_mode="fast_heuristic",
        productive_routing=False,
    )
    base["drones"][0].update(
        autopilot_profile={"airframe": "fixed_wing"},
        minimum_turn_radius_m=50,
        maximum_cruise_speed_kmh=110,
        fixed_wing_phase_profile={
            "climb_rate_mps": 5,
            "descent_rate_mps": 5,
            "climb_energy_multiplier": 1.5,
            "descent_energy_multiplier": 1.1,
        }
        if enabled
        else None,
    )
    return PlanRequest.model_validate(base)


@pytest.fixture(scope="module")
def plans():
    deps = replace(create_default_dependencies(), alternative_coverage_planner=None)
    with TestClient(create_app(deps, serve_frontend=False)) as client:
        results = []
        for enabled in (False, True):
            value = request(enabled)
            response = client.post("/v1/plans", json=value.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["safety_audit"]["status"] == "passed", result["safety_audit"]
            results.append(result)
        yield client, results


def test_climb_is_concurrent_with_transit_and_only_extra_energy_is_added(plans):
    _, (baseline, updated) = plans
    old = baseline["drone_plans"][0]["missions"]
    new = updated["drone_plans"][0]["missions"]
    assert len(old) == len(new)
    for a, b in zip(old, new, strict=True):
        assert b["route"] == a["route"]
        assert b["duration_minutes"] == pytest.approx(a["duration_minutes"])
        assert b["distance_km"] > a["distance_km"]
        assert b["vertical_climb_duration_minutes"] == b["vertical_descent_duration_minutes"] == 0
        assert b["route_altitudes_msl_m"][0] == b["route_altitudes_msl_m"][-1] == 150
        assert max(b["route_altitudes_msl_m"]) == 250
        phases = b["forward_flight_phases"]
        assert phases["maximum_climb_rate_mps"] <= 5
        assert phases["maximum_descent_rate_mps"] <= 5
        surcharge = phases["climb_minutes"] * 0.5 + phases["descent_minutes"] * 0.1
        assert phases["energy_surcharge_minutes"] == pytest.approx(surcharge)
        assert b["estimated_battery_percent"] - a["estimated_battery_percent"] == pytest.approx(
            surcharge / 60 * 100, abs=0.02
        )
        assert b["computed_gsd_cm"] == a["computed_gsd_cm"]


def test_gis_keeps_profile_and_autopilot_export_is_explicitly_unavailable(plans):
    client, (_, updated) = plans
    body = {"plan": updated, "drone_id": updated["drone_plans"][0]["drone_id"]}
    geo = client.post("/v1/exports/geojson", json=body)
    assert geo.status_code == 200, geo.text
    coords = geo.json()["features"][0]["geometry"]["coordinates"]
    assert coords[0][2] == coords[-1][2] == 150
    assert max(p[2] for p in coords) == 250
    assert geo.json()["features"][0]["properties"]["forward_flight_phases"] is not None
    kml = client.post("/v1/exports/kml", json=body)
    assert (
        kml.status_code == 200 and "<altitudeMode>absolute</altitudeMode>" in kml.json()["content"]
    )
    for kind in ("qgroundcontrol", "mission-planner"):
        result = client.post(f"/v1/exports/{kind}", json=body)
        assert result.status_code == 422
        assert result.json()["code"] == "autopilot_phase_export_unsupported"


def test_short_transit_is_rejected_instead_of_adding_a_stationary_wait(plans):
    client, _ = plans
    body = request().model_dump(mode="json")
    body["drones"][0]["fixed_wing_phase_profile"].update(climb_rate_mps=0.01, descent_rate_mps=0.01)
    result = client.post("/v1/plans", json=body)
    assert result.status_code == 422
    assert result.json()["code"] == "vertical_profile_unsatisfied"


def test_cruise_only_airspace_does_not_authorize_launch_below_its_floor(plans):
    client, _ = plans
    value = request()
    all_points = [p for a in value.coverage_areas for p in a.coordinates]
    assert value.departure_site is not None
    all_points.append(value.departure_site.coordinate)
    lowx, highx = (
        min(p.longitude for p in all_points) - 0.01,
        max(p.longitude for p in all_points) + 0.01,
    )
    lowy, highy = (
        min(p.latitude for p in all_points) - 0.01,
        max(p.latitude for p in all_points) + 0.01,
    )
    body = value.model_dump(mode="json")
    body["allowed_airspaces"] = [
        {
            "airspace_id": "cruise",
            "minimum_altitude_m": 200,
            "maximum_altitude_m": 300,
            "coordinates": [
                {"longitude": x, "latitude": y}
                for x, y in ((lowx, lowy), (highx, lowy), (highx, highy), (lowx, highy))
            ],
        }
    ]
    result = client.post("/v1/plans", json=body)
    assert result.status_code == 422
    assert result.json()["code"] == "outside_allowed_airspace"


def test_independent_audit_detects_a_flattened_flight_profile(plans):
    _, (_, updated) = plans
    value = request()
    mission = Mission.model_validate(updated["drone_plans"][0]["missions"][0])
    altered = mission.model_copy(update={"route_altitudes_msl_m": [250.0] * len(mission.route)})
    audit = IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()).audit(
        value, [], [altered]
    )
    assert (
        next(c for c in audit.checks if c.code.value == "flight_envelope").status.value == "failed"
    )


def test_fixed_wing_phase_configuration_requires_airframe_radius_and_valid_limits():
    drone = request().drones[0].model_dump()
    for patch in (
        {"minimum_turn_radius_m": 0},
        {"autopilot_profile": {"airframe": "quadrotor"}},
        {"maximum_climb_rate_mps": 1},
    ):
        with pytest.raises(ValidationError):
            DroneSpec.model_validate({**drone, **patch})


def test_full_airspeed_is_larger_than_its_horizontal_component():
    drone = request().drones[0].model_copy(update={"maximum_cruise_speed_kmh": 90})
    with pytest.raises(PlanningError, match="Полная воздушная скорость"):
        FixedWingPhasePlanner().plan(drone, 100, 1, 1)


def test_site_height_uncertainty_affects_rates_and_separation_bounds(plans):
    client, _ = plans
    body = request().model_dump(mode="json")
    body["terrain_vertical_uncertainty_m"] = 10
    result = client.post("/v1/plans", json=body)
    assert result.status_code == 200, result.text
    mission = Mission.model_validate(result.json()["drone_plans"][0]["missions"][0])
    phases = mission.forward_flight_phases
    assert phases is not None
    assert phases.maximum_climb_rate_mps == pytest.approx(110 / (phases.climb_minutes * 60))
    bounds = altitude_interval_msl(mission)
    assert bounds.minimum_msl_m == 140
    assert bounds.maximum_msl_m == 250
