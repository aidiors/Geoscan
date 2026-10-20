from pathlib import Path

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanRequest
from scenarios.customer import customer_polygons, customer_request
from scenarios.planning import productive_survey_request


def test_installed_cameras_keep_forward_overlap_on_explicit_capture_points():
    source = Path(__file__).resolve().parents[3] / "reference/hackathon/Границы полетов.kml"
    selected = {"zone-472", "zone-649", "zone-501", "zone-508"}
    request = customer_request(
        [p for p in customer_polygons(source) if p.area.area_id in selected],
        criterion="completion_time",
        wind={"speed_mps": 6, "direction_degrees": 270},
        planning_time_limit_seconds=30,
    )
    assert len(request.coverage_areas) == 4 and request.survey_parameters is not None
    camera = request.survey_parameters.sensor
    drones = [
        d.model_copy(
            update={
                "cruise_speed_kmh": (43, 70, 95)[i],
                "maximum_flight_minutes": (40, 70, 100)[i],
                "available_battery_sets": 8,
                "maximum_wind_mps": 12,
                "installed_sensor": camera.model_copy(
                    update={
                        "sensor_width_mm": (13.2, 17.3, 23.5)[i],
                        "sensor_height_mm": (8.8, 13, 15.6)[i],
                        "focal_length_mm": (8.8, 12, 16)[i],
                        "minimum_trigger_interval_s": (1, 1.5, 2)[i],
                    }
                ),
            }
        )
        for i, d in enumerate(request.drones[:3])
    ]
    request = PlanRequest.model_validate(
        {**request.model_dump(), "drones": drones, "maximum_active_drones": 3}
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.json()["safety_audit"]["status"] == "passed"
        quality = client.post(
            "/v1/survey-quality",
            json={
                "request": request.model_dump(mode="json"),
                "plan": response.json(),
            },
        ).json()
    assert quality["issues"] == []
    assert quality["minimum_forward_overlap_percent"] >= 75 - 1e-4
    assert quality["status"] == "partial"  # Wind/orientation assumptions remain explicit.


def test_camera_speed_refusal_quantifies_required_interval():
    request = productive_survey_request()
    assert request.survey_parameters is not None
    drone = request.drones[0].model_copy(
        update={
            "minimum_cruise_speed_kmh": 80,
            "installed_sensor": request.survey_parameters.sensor.model_copy(
                update={"minimum_trigger_interval_s": 30}
            ),
        }
    )
    request = PlanRequest.model_validate({**request.model_dump(), "drones": [drone]})
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 422
    error = response.json()
    assert error["code"] == "flight_envelope_exceeded"
    advice = error["recommendations"][0]
    assert advice["parameter"] == "installed_sensor.minimum_trigger_interval_s"
    assert advice["current_value"] == 30
    assert advice["required_value"] == 1.35
    assert advice["confidence"] == "estimate"


def test_terrain_camera_refusal_explains_short_boundary_interval():
    request = productive_survey_request(optimization_mode="fast_heuristic")
    assert request.departure_site is not None and request.survey_parameters is not None
    projection = LocalProjection(37, 55)
    request = PlanRequest.model_validate(
        {
            **request.model_dump(),
            "altitude_reference": "agl",
            "mission_altitude_m": 100,
            # The strip end lies shortly after a capture; the next strip starts
            # before the real camera can trigger again. Transit is not survey.
            "coverage_areas": [
                request.coverage_areas[0].model_copy(
                    update={
                        "coordinates": [
                            projection.unproject(x, y)
                            for x, y in ((0, 0), (1205, 0), (1205, 600), (0, 600))
                        ]
                    }
                )
            ],
            "survey_parameters": request.survey_parameters.model_copy(
                update={"maximum_segment_distance_km": 20}
            ),
            "terrain_elevation_samples": [
                {"coordinate": request.departure_site.coordinate, "elevation_m": 100}
            ],
            "drones": [
                request.drones[0].model_copy(
                    update={
                        "maximum_climb_rate_mps": 5,
                        "maximum_descent_rate_mps": 5,
                    }
                )
            ],
        }
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 422
    error = response.json()
    assert error["code"] == "unplannable_segment"
    assert "предел камеры 4" in error["message"] and "DEM" in error["message"]
    assert error["recommendations"][0]["parameter"] == (
        "survey_parameters.maximum_segment_distance_km"
    )


def test_terrain_camera_does_not_scan_transit_between_separate_strips():
    request = productive_survey_request(optimization_mode="fast_heuristic")
    assert request.departure_site is not None
    request = PlanRequest.model_validate(
        {
            **request.model_dump(),
            "altitude_reference": "agl",
            "mission_altitude_m": 100,
            "terrain_elevation_samples": [
                {
                    "coordinate": request.departure_site.coordinate,
                    "elevation_m": 100,
                }
            ],
            "drones": [
                request.drones[0].model_copy(
                    update={
                        "maximum_climb_rate_mps": 5,
                        "maximum_descent_rate_mps": 5,
                    }
                )
            ],
        }
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["safety_audit"]["status"] == "passed"
        quality = client.post(
            "/v1/survey-quality",
            json={
                "request": request.model_dump(mode="json"),
                "plan": plan,
            },
        ).json()
    assert quality["issues"] == []
    assert quality["minimum_capture_interval_s"] >= 4 - 1e-6
