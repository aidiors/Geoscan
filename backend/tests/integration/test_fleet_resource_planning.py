from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import SafetyCheckCode
from scenarios.optimization_scale import scale_scenarios
from scenarios.planning import productive_survey_request


def test_productive_regrouping_can_rescue_a_single_battery_job():
    request = productive_survey_request()
    drone = request.drones[0].model_copy(update={"available_battery_sets": 1})
    request = request.model_copy(update={"drones": [drone]})
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["metrics"]["sortie_count"] == 1
    assert plan["safety_audit"]["status"] == "passed"
    check = next(
        c for c in plan["safety_audit"]["checks"] if c["code"] == SafetyCheckCode.FLEET_RESOURCES
    )
    assert check["status"] == "passed"


def test_exhausted_battery_or_service_budget_is_diagnosed_before_geometry_search():
    request = productive_survey_request()
    for state in (
        {"available_battery_sets": 0},
        {"maintenance_interval_flights": 10, "completed_flights_since_maintenance": 10},
        {"maintenance_interval_hours": 10, "flight_hours_since_maintenance": 11},
    ):
        drone = request.drones[0].model_copy(update=state)
        with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
            response = client.post(
                "/v1/plans",
                json=request.model_copy(update={"drones": [drone]}).model_dump(mode="json"),
            )
        assert response.status_code == 422
        assert response.json()["code"] == "no_capable_drone"


def test_ten_aircraft_cover_100_km2_with_two_battery_sets_each():
    base = next(case.request for case in scale_scenarios() if case.name == "flat_100")
    request = base.model_copy(
        update={
            "planning_time_limit_seconds": 60,
            "drones": [
                drone.model_copy(
                    update={
                        "available_battery_sets": 2,
                        "maintenance_interval_flights": 4,
                        "completed_flights_since_maintenance": 1,
                        "maintenance_interval_hours": 8,
                        "flight_hours_since_maintenance": 1,
                    }
                )
                for drone in base.drones
            ],
        }
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["safety_audit"]["status"] == "passed"
    assert all(len(drone["missions"]) <= 2 for drone in plan["drone_plans"])
