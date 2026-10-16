import pytest
from pydantic import ValidationError

from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters, WorkSegment
from geoplan.domain.airspace import AllowedAirspace, NoFlyZone
from geoplan.domain.enums import AltitudeReference, MissionTask, OptimizationCriterion
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.planning.models import PlanRequest


def test_plan_request_rejects_empty_drone_list() -> None:
    with pytest.raises(ValidationError):
        PlanRequest(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drones=[],
            segments=[
                WorkSegment(
                    segment_id="north",
                    distance_km=12,
                    route=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.71),
                    ],
                )
            ],
            mission_altitude_m=120,
            task=MissionTask.RGB,
        )


def test_plan_request_radius_defaults_to_zero_and_rejects_unsupported_inputs() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="survey",
                name="БВС",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="explicit",
                distance_km=1,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    assert request.minimum_turn_radius_m == 0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        PlanRequest.model_validate({**request.model_dump(), "minimum_turn_radius_m": -1})
    with pytest.raises(ValidationError, match="генерируемых галсов"):
        PlanRequest.model_validate({**request.model_dump(), "minimum_turn_radius_m": 10})


def test_drone_spec_rejects_full_battery_reserve() -> None:
    with pytest.raises(ValidationError):
        DroneSpec(
            drone_id="201",
            name="Геоскан 201",
            cruise_speed_kmh=60,
            maximum_flight_minutes=60,
            battery_reserve_percent=100,
            supported_tasks=[MissionTask.RGB],
        )


def test_drone_spec_rejects_charge_not_above_reserve() -> None:
    with pytest.raises(ValidationError, match="Начальный заряд"):
        DroneSpec(
            drone_id="low-charge",
            name="БВС",
            cruise_speed_kmh=60,
            maximum_flight_minutes=60,
            initial_charge_percent=20,
            battery_reserve_percent=20,
            supported_tasks=[MissionTask.RGB],
        )


def test_drone_spec_rejects_cruise_speed_outside_confirmed_envelope() -> None:
    with pytest.raises(ValidationError, match="выше подтверждённого диапазона"):
        DroneSpec(
            drone_id="too-fast",
            name="БВС",
            cruise_speed_kmh=70,
            maximum_flight_minutes=60,
            battery_reserve_percent=20,
            supported_tasks=[MissionTask.RGB],
            maximum_cruise_speed_kmh=54,
        )


def test_drone_spec_resolves_reference_specific_altitude_before_legacy_fallback() -> None:
    drone = DroneSpec(
        drone_id="gemini",
        name="Geoscan Gemini",
        cruise_speed_kmh=43,
        maximum_flight_minutes=40,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
        maximum_mission_altitude_m=700,
        maximum_mission_altitude_agl_m=500,
        maximum_mission_altitude_msl_m=4_000,
    )

    assert drone.maximum_altitude_for(AltitudeReference.AGL) == 500
    assert drone.maximum_altitude_for(AltitudeReference.MSL) == 4_000


def test_msl_plan_rejects_agl_restriction_without_terrain_conversion() -> None:
    with pytest.raises(ValidationError, match="direct comparison with MSL"):
        PlanRequest(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drones=[
                DroneSpec(
                    drone_id="survey",
                    name="БВС",
                    cruise_speed_kmh=60,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            segments=[
                WorkSegment(
                    segment_id="explicit",
                    distance_km=1,
                    route=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.71),
                    ],
                )
            ],
            no_fly_zones=[
                NoFlyZone(
                    zone_id="agl-layer",
                    coordinates=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.7),
                        Coordinate(longitude=37.4, latitude=55.71),
                    ],
                    minimum_altitude_m=100,
                    maximum_altitude_m=200,
                    altitude_reference=AltitudeReference.AGL,
                )
            ],
            mission_altitude_m=120,
            altitude_reference=AltitudeReference.MSL,
            task=MissionTask.RGB,
        )


def test_work_segment_rejects_route_with_one_coordinate() -> None:
    with pytest.raises(ValidationError):
        WorkSegment(
            segment_id="north",
            distance_km=12,
            route=[Coordinate(longitude=37.4, latitude=55.7)],
        )


def test_allowed_airspace_rejects_equal_altitude_bounds() -> None:
    with pytest.raises(ValidationError):
        AllowedAirspace(
            airspace_id="corridor",
            coordinates=[
                Coordinate(longitude=37.4, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
                Coordinate(longitude=37.4, latitude=55.71),
            ],
            minimum_altitude_m=120,
            maximum_altitude_m=120,
        )


def test_balanced_plan_requires_completion_deadline() -> None:
    with pytest.raises(ValidationError):
        PlanRequest(
            criterion=OptimizationCriterion.BALANCED,
            drones=[
                DroneSpec(
                    drone_id="201",
                    name="Геоскан 201",
                    cruise_speed_kmh=60,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            segments=[
                WorkSegment(
                    segment_id="north",
                    distance_km=12,
                    route=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.71),
                    ],
                )
            ],
            mission_altitude_m=120,
            task=MissionTask.RGB,
        )


def test_plan_request_rejects_drone_limit_larger_than_fleet() -> None:
    with pytest.raises(ValidationError):
        PlanRequest(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drones=[
                DroneSpec(
                    drone_id="201",
                    name="Геоскан 201",
                    cruise_speed_kmh=60,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            segments=[
                WorkSegment(
                    segment_id="north",
                    distance_km=12,
                    route=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.71),
                    ],
                )
            ],
            mission_altitude_m=120,
            task=MissionTask.RGB,
            maximum_active_drones=2,
        )


def test_aerial_imaging_requires_at_least_fifty_percent_side_overlap() -> None:
    with pytest.raises(ValidationError) as error:
        PlanRequest(
            criterion=OptimizationCriterion.COMPLETION_TIME,
            drones=[
                DroneSpec(
                    drone_id="201",
                    name="Геоскан 201",
                    cruise_speed_kmh=60,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            coverage_areas=[
                CoverageArea(
                    area_id="field",
                    name="Поле",
                    coordinates=[
                        Coordinate(longitude=37.4, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.7),
                        Coordinate(longitude=37.41, latitude=55.71),
                    ],
                )
            ],
            survey_parameters=SurveyParameters(
                flight_direction_degrees=90,
                side_overlap_percent=49,
                forward_overlap_percent=75,
                maximum_gsd_cm=5,
                strip_width_m=200,
                sensor=SensorProfile(
                    sensor_id="rgb",
                    name="RGB",
                    sensor_width_mm=13.2,
                    sensor_height_mm=8.8,
                    focal_length_mm=8.8,
                    image_width_px=5_472,
                    image_height_px=3_648,
                    minimum_trigger_interval_s=1,
                ),
            ),
            mission_altitude_m=120,
            task=MissionTask.RGB,
        )

    assert "боковое перекрытие должно быть не менее 50%" in str(error.value)
