from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import SafetyCheckCode
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
