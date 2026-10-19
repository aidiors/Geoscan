from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geoplan.airspace.windows import _vertical_layer_options
from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import AirframeType, MissionTask, OptimizationCriterion, OptimizationMode
from geoplan.domain.fleet import AutopilotProfile, DroneSpec
from geoplan.domain.flight_phases import VerticalTakeoffLandingProfile
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.flight.camera import DistanceCameraPolicy
from geoplan.flight.takeoff_landing import TakeoffLandingEstimator
from geoplan.flight.trajectory import mission_route_clock
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.planning.models import PlanRequest
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.vertical import apply_vertical_layer


def request(profile=True, **drone_changes):
    drone = DroneSpec(
        drone_id="rotor",
        name="Rotor",
        cruise_speed_kmh=60,
        maximum_flight_minutes=60,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
        takeoff_landing_profile=VerticalTakeoffLandingProfile(
            climb_rate_mps=2,
            descent_rate_mps=1,
            climb_energy_multiplier=2,
        )
        if profile
        else None,
    )
    drone = DroneSpec.model_validate({**drone.model_dump(), **drone_changes})
    return PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        optimization_mode=OptimizationMode.FAST_HEURISTIC,
        productive_routing=False,
        drones=[drone],
        mission_altitude_m=250,
        flat_ground_elevation_m=150,
        terrain_vertical_uncertainty_m=10,
        task=MissionTask.RGB,
        segments=[
            WorkSegment(
                segment_id="line",
                distance_km=0.638,
                route=[
                    Coordinate(longitude=37, latitude=55),
                    Coordinate(longitude=37.01, latitude=55),
                ],
            )
        ],
    )


@pytest.fixture
def client():
    deps = replace(create_default_dependencies(), alternative_coverage_planner=None)
    with TestClient(create_app(deps, serve_frontend=False)) as client:
        yield client


def plan(client, value):
    result = client.post("/v1/plans", json=value.model_dump(mode="json"))
    assert result.status_code == 200, result.text
    assert result.json()["safety_audit"]["status"] == "passed"
    return result.json()


def test_vertical_phases_enter_schedule_battery_and_route_clock_once(client):
    baseline = plan(client, request(False))
    updated = plan(client, request())
    original = baseline["drone_plans"][0]["missions"][0]
    mission = Mission.model_validate(updated["drone_plans"][0]["missions"][0])
    climb, descent = 110 / 2 / 60, 110 / 60
    assert mission.duration_minutes == pytest.approx(original["duration_minutes"] + climb + descent)
    assert mission.vertical_climb_duration_minutes == pytest.approx(climb)
    assert mission.vertical_descent_duration_minutes == pytest.approx(descent)
    assert sum(stage.duration_minutes for stage in mission.stages) == pytest.approx(
        mission.duration_minutes
    )
    assert mission_route_clock(mission).offsets_minutes[-1] == pytest.approx(
        original["duration_minutes"]
    )
    assert mission.vertical_energy_equivalent_minutes == pytest.approx(climb, abs=0.001)
    assert mission.estimated_battery_percent == pytest.approx(
        (mission.duration_minutes + climb) / 60 * 100, abs=0.01
    )
    assert updated["metrics"]["total_flight_minutes"] == pytest.approx(
        mission.duration_minutes, abs=0.01
    )
    assert mission.end_offset_minutes is not None and mission.start_offset_minutes is not None
    assert mission.end_offset_minutes - mission.start_offset_minutes == pytest.approx(
        mission.duration_minutes, abs=0.002
    )
    explicit = Mission.model_validate(
        {**mission.model_dump(), "photo_capture_route_indices": [0, 1], "image_count": 2}
    )
    assert DistanceCameraPolicy().capture_times_minutes(explicit)[0] == pytest.approx(climb)
    assert any("плановым скоростям" in a for a in updated["safety_audit"]["assumptions"])
    assert any("не включены" in a for a in baseline["safety_audit"]["assumptions"])


def test_sortie_that_only_fits_without_takeoff_is_rejected(client):
    plan(client, request(False, maximum_flight_minutes=1))
    result = client.post(
        "/v1/plans", json=request(True, maximum_flight_minutes=1).model_dump(mode="json")
    )
    assert result.status_code == 422
    assert result.json()["code"] in {"battery_endurance_exceeded", "unplannable_segment"}


@pytest.mark.parametrize("airframe", [AirframeType.FIXED_WING, AirframeType.VTOL_FIXED_ROTOR])
def test_rotor_profile_cannot_be_used_for_aircraft_approach(airframe):
    with pytest.raises(ValidationError, match="только мультикоптеры"):
        request(autopilot_profile=AutopilotProfile(airframe=airframe))


def test_operating_rates_are_checked_against_separate_aircraft_limits():
    with pytest.raises(ValidationError, match="превышает предел"):
        request(maximum_climb_rate_mps=1)
    estimate = TakeoffLandingEstimator().estimate(request().drones[0], 100, 200)
    assert estimate.climb_minutes == pytest.approx(100 / 2 / 60)
    assert estimate.descent_minutes == pytest.approx(200 / 60)


def test_optimizer_selects_aircraft_using_complete_sortie_time(client):
    base = request()
    slow = base.drones[0].model_copy(update={"drone_id": "a-slow"})
    fast = DroneSpec.model_validate(
        {
            **slow.model_dump(),
            "drone_id": "z-fast",
            "takeoff_landing_profile": {"climb_rate_mps": 5, "descent_rate_mps": 5},
        }
    )
    result = plan(client, base.model_copy(update={"drones": [slow, fast]}))
    assert [d["drone_id"] for d in result["drone_plans"] if d["missions"]] == ["z-fast"]


def test_launch_column_cannot_cross_an_airspace_floor(client):
    base = request()
    ring = [
        Coordinate(longitude=x, latitude=y)
        for x, y in ((36.99, 54.99), (37.02, 54.99), (37.02, 55.01), (36.99, 55.01))
    ]
    body = base.model_dump(mode="json")
    body["allowed_airspaces"] = [
        {
            "airspace_id": "cruise-only",
            "coordinates": [p.model_dump() for p in ring],
            "minimum_altitude_m": 200,
            "maximum_altitude_m": 300,
        }
    ]
    result = client.post("/v1/plans", json=body)
    assert result.status_code == 422
    assert "колонна" in result.json()["message"]


def test_reserve_for_alternate_includes_climb_descent_and_energy_surcharge(client):
    values = []
    for enabled in (False, True):
        base = request(enabled)
        body = base.model_dump(mode="json")
        body["alternate_landing_sites"] = [
            {
                "site_id": "backup",
                "name": "Backup",
                "coordinate": base.segments[0].route[0].model_dump(),
            }
        ]
        result = client.post("/v1/plans", json=body)
        assert result.status_code == 200, result.text
        values.append(result.json()["drone_plans"][0]["missions"][0])
    baseline, updated = values
    climb, descent = 110 / 2 / 60, 110 / 60
    expected_energy = 2 * climb + descent
    assert baseline["minimum_alternate_margin_percent"] - updated[
        "minimum_alternate_margin_percent"
    ] == pytest.approx(expected_energy / 60 * 100, abs=0.02)
    assert updated["critical_alternate_start_offset_minutes"] - baseline[
        "critical_alternate_start_offset_minutes"
    ] == pytest.approx(climb)
    assert updated["critical_alternate_duration_minutes"] - baseline[
        "critical_alternate_duration_minutes"
    ] == pytest.approx(descent)


def test_vertical_layer_energy_respects_battery_health_without_double_counting_time(client):
    base = request(battery_health_percent=50, energy_consumption_multiplier=2)
    result = plan(client, base)
    mission = Mission.model_validate(result["drone_plans"][0]["missions"][0])
    layered = base.model_copy(
        update={"maximum_vertical_deconfliction_m": 30, "vertical_deconfliction_step_m": 30}
    )
    options = _vertical_layer_options(mission, base.drones[0], layered)
    assert len(options) == 2
    raised = apply_vertical_layer(mission, options[1])
    assert options[1].extra_battery_percent == pytest.approx(
        (30 / 2 / 60 * 2 + 30 / 60) / 15 * 100, abs=1e-6
    )
    assert raised.estimated_battery_percent == pytest.approx(
        (raised.duration_minutes + raised.vertical_energy_equivalent_minutes) / 15 * 100, abs=0.011
    )
    assert raised.route_leg_durations_minutes == mission.route_leg_durations_minutes


def test_audit_rejects_a_mission_that_dropped_configured_vertical_phases(client):
    baseline = plan(client, request(False))
    mission = Mission.model_validate(baseline["drone_plans"][0]["missions"][0])
    value = request()
    report = IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()).audit(
        value, value.segments, [mission]
    )
    assert report.status.value == "failed"
    assert (
        next(check for check in report.checks if check.code.value == "flight_envelope").status.value
        == "failed"
    )


def test_agl_profile_uses_clearance_at_each_site_including_terrain_uncertainty(client):
    base = request()
    body = base.model_dump(mode="json")
    body.update(
        altitude_reference="agl",
        mission_altitude_m=100,
        flat_ground_elevation_m=0,
        terrain_vertical_uncertainty_m=7,
    )
    body["drones"][0].update(maximum_climb_rate_mps=20, maximum_descent_rate_mps=20)
    body["terrain_elevation_samples"] = [
        {"coordinate": point.model_dump(), "elevation_m": elevation}
        for point, elevation in zip(base.segments[0].route, (150, 200), strict=True)
    ]
    result = client.post("/v1/plans", json=body)
    assert result.status_code == 200, result.text
    mission = result.json()["drone_plans"][0]["missions"][0]
    assert mission["vertical_climb_duration_minutes"] == pytest.approx(114 / 2 / 60)
    assert mission["vertical_descent_duration_minutes"] == pytest.approx(114 / 60)
    assert mission["vertical_profile"][0]["target_altitude_msl"] == 257
    assert mission["vertical_profile"][-1]["target_altitude_msl"] == 307
    assert result.json()["safety_audit"]["status"] == "passed"
    body["alternate_landing_sites"] = [
        {
            "site_id": "backup",
            "name": "Backup",
            "coordinate": base.segments[0].route[0].model_dump(),
        }
    ]
    rejected = client.post("/v1/plans", json=body)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "invalid_request"
    with pytest.raises(ValidationError, match="DEM-профиля аварийного маршрута"):
        PlanRequest.model_validate(body)
