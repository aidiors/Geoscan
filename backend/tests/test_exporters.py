import json
from datetime import UTC, datetime

import pytest

from src.exporters import (
    build_geojson_export,
    build_kml_export,
    build_mission_planner_export,
    build_qgroundcontrol_export,
)
from src.schemas import (
    AirframeType,
    AltitudeReference,
    AutopilotFirmware,
    AutopilotProfile,
    Coordinate,
    DronePlan,
    ErrorCode,
    ExportRequest,
    Mission,
    MissionStage,
    MissionStageType,
    OptimizationCriterion,
    PlanMetrics,
    PlanResponse,
    VerticalProfilePoint,
)
from src.services import PlanningError


def test_geojson_export_contains_mission_route_and_altitude() -> None:
    response = build_geojson_export(_export_request())

    assert response.type == "FeatureCollection"
    assert response.features[0].geometry.coordinates == [
        [37.4, 55.7, 120],
        [37.41, 55.71, 120],
    ]
    assert response.features[0].properties.segment_id == "north"
    assert response.features[0].properties.start_offset_minutes == 3
    assert response.features[0].properties.end_offset_minutes == 15
    assert response.features[0].properties.start_time == datetime(2026, 9, 27, 7, 3, tzinfo=UTC)
    assert response.features[0].properties.end_time == datetime(2026, 9, 27, 7, 15, tzinfo=UTC)
    assert response.features[0].properties.remaining_battery_percent == 80
    assert response.features[0].properties.battery_reserve_margin_percent == 60
    assert response.features[0].properties.minimum_alternate_margin_percent == 35
    assert response.features[0].properties.critical_alternate_site_id == "reserve"


def test_kml_export_contains_segment_name_and_coordinates() -> None:
    response = build_kml_export(_export_request())

    assert response.file_name == "mission-201.kml"
    assert "<name>north</name>" in response.content
    assert "<name>takeoff</name>" in response.content
    assert "37.4,55.7,120.0 37.41,55.71,120.0" in response.content
    assert '<Data name="start_offset_minutes"><value>3.0</value></Data>' in response.content
    assert '<Data name="end_offset_minutes"><value>15.0</value></Data>' in response.content
    assert (
        '<Data name="start_time"><value>2026-09-27T07:03:00+00:00</value></Data>'
        in response.content
    )
    assert (
        '<Data name="end_time"><value>2026-09-27T07:15:00+00:00</value></Data>' in response.content
    )
    assert '<Data name="remaining_battery_percent"><value>80.0</value></Data>' in response.content
    assert (
        '<Data name="minimum_alternate_margin_percent"><value>35.0</value></Data>'
        in response.content
    )
    assert "37.4,55.7,120.0" in response.content
    assert "<name>takeoff</name><Point><coordinates>37.4,55.7,0</coordinates>" in response.content


def test_qgroundcontrol_export_contains_operational_mission_commands() -> None:
    response = build_qgroundcontrol_export(_export_request())
    content = json.loads(response.content)
    items = content["mission"]["items"]

    assert response.file_name == "mission-201.plan"
    assert content["fileType"] == "Plan"
    assert content["mission"]["firmwareType"] == 0
    assert content["mission"]["vehicleType"] == 43
    assert [item["command"] for item in items] == [22, 178, 206, 16, 206, 21]
    assert items[0]["params"] == [0.0, 0.0, 0.0, None, 55.7, 37.4, 120.0]
    assert items[1]["params"][1] == pytest.approx(60 / 3.6)
    assert items[2]["params"][0] == pytest.approx(100)
    assert items[-1]["params"][4:] == [55.71, 37.41, 0.0]
    assert items[0]["Altitude"] == 120
    assert items[-1]["Altitude"] == 0


@pytest.mark.parametrize(
    ("profile", "firmware_type", "vehicle_type"),
    [
        (
            AutopilotProfile(
                firmware=AutopilotFirmware.PX4,
                airframe=AirframeType.FIXED_WING,
                hover_speed_mps=7,
            ),
            12,
            1,
        ),
        (
            AutopilotProfile(
                firmware=AutopilotFirmware.ARDUPILOT,
                airframe=AirframeType.HEXAROTOR,
                hover_speed_mps=4,
            ),
            3,
            13,
        ),
    ],
)
def test_qgroundcontrol_export_uses_drone_autopilot_profile(
    profile: AutopilotProfile,
    firmware_type: int,
    vehicle_type: int,
) -> None:
    content = json.loads(build_qgroundcontrol_export(_export_request(profile=profile)).content)

    assert content["mission"]["firmwareType"] == firmware_type
    assert content["mission"]["vehicleType"] == vehicle_type
    assert content["mission"]["hoverSpeed"] == profile.hover_speed_mps


def test_mission_planner_export_contains_operational_mission_commands() -> None:
    response = build_mission_planner_export(_export_request())
    lines = response.content.splitlines()
    commands = [int(line.split("\t")[3]) for line in lines[1:]]

    assert response.file_name == "mission-201.waypoints"
    assert response.content.startswith("QGC WPL 110\n")
    assert commands == [22, 178, 206, 16, 206, 21]
    assert "55.7000000\t37.4000000\t120.0000000" in response.content


def test_terrain_following_exports_absolute_profile_and_mavlink_agl_frame() -> None:
    request = _terrain_following_request()

    geojson = build_geojson_export(request)
    kml = build_kml_export(request)
    qgroundcontrol = json.loads(build_qgroundcontrol_export(request).content)

    assert geojson.features[0].geometry.coordinates == [
        [37.4, 55.7, 220],
        [37.41, 55.71, 250],
    ]
    assert "37.4,55.7,220.0 37.41,55.71,250.0" in kml.content
    position_items = [
        item for item in qgroundcontrol["mission"]["items"] if item["command"] in {16, 21, 22}
    ]
    assert {item["frame"] for item in position_items} == {10}
    assert {item["AltitudeMode"] for item in position_items} == {4}
    assert position_items[0]["params"][6] == 128
    assert qgroundcontrol["mission"]["globalPlanAltitudeMode"] == 4
    assert qgroundcontrol["mission"]["plannedHomePosition"][2] == 100


def test_export_returns_stable_error_when_drone_is_not_in_plan() -> None:
    request = _export_request(drone_id="missing")

    with pytest.raises(PlanningError) as error:
        build_geojson_export(request)

    assert error.value.code is ErrorCode.DRONE_NOT_FOUND
    assert error.value.message == "БВС missing отсутствует в плане."


def _export_request(
    drone_id: str = "201",
    profile: AutopilotProfile | None = None,
) -> ExportRequest:
    mission = Mission(
        drone_id="201",
        segment_id="north",
        duration_minutes=12,
        distance_km=12,
        route=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.71),
        ],
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
        remaining_battery_percent=80,
        battery_reserve_margin_percent=60,
        minimum_alternate_margin_percent=35,
        critical_alternate_site_id="reserve",
        alternate_check_spacing_m=500,
        survey_duration_minutes=10,
        transit_duration_minutes=2,
        survey_distance_km=1,
        image_count=11,
        start_offset_minutes=3,
        end_offset_minutes=15,
        start_time=datetime(2026, 9, 27, 7, 3, tzinfo=UTC),
        end_time=datetime(2026, 9, 27, 7, 15, tzinfo=UTC),
        stages=[
            MissionStage(
                stage_type=MissionStageType.TAKEOFF,
                coordinates=[Coordinate(longitude=37.4, latitude=55.7)],
            ),
            MissionStage(
                stage_type=MissionStageType.SURVEY,
                coordinates=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            ),
            MissionStage(
                stage_type=MissionStageType.LANDING,
                coordinates=[Coordinate(longitude=37.41, latitude=55.71)],
            ),
        ],
    )
    return ExportRequest(
        drone_id=drone_id,
        plan=PlanResponse(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drone_plans=[
                DronePlan(
                    drone_id="201",
                    mission_count=1,
                    total_duration_minutes=12,
                    missions=[mission],
                    autopilot_profile=profile or AutopilotProfile(),
                )
            ],
            metrics=PlanMetrics(
                completion_minutes=12,
                total_flight_minutes=12,
                total_distance_km=12,
                average_fleet_utilization_percent=20,
            ),
        ),
    )


def _terrain_following_request() -> ExportRequest:
    request = _export_request()
    drone_plan = request.plan.drone_plans[0]
    mission = drone_plan.missions[0].model_copy(
        update={
            "altitude_reference": AltitudeReference.AGL,
            "minimum_agl_m": 120,
            "minimum_terrain_elevation_m": 100,
            "maximum_terrain_elevation_m": 130,
            "vertical_distance_m": 30,
            "vertical_energy_equivalent_minutes": 0.2,
            "terrain_vertical_uncertainty_m": 8,
            "vertical_profile": [
                VerticalProfilePoint(
                    coordinate=Coordinate(longitude=37.4, latitude=55.7, altitude_m=220),
                    distance_from_start_m=0,
                    terrain_elevation_m=100,
                    target_altitude_msl=220,
                    target_agl_m=120,
                    required_vertical_rate_mps=0,
                ),
                VerticalProfilePoint(
                    coordinate=Coordinate(longitude=37.41, latitude=55.71, altitude_m=250),
                    distance_from_start_m=1_200,
                    terrain_elevation_m=130,
                    target_altitude_msl=250,
                    target_agl_m=120,
                    required_vertical_rate_mps=0.4,
                ),
            ],
        }
    )
    updated_plan = request.plan.model_copy(
        update={
            "drone_plans": [
                drone_plan.model_copy(update={"missions": [mission]}),
            ]
        }
    )
    return request.model_copy(update={"plan": updated_plan})
