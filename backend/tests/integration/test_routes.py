from pathlib import Path

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.assignment.solver import HybridAssignmentOptimizer
from geoplan.composition import AppDependencies, create_default_dependencies
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.navigation.optimizer import BidirectionalRouteOptimizer
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler
from scenarios.coverage import default_coverage_benchmark_cases

FRONTEND_DIRECTORY = Path("unused-frontend")


def test_health_route_returns_stable_report() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "message": "Сервис планирования доступен."}


def test_plan_route_returns_contract_for_completion_time() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )

    response = client.post(
        "/v1/plans",
        json={
            "criterion": "completion_time",
            "assignment_strategy": "hybrid",
            "drones": [
                {
                    "drone_id": "201",
                    "name": "Геоскан 201",
                    "cruise_speed_kmh": 60,
                    "maximum_flight_minutes": 60,
                    "battery_reserve_percent": 20,
                    "supported_tasks": ["rgb"],
                },
                {
                    "drone_id": "801",
                    "name": "Геоскан 801",
                    "cruise_speed_kmh": 50,
                    "maximum_flight_minutes": 60,
                    "battery_reserve_percent": 20,
                    "supported_tasks": ["rgb"],
                },
            ],
            "segments": [
                {
                    "segment_id": "north",
                    "distance_km": 12,
                    "route": [
                        {"longitude": 37.4, "latitude": 55.7},
                        {"longitude": 37.41, "latitude": 55.71},
                    ],
                },
                {
                    "segment_id": "south",
                    "distance_km": 12,
                    "route": [
                        {"longitude": 37.42, "latitude": 55.7},
                        {"longitude": 37.43, "latitude": 55.71},
                    ],
                },
            ],
            "mission_altitude_m": 120,
            "task": "rgb",
            "maximum_active_drones": 2,
            "completion_deadline_minutes": 15,
            "position_uncertainty_m": 5,
            "start_time_uncertainty_seconds": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["criterion"] == "completion_time"
    assert response.json()["metrics"]["completion_minutes"] == 14.4
    assert response.json()["feasibility"]["status"] == "feasible"
    assert response.json()["feasibility"]["deadline_margin_minutes"] == 0.6
    assert response.json()["schedule"] == {
        "algorithm": "priority_deconfliction",
        "dispatch_priority": "ready_first",
        "layer_preference": "earliest_finish",
        "reference_makespan_minutes": 14.4,
        "reference_total_flight_minutes": 26.4,
        "reference_audited": True,
        "variants_evaluated": 1,
        "makespan_minutes": 14.4,
        "minimum_separation_m": 50.0,
        "minimum_vertical_separation_m": 30.0,
        "vertically_deconflicted_mission_count": 0,
        "position_uncertainty_m": 5.0,
        "start_time_uncertainty_seconds": 2.0,
        "speed_uncertainty_percent": 0.0,
        "flight_site_occupancy_seconds": 0.0,
        "turnaround_minutes": 0.0,
        "resolved_conflict_count": 0,
        "resolved_conflicts": [],
    }
    assert response.json()["safety_audit"]["status"] == "passed"
    assert "survey_resolution" in {
        check["code"] for check in response.json()["safety_audit"]["checks"]
    }
    missions = [
        mission
        for drone_plan in response.json()["drone_plans"]
        for mission in drone_plan["missions"]
    ]
    assert all(mission["start_offset_minutes"] == 0 for mission in missions)
    assert all(mission["end_offset_minutes"] is not None for mission in missions)
    assert response.json()["optimization"] == {
        "algorithm": "exact_branch_and_bound",
        "coverage_algorithm": "boustrophedon_sweep",
        "route_algorithm": "bidirectional_visibility_graph",
        "scope": "generated_route_assignment",
        "optimality": "proven_optimal",
        "completion_minutes": 14.4,
        "total_flight_minutes": 26.4,
        "active_drone_count": 2,
        "survey_turn_radius_m": 0.0,
        "turn_radii_by_drone": {"201": 0.0, "801": 0.0},
        "coverage_angle_cost": "geometric_distance",
        "coverage_reference_airspeed_kmh": None,
        "productive_routing": None,
        "assignment_search": None,
    }


def test_plan_route_applies_positive_survey_turn_radius() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    client = TestClient(create_app(create_default_dependencies(), serve_frontend=False))

    response = client.post(
        "/v1/plans",
        json={
            "criterion": "total_flight_time",
            "drones": [
                {
                    "drone_id": "survey",
                    "name": "Survey drone",
                    "cruise_speed_kmh": 80,
                    "maximum_flight_minutes": 1_440,
                    "battery_reserve_percent": 20,
                    "supported_tasks": ["rgb"],
                }
            ],
            "coverage_areas": [coverage.area.model_dump(mode="json")],
            "survey_parameters": coverage.parameters.model_dump(mode="json"),
            "mission_altitude_m": coverage.mission_altitude_m,
            "minimum_turn_radius_m": 10,
            "task": "rgb",
        },
    )

    assert response.status_code == 200
    assert response.json()["optimization"]["survey_turn_radius_m"] == 10
    assert response.json()["safety_audit"]["status"] == "passed"
    mission = response.json()["drone_plans"][0]["missions"][0]
    assert len(mission["route"]) > 20
    assert any(
        s["stage_type"] == "transit" and s["duration_minutes"] > 0 for s in mission["stages"][2:-1]
    )


def test_plan_route_returns_stable_error_for_unplannable_segment() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )

    response = client.post(
        "/v1/plans",
        json={
            "criterion": "completion_time",
            "drones": [
                {
                    "drone_id": "201",
                    "name": "Геоскан 201",
                    "cruise_speed_kmh": 60,
                    "maximum_flight_minutes": 20,
                    "battery_reserve_percent": 20,
                    "supported_tasks": ["rgb"],
                }
            ],
            "segments": [
                {
                    "segment_id": "far",
                    "distance_km": 30,
                    "route": [
                        {"longitude": 37.4, "latitude": 55.7},
                        {"longitude": 37.41, "latitude": 55.71},
                    ],
                }
            ],
            "mission_altitude_m": 120,
            "task": "rgb",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "unplannable_segment"
    assert payload["message"] == (
        "Участок far не помещается в безопасную продолжительность полёта."
    )
    assert [item["code"] for item in payload["recommendations"]] == [
        "extend_endurance",
        "reduce_mission_scope",
    ]
    assert payload["recommendations"][0]["current_value"] == 20
    assert payload["recommendations"][0]["required_value"] == 37.5


def test_export_routes_return_planner_compatible_contracts() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )
    request = {
        "drone_id": "201",
        "plan": {
            "criterion": "completion_time",
            "drone_plans": [
                {
                    "drone_id": "201",
                    "mission_count": 1,
                    "total_duration_minutes": 12,
                    "missions": [
                        {
                            "drone_id": "201",
                            "segment_id": "north",
                            "duration_minutes": 12,
                            "distance_km": 12,
                            "route": [
                                {"longitude": 37.4, "latitude": 55.7},
                                {"longitude": 37.41, "latitude": 55.71},
                            ],
                            "altitude_m": 120,
                            "cruise_speed_kmh": 60,
                            "effective_speed_kmh": 60,
                            "estimated_battery_percent": 20,
                        }
                    ],
                }
            ],
            "metrics": {
                "completion_minutes": 12,
                "total_flight_minutes": 12,
                "total_distance_km": 12,
                "average_fleet_utilization_percent": 20,
            },
        },
    }

    geojson_response = client.post("/v1/exports/geojson", json=request)
    kml_response = client.post("/v1/exports/kml", json=request)
    qgroundcontrol_response = client.post("/v1/exports/qgroundcontrol", json=request)
    mission_planner_response = client.post("/v1/exports/mission-planner", json=request)

    assert geojson_response.status_code == 200
    assert geojson_response.json()["features"][0]["geometry"]["type"] == "LineString"
    assert kml_response.status_code == 200
    assert kml_response.json()["file_name"] == "mission-201.kml"
    # Arbitrary client-created plans remain inspectable in GIS, but cannot be released.
    assert qgroundcontrol_response.status_code == 422
    assert qgroundcontrol_response.json()["code"] == "export_release_required"
    assert mission_planner_response.status_code == 422
    assert mission_planner_response.json()["code"] == "export_release_required"


def test_frontend_is_served_from_the_same_origin_as_api(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>GeoPlan</html>", encoding="utf-8")
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=tmp_path,
            )
        )
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "GeoPlan" in response.text


def test_geojson_import_route_returns_zone_contract() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )

    response = client.post(
        "/v1/imports/geojson",
        json={
            "source_file_name": "work-area.geojson",
            "zone_type": "work_area",
            "content": (
                '{"type":"FeatureCollection","features":[{"type":"Feature",'
                '"properties":{"id":"north","name":"Северная зона"},'
                '"geometry":{"type":"Polygon","coordinates":[[[37.4,55.7],'
                "[37.41,55.7],[37.41,55.71],[37.4,55.7]]]}}]}"
            ),
        },
    )

    assert response.status_code == 200
    assert response.json()["zones"][0]["zone_type"] == "work_area"


def test_import_route_returns_stable_error_when_contract_is_invalid() -> None:
    client = TestClient(
        create_app(
            AppDependencies(
                duration_calculator=CruiseMissionDurationCalculator(),
                route_optimizer=BidirectionalRouteOptimizer(),
                coverage_planner=BoustrophedonCoveragePlanner(),
                assignment_optimizer=HybridAssignmentOptimizer(),
                mission_scheduler=ConflictAwareMissionScheduler(),
                frontend_directory=FRONTEND_DIRECTORY,
            ),
            serve_frontend=False,
        )
    )

    response = client.post("/v1/imports/geojson", json={})

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "message": "Запрос не соответствует контракту API.",
        "recommendations": [],
    }
