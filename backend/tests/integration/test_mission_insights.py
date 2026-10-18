from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import OptimizationCriterion
from geoplan.planning.models import PlanResponse
from scenarios.planning import parallel_survey_request, productive_survey_request


class FixedTrackOnly:
    def improve(self, request, source, drones, baseline, build, evaluate, reference=None):
        return baseline


@pytest.fixture
def client():
    dependencies = replace(create_default_dependencies(), freeform_optimizer=FixedTrackOnly())
    with TestClient(create_app(dependencies, serve_frontend=False)) as result:
        yield result


def plan_for(client, request):
    response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    return PlanResponse.model_validate(response.json())


def test_installed_camera_and_aircraft_radius_reach_schedule_audit_and_export(client):
    request = productive_survey_request()
    assert request.survey_parameters is not None
    sensor = request.survey_parameters.sensor.model_copy(
        update={
            "sensor_id": "installed",
            "minimum_trigger_interval_s": 0.5,
            "sensor_width_mm": 12,
            "sensor_height_mm": 8,
        }
    )
    drone = request.drones[0].model_copy(
        update={
            "installed_sensor": sensor,
            "minimum_turn_radius_m": 40,
        }
    )
    request = request.model_copy(update={"drones": [drone]})
    assert request.survey_parameters is not None
    plan = plan_for(client, request)
    assert plan.safety_audit.status == "passed"
    assert plan.optimization.productive_routing.evaluated_variants > 0
    for mission in plan.drone_plans[0].missions:
        assert mission.sensor == sensor
        assert mission.minimum_turn_radius_m == 40
        assert mission.camera_trigger_spacing_m == pytest.approx(120 * 8 / 8.8 * 0.25)
        assert mission.computed_gsd_cm <= request.survey_parameters.maximum_gsd_cm


def test_individual_camera_baseline_with_many_strips_is_not_normalized_twice(client):
    request = productive_survey_request(productive_routing=False)
    assert request.survey_parameters is not None
    request = request.model_copy(
        update={
            "survey_parameters": request.survey_parameters.model_copy(
                update={"maximum_segment_distance_km": 500}
            ),
            "drones": [
                request.drones[0].model_copy(
                    update={"installed_sensor": request.survey_parameters.sensor}
                )
            ],
        }
    )
    plan = plan_for(client, request)
    assert plan.safety_audit.status == "passed"
    assert sum(stage.stage_type == "survey" for stage in plan.drone_plans[0].missions[0].stages) > 1


def test_quality_uses_exported_trigger_spacing_and_detects_missing_forward_overlap(client):
    request = productive_survey_request()
    plan = plan_for(client, request)
    response = client.post(
        "/v1/survey-quality",
        json={
            "request": request.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["frame_count"] > 100
    assert report["frame_count"] == sum(m.image_count for d in plan.drone_plans for m in d.missions)

    assert report["covered_percent"] > 99
    assert report["minimum_forward_overlap_percent"] >= 75 - 0.1
    assert report["minimum_side_overlap_percent"] >= 60 - 0.1
    assert not report["issues"], report
    payload = plan.model_dump(mode="json")
    for drone in payload["drone_plans"]:
        for mission in drone["missions"]:
            mission["camera_trigger_spacing_m"] = 150
    bad = client.post(
        "/v1/survey-quality",
        json={
            "request": request.model_dump(mode="json"),
            "plan": payload,
        },
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["status"] == "issues"
    assert bad.json()["minimum_forward_overlap_percent"] < 75


def test_camera_clock_survives_short_connections_between_survey_strips(client):
    from geoplan.navigation.geometry import LocalProjection

    request = productive_survey_request()
    projection = LocalProjection(37, 55)
    area = request.coverage_areas[0].model_copy(
        update={
            "coordinates": [
                projection.unproject(x, y) for x, y in ((0, 0), (1205, 0), (1205, 600), (0, 600))
            ]
        }
    )
    request = request.model_copy(update={"coverage_areas": [area]})
    plan = plan_for(client, request)
    assert plan.safety_audit.status == "passed"
    report = client.post(
        "/v1/survey-quality",
        json={"request": request.model_dump(mode="json"), "plan": plan.model_dump(mode="json")},
    ).json()
    assert report["minimum_capture_interval_s"] >= 4 - 1e-6
    assert report["covered_percent"] > 99.99
    assert not report["issues"], report
    assert report["frame_count"] == sum(m.image_count for d in plan.drone_plans for m in d.missions)

    # Audit the actual commands again after stripping the repaired clock.
    from geoplan.safety.audit import _camera_timing_check

    missions = [m for d in plan.drone_plans for m in d.missions]
    assert _camera_timing_check(request, missions).status == "passed"
    broken = [m.model_copy(update={"photo_capture_route_indices": None}) for m in missions]
    assert _camera_timing_check(request, broken).status == "failed"


def test_advisor_replans_deadline_variant_without_relaxing_safety_or_overlap(client):
    request = productive_survey_request(
        criterion=OptimizationCriterion.BALANCED, completion_deadline_minutes=1
    )
    assert request.survey_parameters is not None
    response = client.post(
        "/v1/feasibility-advice",
        json={
            "request": request.model_dump(mode="json"),
            "maximum_variants": 2,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["baseline"]["metrics"]["completion_minutes"] > 1
    verified = [variant for variant in report["variants"] if variant["status"] == "verified"]
    assert verified
    for variant in verified:
        assert variant["plan"]["safety_audit"]["status"] == "passed"
        assert (
            variant["plan"]["metrics"]["completion_minutes"]
            <= variant["request"]["completion_deadline_minutes"]
        )
        assert variant["request"]["survey_parameters"] == request.survey_parameters.model_dump(
            mode="json"
        )
        assert variant["changed_parameters"] == ["completion_deadline_minutes"]


def test_advisor_does_not_treat_planner_failure_as_proven_infeasibility(client):
    request = productive_survey_request()
    drone = request.drones[0].model_copy(
        update={
            "initial_charge_percent": 21,
            "battery_reserve_percent": 20,
            "maximum_flight_minutes": 1,
        }
    )
    response = client.post(
        "/v1/feasibility-advice",
        json={
            "request": request.model_copy(update={"drones": [drone]}).model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["baseline"] is None
    assert response.json()["baseline_error"]
    assert "не доказывает" in response.json()["message"]


def test_slow_camera_cannot_force_fixed_wing_below_minimum_airspeed(client):
    request = productive_survey_request()
    drone = request.drones[0].model_copy(
        update={"minimum_cruise_speed_kmh": 64, "minimum_turn_radius_m": 40}
    )
    response = client.post(
        "/v1/plans", json=request.model_copy(update={"drones": [drone]}).model_dump(mode="json")
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "flight_envelope_exceeded"


def test_advisor_changes_altitude_only_when_operator_explicitly_allows_it(client):
    request = productive_survey_request()
    assert request.survey_parameters is not None
    request = request.model_copy(
        update={
            "survey_parameters": request.survey_parameters.model_copy(update={"maximum_gsd_cm": 2})
        }
    )
    denied = client.post(
        "/v1/feasibility-advice",
        json={
            "request": request.model_dump(mode="json"),
        },
    ).json()
    assert denied["baseline"] is None
    assert not denied["variants"]
    permitted = client.post(
        "/v1/feasibility-advice",
        json={
            "request": request.model_dump(mode="json"),
            "allow_altitude_change": True,
        },
    )
    assert permitted.status_code == 200, permitted.text
    variant = permitted.json()["variants"][0]
    assert variant["changed_parameters"] == ["mission_altitude_m"]
    assert variant["request"]["mission_altitude_m"] < request.mission_altitude_m
    assert variant["request"]["survey_parameters"]["maximum_gsd_cm"] == 2
    assert variant["status"] == "verified", variant["explanation"]


def test_parallel_heterogeneous_cameras_keep_individual_triggers_and_check_sortie_joins(client):
    request = parallel_survey_request()
    assert request.survey_parameters is not None
    sensor = request.survey_parameters.sensor
    drones = [
        d.model_copy(
            update={
                "installed_sensor": sensor.model_copy(
                    update={
                        "sensor_id": f"camera-{i}",
                        "minimum_trigger_interval_s": 0.5 + i * 0.1,
                        "sensor_width_mm": 13.2 + i,
                        "sensor_height_mm": 8.8 + i,
                    }
                )
            }
        )
        for i, d in enumerate(request.drones)
    ]
    request = request.model_copy(update={"drones": drones})
    plan = plan_for(client, request)
    assert plan.metrics.active_drone_count == 2
    assert plan.safety_audit.status == "passed"
    for drone in plan.drone_plans:
        expected = next(d.installed_sensor for d in drones if d.drone_id == drone.drone_id)
        for mission in drone.missions:
            assert mission.sensor == expected
    response = client.post(
        "/v1/survey-quality",
        json={
            "request": request.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["cross_sortie_pairs_checked"] > 0
