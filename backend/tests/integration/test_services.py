from datetime import UTC, datetime, timedelta

import pytest

from geoplan.assignment.solver import HybridAssignmentOptimizer
from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters, WorkSegment
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.domain.airspace import AllowedAirspace, FlightObstacle, NoFlyZone, RestrictionValidity
from geoplan.domain.enums import (
    AirframeType,
    AltitudeMode,
    AltitudeReference,
    AutopilotFirmware,
    CoverageAngleCost,
    ErrorCode,
    ImportedGeometryType,
    MissionStageType,
    MissionTask,
    OptimizationCriterion,
    ValidityMode,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import AutopilotProfile, DroneSpec, FlightSite, PayloadCapability
from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import TerrainElevationSample
from geoplan.errors import PlanningError
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.flight.models import MissionDurationRequest
from geoplan.flight.terrain import InverseDistanceTerrainProvider, TerrainFollowingProfilePlanner
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.navigation.curvature import DubinsSweepTransitionPlanner, PlannedSweepTransition
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.models import SweepRouteCurvatureRequest
from geoplan.navigation.optimizer import BidirectionalRouteOptimizer
from geoplan.navigation.routing import (
    RouteGeometryError,
    route_bearing_degrees,
    route_is_inside_coverage_area,
)
from geoplan.planning.engine import build_plan
from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler
from scenarios.coverage import default_coverage_benchmark_cases


class RejectingTransitionPlanner:
    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition:
        raise RouteGeometryError("Для теста переход недоступен.")


def test_agl_plan_builds_vertical_profile_and_charges_vertical_energy() -> None:
    route = [
        Coordinate(longitude=37, latitude=55),
        Coordinate(longitude=37.001, latitude=55),
    ]
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="terrain-drone",
                name="Terrain drone",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
                maximum_climb_rate_mps=20,
                maximum_descent_rate_mps=20,
            )
        ],
        segments=[WorkSegment(segment_id="ridge", distance_km=0.064, route=route)],
        mission_altitude_m=120,
        altitude_reference=AltitudeReference.AGL,
        terrain_elevation_samples=[
            TerrainElevationSample(coordinate=route[0], elevation_m=100),
            TerrainElevationSample(coordinate=route[1], elevation_m=130),
        ],
        terrain_max_interpolation_distance_m=100,
        task=MissionTask.RGB,
    )

    response = create_plan(request)
    mission = response.drone_plans[0].missions[0]

    assert mission.altitude_reference is AltitudeReference.AGL
    assert mission.vertical_profile[0].target_altitude_msl == 220
    assert mission.vertical_profile[-1].target_altitude_msl == 250
    assert mission.vertical_distance_m == pytest.approx(30)
    assert mission.vertical_energy_equivalent_minutes > 0
    horizontal_battery_percent = mission.duration_minutes / 60 * 100
    assert mission.estimated_battery_percent > horizontal_battery_percent
    assert response.safety_audit is not None
    assert response.safety_audit.status.value == "passed"


def test_gemini_uses_separate_agl_and_msl_ceiling() -> None:
    route = [
        Coordinate(longitude=37, latitude=55),
        Coordinate(longitude=37.001, latitude=55),
    ]
    drone = DroneSpec(
        drone_id="gemini",
        name="Geoscan Gemini",
        cruise_speed_kmh=43,
        maximum_flight_minutes=40,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
        maximum_climb_rate_mps=5,
        maximum_descent_rate_mps=5,
        maximum_mission_altitude_agl_m=500,
        maximum_mission_altitude_msl_m=4_000,
    )
    segment = WorkSegment(segment_id="short", distance_km=0.064, route=route)

    msl_response = create_plan(
        PlanRequest(
            criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
            drones=[drone],
            segments=[segment],
            mission_altitude_m=1_200,
            altitude_reference=AltitudeReference.MSL,
            task=MissionTask.RGB,
        )
    )

    assert msl_response.drone_plans[0].missions[0].altitude_m == 1_200
    with pytest.raises(PlanningError) as error:
        create_plan(
            PlanRequest(
                criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
                drones=[drone],
                segments=[segment],
                mission_altitude_m=600,
                altitude_reference=AltitudeReference.AGL,
                terrain_elevation_samples=[
                    TerrainElevationSample(coordinate=route[0], elevation_m=100),
                    TerrainElevationSample(coordinate=route[1], elevation_m=100),
                ],
                terrain_max_interpolation_distance_m=100,
                task=MissionTask.RGB,
            )
        )

    assert error.value.code is ErrorCode.FLIGHT_ENVELOPE_EXCEEDED


def create_plan(request: PlanRequest) -> PlanResponse:
    return build_plan(
        request,
        CruiseMissionDurationCalculator(),
        BidirectionalRouteOptimizer(),
        BoustrophedonCoveragePlanner(),
        HybridAssignmentOptimizer(),
        ConflictAwareMissionScheduler(),
        DubinsSweepTransitionPlanner(ExactDubinsPlanner()),
        IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
        TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider()),
        AuditedFreeformRouteOptimizer(),
    )


def test_positive_turn_radius_changes_survey_route_and_distance() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    baseline_request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="survey",
                name="Съёмочный БВС",
                cruise_speed_kmh=80,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
                autopilot_profile=AutopilotProfile(
                    firmware=AutopilotFirmware.ARDUPILOT,
                    airframe=AirframeType.FIXED_WING,
                    hover_speed_mps=6,
                ),
            )
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
    )

    baseline = build_plan(
        baseline_request,
        CruiseMissionDurationCalculator(),
        BidirectionalRouteOptimizer(),
        BoustrophedonCoveragePlanner(),
        HybridAssignmentOptimizer(),
        ConflictAwareMissionScheduler(),
        RejectingTransitionPlanner(),
        IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
        TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider()),
        AuditedFreeformRouteOptimizer(),
    )
    curved = create_plan(baseline_request.model_copy(update={"minimum_turn_radius_m": 10}))

    baseline_mission = baseline.drone_plans[0].missions[0]
    curved_mission = curved.drone_plans[0].missions[0]
    assert curved.drone_plans[0].autopilot_profile == baseline_request.drones[0].autopilot_profile
    assert curved.optimization is not None
    assert curved.optimization.survey_turn_radius_m == 10
    assert len(curved_mission.route) > len(baseline_mission.route)
    assert curved_mission.survey_distance_km is not None
    assert baseline_mission.survey_distance_km is not None
    assert curved_mission.distance_km > baseline_mission.distance_km
    assert all(
        len(s.coordinates) == 2
        for s in curved_mission.stages
        if s.stage_type is MissionStageType.SURVEY
    )


def test_gsd_failure_recommends_exact_maximum_altitude() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    maximum_gsd_cm = 0.1
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="survey",
                name="Съёмочный БВС",
                cruise_speed_kmh=60,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters.model_copy(update={"maximum_gsd_cm": maximum_gsd_cm}),
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    sensor = coverage.parameters.sensor
    expected_altitude = (
        maximum_gsd_cm
        / 100
        * sensor.image_width_px
        * sensor.focal_length_mm
        / sensor.sensor_width_mm
    )
    assert error.value.code is ErrorCode.GSD_REQUIREMENT_UNSATISFIED
    assert error.value.recommendations[0].code == "adjust_mission_altitude"
    assert error.value.recommendations[0].required_value == round(expected_altitude, 2)


def test_positive_turn_radius_connects_departure_and_landing_tangentially() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="survey",
                name="Съёмочный БВС",
                cruise_speed_kmh=80,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
        departure_site=FlightSite(
            site_id="west-departure",
            name="Западный старт",
            coordinate=Coordinate(longitude=37.6, latitude=55.75),
        ),
        landing_site=FlightSite(
            site_id="east-landing",
            name="Восточная посадка",
            coordinate=Coordinate(longitude=37.64, latitude=55.75),
        ),
        minimum_turn_radius_m=30,
    )

    mission = create_plan(request).drone_plans[0].missions[0]
    transit_route = mission.stages[1].coordinates
    surveys = [s for s in mission.stages if s.stage_type is MissionStageType.SURVEY]
    survey_route = [*surveys[0].coordinates, *surveys[-1].coordinates]
    landing_route = mission.stages[-1].coordinates

    assert len(transit_route) > 2
    assert len(landing_route) > 2
    assert transit_route[-1] == survey_route[0]
    assert landing_route[0] == survey_route[-1]
    assert (
        _heading_difference_degrees(
            route_bearing_degrees(transit_route[-2], transit_route[-1]),
            route_bearing_degrees(survey_route[0], survey_route[1]),
        )
        < 10
    )
    assert (
        _heading_difference_degrees(
            route_bearing_degrees(survey_route[-2], survey_route[-1]),
            route_bearing_degrees(landing_route[0], landing_route[1]),
        )
        < 10
    )


def _heading_difference_degrees(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def test_positive_turn_radius_reports_stable_failure_without_polyline_fallback() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="survey",
                name="Съёмочный БВС",
                cruise_speed_kmh=80,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        minimum_turn_radius_m=10,
        task=MissionTask.RGB,
    )

    with pytest.raises(PlanningError) as error:
        build_plan(
            request,
            CruiseMissionDurationCalculator(),
            BidirectionalRouteOptimizer(),
            BoustrophedonCoveragePlanner(),
            HybridAssignmentOptimizer(),
            ConflictAwareMissionScheduler(),
            RejectingTransitionPlanner(),
            IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
            TerrainFollowingProfilePlanner(InverseDistanceTerrainProvider()),
            AuditedFreeformRouteOptimizer(),
        )

    assert error.value.code is ErrorCode.TURN_RADIUS_UNSATISFIED
    assert "Радиус: 10 м" in error.value.message


def test_build_plan_balances_work_when_completion_time_selected() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="fast",
                name="Быстрый",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="slow",
                name="Медленный",
                cruise_speed_kmh=40,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        segments=[
            WorkSegment(
                segment_id="north",
                distance_km=20,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            ),
            WorkSegment(
                segment_id="south",
                distance_km=16,
                route=[
                    Coordinate(longitude=37.42, latitude=55.7),
                    Coordinate(longitude=37.43, latitude=55.71),
                ],
            ),
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    assert [plan.mission_count for plan in response.drone_plans] == [1, 1]
    assert response.metrics.completion_minutes == 24


def test_build_plan_prefers_fastest_drone_when_total_flight_time_selected() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="fast",
                name="Быстрый",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="slow",
                name="Медленный",
                cruise_speed_kmh=40,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        segments=[
            WorkSegment(
                segment_id="north",
                distance_km=12,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            ),
            WorkSegment(
                segment_id="south",
                distance_km=12,
                route=[
                    Coordinate(longitude=37.42, latitude=55.7),
                    Coordinate(longitude=37.43, latitude=55.71),
                ],
            ),
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    assert [plan.mission_count for plan in response.drone_plans] == [2, 0]
    assert response.metrics.total_flight_minutes == 24
    assert response.metrics.average_fleet_utilization_percent == 50


def test_build_plan_returns_stable_error_for_unplannable_segment() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=60,
                maximum_flight_minutes=30,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="far",
                distance_km=30,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.UNPLANNABLE_SEGMENT
    assert error.value.message == "Участок far не помещается в безопасную продолжительность полёта."
    assert [item.code for item in error.value.recommendations] == [
        "extend_endurance",
        "reduce_mission_scope",
    ]
    assert error.value.recommendations[0].current_value == 30
    assert error.value.recommendations[0].required_value == 37.5


def test_build_plan_returns_stable_error_when_fleet_cannot_perform_task() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="gemini",
                name="Геоскан Gemini",
                cruise_speed_kmh=45,
                maximum_flight_minutes=40,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="thermal-inspection",
                distance_km=4,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=80,
        task=MissionTask.THERMAL,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.NO_CAPABLE_DRONE
    assert error.value.message == "Для выбранного типа работ нет подходящего БВС."
    assert error.value.recommendations[0].code == "add_capable_drone"
    assert error.value.recommendations[0].affected_ids == ["thermal"]


def test_payload_capability_is_required_when_passport_payloads_are_known() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="rgb-only",
                name="RGB only",
                cruise_speed_kmh=45,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB, MissionTask.THERMAL],
                payloads=[
                    PayloadCapability(
                        payload_id="camera",
                        name="RGB camera",
                        supported_tasks=[MissionTask.RGB],
                    )
                ],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="thermal",
                distance_km=4,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=80,
        task=MissionTask.THERMAL,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.NO_CAPABLE_DRONE


def test_temperature_outside_passport_envelope_is_rejected() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="cold-limited",
                name="Cold limited",
                cruise_speed_kmh=45,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
                minimum_operating_temperature_c=-10,
                maximum_operating_temperature_c=40,
            )
        ],
        segments=[
            WorkSegment(
                segment_id="cold-survey",
                distance_km=4,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=80,
        ambient_temperature_c=-20,
        task=MissionTask.RGB,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.TEMPERATURE_LIMIT_EXCEEDED


def test_build_plan_detours_route_around_no_fly_zone() -> None:
    request = PlanRequest(
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
                segment_id="crossing",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.42, latitude=55.7),
                ],
            )
        ],
        no_fly_zones=[
            NoFlyZone(
                zone_id="restriction",
                coordinates=[
                    Coordinate(longitude=37.408, latitude=55.698),
                    Coordinate(longitude=37.412, latitude=55.698),
                    Coordinate(longitude=37.412, latitude=55.702),
                    Coordinate(longitude=37.408, latitude=55.702),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert len(mission.route) == 4
    assert mission.duration_minutes > 2


def test_build_plan_includes_takeoff_survey_and_landing_stages() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="801",
                name="Геоскан 801",
                cruise_speed_kmh=55,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.THERMAL],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="inspection",
                distance_km=4,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=80,
        task=MissionTask.THERMAL,
        departure_site=FlightSite(
            site_id="base",
            name="База",
            coordinate=Coordinate(longitude=37.39, latitude=55.69),
        ),
        landing_site=FlightSite(
            site_id="landing",
            name="Посадка",
            coordinate=Coordinate(longitude=37.42, latitude=55.72),
        ),
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert [stage.stage_type for stage in mission.stages] == [
        "takeoff",
        "transit",
        "survey",
        "landing",
    ]
    assert mission.stages[0].coordinates[0].longitude == 37.39
    assert mission.stages[-1].coordinates[-1].longitude == 37.42


def test_build_plan_optimizes_site_pair_and_route_orientation_together() -> None:
    west = Coordinate(longitude=37.4, latitude=55.7)
    east = Coordinate(longitude=37.5, latitude=55.7)
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=70,
                maximum_flight_minutes=180,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="survey",
                distance_km=6.3,
                route=[west, east],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        departure_sites=[
            FlightSite(site_id="west-start", name="Запад", coordinate=west),
            FlightSite(site_id="east-start", name="Восток", coordinate=east),
        ],
        landing_sites=[
            FlightSite(site_id="west-landing", name="Запад", coordinate=west),
            FlightSite(
                site_id="remote-landing",
                name="Удалённая площадка",
                coordinate=Coordinate(longitude=37.45, latitude=55.8),
            ),
        ],
        alternate_landing_sites=[
            FlightSite(site_id="east-alternate", name="Резерв", coordinate=east),
        ],
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert mission.departure_site is not None
    assert mission.landing_site is not None
    assert mission.departure_site.site_id == "east-start"
    assert mission.landing_site.site_id == "west-landing"
    assert mission.stages[2].coordinates == [east, west]
    assert mission.survey_distance_km is not None
    assert mission.transit_distance_km == 0
    assert mission.useful_flight_percent == 100


def test_build_plan_returns_stable_error_when_route_leaves_allowed_airspace() -> None:
    request = PlanRequest(
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
                segment_id="outside",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.42, latitude=55.7),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        allowed_airspaces=[
            AllowedAirspace(
                airspace_id="corridor",
                coordinates=[
                    Coordinate(longitude=37.39, latitude=55.69),
                    Coordinate(longitude=37.41, latitude=55.69),
                    Coordinate(longitude=37.41, latitude=55.71),
                    Coordinate(longitude=37.39, latitude=55.71),
                ],
                minimum_altitude_m=20,
                maximum_altitude_m=500,
            )
        ],
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.OUTSIDE_ALLOWED_AIRSPACE
    assert error.value.message == (
        "Маршрут участка outside выходит за границы разрешённого воздушного пространства."
    )


def test_build_plan_returns_stable_error_when_altitude_is_not_allowed() -> None:
    request = PlanRequest(
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
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.7),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        allowed_airspaces=[
            AllowedAirspace(
                airspace_id="low",
                coordinates=[
                    Coordinate(longitude=37.39, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.71),
                    Coordinate(longitude=37.39, latitude=55.71),
                ],
                minimum_altitude_m=20,
                maximum_altitude_m=100,
            )
        ],
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.OUTSIDE_ALLOWED_AIRSPACE
    assert error.value.message == "Высота задания не входит в разрешённое воздушное пространство."
    assert error.value.recommendations[0].code == "adjust_mission_altitude"
    assert error.value.recommendations[0].required_value == 100


def test_build_plan_waits_until_allowed_airspace_window_opens() -> None:
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    request = PlanRequest(
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
        segments=[WorkSegment(segment_id="timed", distance_km=2, route=route)],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=planning_start,
        allowed_airspaces=[
            AllowedAirspace(
                airspace_id="timed-corridor",
                coordinates=[
                    Coordinate(longitude=37.39, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.71),
                    Coordinate(longitude=37.39, latitude=55.71),
                ],
                minimum_altitude_m=20,
                maximum_altitude_m=500,
                validity=RestrictionValidity(
                    mode=ValidityMode.TIME_WINDOW,
                    valid_from=planning_start + timedelta(minutes=30),
                    valid_to=planning_start + timedelta(minutes=60),
                ),
            )
        ],
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert mission.start_offset_minutes == 30
    assert mission.start_time == planning_start + timedelta(minutes=30)
    assert mission.end_time == planning_start + timedelta(minutes=32)
    assert response.metrics.completion_minutes == 32


def test_build_plan_rejects_mission_that_does_not_fit_airspace_window() -> None:
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    request = PlanRequest(
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
        segments=[WorkSegment(segment_id="timed", distance_km=2, route=route)],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=planning_start,
        allowed_airspaces=[
            AllowedAirspace(
                airspace_id="short-window",
                coordinates=[
                    Coordinate(longitude=37.39, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.69),
                    Coordinate(longitude=37.42, latitude=55.71),
                    Coordinate(longitude=37.39, latitude=55.71),
                ],
                minimum_altitude_m=20,
                maximum_altitude_m=500,
                validity=RestrictionValidity(
                    mode=ValidityMode.TIME_WINDOW,
                    valid_from=planning_start,
                    valid_to=planning_start + timedelta(minutes=1),
                ),
            )
        ],
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE


def test_build_plan_waits_until_crossed_no_fly_window_closes() -> None:
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    restriction = NoFlyZone(
        zone_id="temporary-crossing",
        coordinates=[
            Coordinate(longitude=37.404, latitude=55.699),
            Coordinate(longitude=37.406, latitude=55.699),
            Coordinate(longitude=37.406, latitude=55.701),
            Coordinate(longitude=37.404, latitude=55.701),
        ],
        safety_margin_m=0,
        minimum_altitude_m=100,
        maximum_altitude_m=150,
        validity=RestrictionValidity(
            mode=ValidityMode.TIME_WINDOW,
            valid_from=planning_start,
            valid_to=planning_start + timedelta(minutes=30),
        ),
    )
    request = PlanRequest(
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
        segments=[WorkSegment(segment_id="timed-no-fly", distance_km=2, route=route)],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=planning_start,
        no_fly_zones=[restriction],
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert mission.route == route
    assert mission.start_offset_minutes == 30
    assert mission.start_time == planning_start + timedelta(minutes=30)
    assert response.safety_audit is not None
    assert response.safety_audit.status == "passed"


def test_build_plan_ignores_no_fly_zone_above_mission_altitude() -> None:
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    restriction = NoFlyZone(
        zone_id="high-restriction",
        coordinates=[
            Coordinate(longitude=37.404, latitude=55.699),
            Coordinate(longitude=37.406, latitude=55.699),
            Coordinate(longitude=37.406, latitude=55.701),
            Coordinate(longitude=37.404, latitude=55.701),
        ],
        safety_margin_m=0,
        minimum_altitude_m=200,
        maximum_altitude_m=300,
        validity=RestrictionValidity(
            mode=ValidityMode.TIME_WINDOW,
            valid_from=planning_start,
            valid_to=planning_start + timedelta(minutes=30),
        ),
    )
    request = PlanRequest(
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
        segments=[WorkSegment(segment_id="below-zone", distance_km=2, route=route)],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=planning_start,
        no_fly_zones=[restriction],
    )

    response = create_plan(request)

    assert response.drone_plans[0].missions[0].start_offset_minutes == 0


def test_build_plan_rejects_mission_outside_operating_window() -> None:
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    request = PlanRequest(
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
                segment_id="daylight",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.7),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        planning_start_time=planning_start,
        planning_end_time=planning_start + timedelta(minutes=1),
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE
    assert "4D-окно" in error.value.message


def test_duration_increases_with_headwind() -> None:
    calculator = CruiseMissionDurationCalculator()
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.4, latitude=55.8),
    ]

    calm = calculator.calculate(
        MissionDurationRequest(distance_km=10, cruise_speed_kmh=60, route=route)
    )
    headwind = calculator.calculate(
        MissionDurationRequest(
            distance_km=10,
            cruise_speed_kmh=60,
            route=route,
            wind=WindCondition(speed_mps=5, direction_degrees=0),
        )
    )

    assert headwind.duration_minutes > calm.duration_minutes
    assert headwind.effective_speed_kmh < calm.effective_speed_kmh


def test_build_plan_reports_wind_adjusted_coverage_cost_and_reference_speed() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="slow",
                name="Медленный БВС",
                cruise_speed_kmh=40,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="fast",
                name="Быстрый БВС",
                cruise_speed_kmh=60,
                maximum_flight_minutes=1_440,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
        wind=WindCondition(speed_mps=5, direction_degrees=45),
    )

    response = create_plan(request)

    assert response.optimization is not None
    assert response.optimization.coverage_angle_cost is CoverageAngleCost.WIND_ADJUSTED_DURATION
    assert response.optimization.coverage_reference_airspeed_kmh == 50


def test_build_plan_reports_battery_remaining_and_reserve_margin() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="battery-check",
                name="Battery check",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                initial_charge_percent=75,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="survey",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.4, latitude=55.8),
                ],
                survey_speed_limit_kmh=30,
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    mission = create_plan(request).drone_plans[0].missions[0]

    assert mission.estimated_battery_percent == pytest.approx(33.33, abs=0.01)
    assert mission.starting_battery_percent == 75
    assert mission.remaining_battery_percent == pytest.approx(41.67, abs=0.01)
    assert mission.battery_reserve_margin_percent == pytest.approx(21.67, abs=0.01)
    assert mission.survey_duration_minutes == pytest.approx(20)
    assert mission.transit_duration_minutes == pytest.approx(0)


def test_battery_health_and_energy_multiplier_reduce_effective_endurance() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="aged-battery",
                name="Aged battery",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_health_percent=80,
                energy_consumption_multiplier=1.2,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="survey",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.4, latitude=55.8),
                ],
                survey_speed_limit_kmh=30,
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    mission = create_plan(request).drone_plans[0].missions[0]

    assert mission.estimated_battery_percent == pytest.approx(50)
    assert mission.remaining_battery_percent == pytest.approx(50)


def test_build_plan_rejects_mission_that_exceeds_current_charge() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="low-charge",
                name="Low charge",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                initial_charge_percent=40,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="survey",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.4, latitude=55.8),
                ],
                survey_speed_limit_kmh=30,
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.recommendations[0].code == "recharge_battery"
    assert error.value.recommendations[0].current_value == 40


def test_build_plan_does_not_use_emergency_alternate_as_planned_landing() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=60,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="north",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        alternate_landing_sites=[
            FlightSite(
                site_id="near",
                name="Ближайшая площадка",
                coordinate=Coordinate(longitude=37.411, latitude=55.711),
            )
        ],
    )

    response = create_plan(request)

    assert response.drone_plans[0].missions[0].landing_site is None


def test_build_plan_reports_minimum_margin_to_alternate_landing_site() -> None:
    start = Coordinate(longitude=37.4, latitude=55.7)
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
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
                segment_id="reachable",
                distance_km=10,
                route=[start, Coordinate(longitude=37.4, latitude=55.79)],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        alternate_landing_sites=[FlightSite(site_id="alternate", name="Резерв", coordinate=start)],
    )

    mission = create_plan(request).drone_plans[0].missions[0]

    assert mission.minimum_alternate_margin_percent is not None
    assert mission.minimum_alternate_margin_percent > 0
    assert mission.critical_alternate_site_id == "alternate"
    assert mission.alternate_check_spacing_m is not None
    assert mission.alternate_check_spacing_m > 500
    assert mission.alternate_evaluated_checkpoint_count == 2
    assert mission.alternate_certification_error_minutes == pytest.approx(0, abs=1e-6)


def test_build_plan_rejects_route_without_energy_to_reach_alternate() -> None:
    start = Coordinate(longitude=37.4, latitude=55.7)
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
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
                segment_id="too-far",
                distance_km=30,
                route=[start, Coordinate(longitude=37.4, latitude=55.97)],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        alternate_landing_sites=[FlightSite(site_id="alternate", name="Резерв", coordinate=start)],
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.ALTERNATE_LANDING_UNREACHABLE
    endurance = next(
        recommendation
        for recommendation in error.value.recommendations
        if recommendation.parameter == "maximum_flight_minutes"
    )
    assert endurance.required_value is not None
    assert endurance.required_value == pytest.approx(75, abs=0.2)


def test_build_plan_assigns_work_only_to_drone_with_eligible_sites() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="assigned",
                name="Назначенный БВС",
                cruise_speed_kmh=60,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="other",
                name="Другой БВС",
                cruise_speed_kmh=90,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        segments=[
            WorkSegment(
                segment_id="field",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=100,
        task=MissionTask.RGB,
        departure_sites=[
            FlightSite(
                site_id="assigned-start",
                name="Назначенный старт",
                coordinate=Coordinate(longitude=37.4, latitude=55.7),
                assigned_drone_ids=["assigned"],
            )
        ],
        landing_sites=[
            FlightSite(
                site_id="assigned-finish",
                name="Назначенная посадка",
                coordinate=Coordinate(longitude=37.41, latitude=55.71),
                assigned_drone_ids=["assigned"],
            )
        ],
    )

    response = create_plan(request)

    assert response.drone_plans[0].mission_count == 1
    assert response.drone_plans[1].mission_count == 0


def test_build_plan_returns_stable_error_when_wind_exceeds_drone_limit() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="gemini",
                name="Геоскан Gemini",
                cruise_speed_kmh=43,
                maximum_flight_minutes=40,
                battery_reserve_percent=20,
                maximum_wind_mps=10,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="north",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.41, latitude=55.71),
                ],
            )
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        wind=WindCondition(speed_mps=12, direction_degrees=0),
    )

    with pytest.raises(PlanningError) as error:
        create_plan(request)

    assert error.value.code is ErrorCode.WIND_LIMIT_EXCEEDED
    assert error.value.recommendations[0].code == "wait_for_lower_wind"
    assert error.value.recommendations[0].current_value == 12
    assert error.value.recommendations[0].required_value == 10


@pytest.mark.parametrize(
    "height,mode,altitude,detour",
    [
        (100, AltitudeMode.ABSOLUTE, 60, True),
        (None, AltitudeMode.ABSOLUTE, 100, True),
        (5, AltitudeMode.RELATIVE_TO_GROUND, 100, True),
        (0, AltitudeMode.ABSOLUTE, 100, False),
    ],
)
def test_build_plan_detours_around_obstacles_without_assuming_relative_heights_are_msl(
    height,
    mode,
    altitude,
    detour,
) -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=60,
                maximum_flight_minutes=120,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        segments=[
            WorkSegment(
                segment_id="crossing",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.42, latitude=55.7),
                ],
            )
        ],
        obstacles=[
            FlightObstacle(
                obstacle_id="tower",
                geometry_type=ImportedGeometryType.POINT,
                coordinates=[Coordinate(longitude=37.41, latitude=55.7)],
                height_m=height,
                altitude_mode=mode,
            )
        ],
        mission_altitude_m=altitude,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    mission = response.drone_plans[0].missions[0]
    assert len(mission.stages[2].coordinates) == (4 if detour else 2)
    if detour:
        assert mission.distance_km > 2


def test_build_plan_generates_coverage_route_from_target_polygon() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=70,
                maximum_flight_minutes=180,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[
            CoverageArea(
                area_id="field",
                name="Тестовое поле",
                coordinates=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.405, latitude=55.7),
                    Coordinate(longitude=37.405, latitude=55.705),
                    Coordinate(longitude=37.4, latitude=55.705),
                ],
            )
        ],
        survey_parameters=SurveyParameters(
            flight_direction_degrees=90,
            side_overlap_percent=60,
            forward_overlap_percent=75,
            maximum_gsd_cm=5,
            strip_width_m=200,
            maximum_segment_distance_km=10,
            sensor=SensorProfile(
                sensor_id="camera",
                name="RGB-камера",
                sensor_width_mm=13.2,
                sensor_height_mm=8.8,
                focal_length_mm=8.8,
                image_width_px=5_472,
                image_height_px=3_648,
                minimum_trigger_interval_s=1,
            ),
        ),
        mission_altitude_m=100,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    missions = response.drone_plans[0].missions
    assert missions
    assert missions[0].image_count is not None
    assert missions[0].computed_gsd_cm == pytest.approx(2.741, abs=0.001)
    survey_stages = [
        stage for stage in missions[0].stages if stage.stage_type is MissionStageType.SURVEY
    ]
    assert len(survey_stages) > 1
    assert all(len(stage.coordinates) == 2 for stage in survey_stages)
    assert sum(stage.duration_minutes for stage in missions[0].stages) == pytest.approx(
        missions[0].duration_minutes
    )


def test_build_plan_routes_coverage_around_target_area_hole() -> None:
    area = CoverageArea(
        area_id="field-with-hole",
        name="Поле, содержащее внутренний контур",
        coordinates=[
            Coordinate(longitude=37.4, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.71),
            Coordinate(longitude=37.4, latitude=55.71),
        ],
        interior_rings=[
            [
                Coordinate(longitude=37.404, latitude=55.704),
                Coordinate(longitude=37.406, latitude=55.704),
                Coordinate(longitude=37.406, latitude=55.706),
                Coordinate(longitude=37.404, latitude=55.706),
            ]
        ],
    )
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=70,
                maximum_flight_minutes=180,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[area],
        survey_parameters=SurveyParameters(
            flight_direction_degrees=90,
            side_overlap_percent=60,
            forward_overlap_percent=75,
            maximum_gsd_cm=5,
            strip_width_m=200,
            maximum_segment_distance_km=20,
            sensor=SensorProfile(
                sensor_id="camera",
                name="RGB-камера",
                sensor_width_mm=13.2,
                sensor_height_mm=8.8,
                focal_length_mm=8.8,
                image_width_px=5_472,
                image_height_px=3_648,
                minimum_trigger_interval_s=1,
            ),
        ),
        mission_altitude_m=100,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    survey_routes = [mission.stages[2].coordinates for mission in response.drone_plans[0].missions]
    assert all(route_is_inside_coverage_area(route, area) for route in survey_routes)


def test_balanced_plan_minimizes_flight_while_meeting_deadline() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.BALANCED,
        drones=[
            DroneSpec(
                drone_id="fast",
                name="Быстрый",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="slow",
                name="Медленный",
                cruise_speed_kmh=30,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        segments=[
            WorkSegment(
                segment_id=f"sector-{index}",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4 + index * 0.01, latitude=55.7),
                    Coordinate(longitude=37.405 + index * 0.01, latitude=55.705),
                ],
            )
            for index in range(2)
        ],
        mission_altitude_m=100,
        task=MissionTask.RGB,
        completion_deadline_minutes=20,
    )

    response = create_plan(request)

    assert [plan.mission_count for plan in response.drone_plans] == [2, 0]
    assert response.metrics.total_flight_minutes == 20
    assert response.feasibility is not None
    assert response.feasibility.status == "feasible"


def test_plan_reports_required_drones_when_limit_breaks_deadline() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id="first",
                name="Первый",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
            DroneSpec(
                drone_id="second",
                name="Второй",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            ),
        ],
        segments=[
            WorkSegment(
                segment_id=f"sector-{index}",
                distance_km=10,
                route=[
                    Coordinate(longitude=37.4 + index * 0.01, latitude=55.7),
                    Coordinate(longitude=37.405 + index * 0.01, latitude=55.705),
                ],
            )
            for index in range(2)
        ],
        mission_altitude_m=100,
        task=MissionTask.RGB,
        maximum_active_drones=1,
        completion_deadline_minutes=15,
    )

    response = create_plan(request)

    assert response.metrics.active_drone_count == 1
    assert response.feasibility is not None
    assert response.feasibility.status == "infeasible"
    assert response.feasibility.estimated_required_drone_count == 2
    assert response.feasibility.deadline_margin_minutes == -5
    assert [item.code for item in response.feasibility.recommendations] == [
        "extend_deadline",
        "increase_active_drone_limit",
    ]
    assert response.feasibility.recommendations[0].required_value == 20


def test_plan_projects_maintenance_threshold_after_sortie() -> None:
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="201",
                name="Геоскан 201",
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
                maintenance_interval_flights=80,
                completed_flights_since_maintenance=79,
            )
        ],
        segments=[
            WorkSegment(
                segment_id="last-before-maintenance",
                distance_km=2,
                route=[
                    Coordinate(longitude=37.4, latitude=55.7),
                    Coordinate(longitude=37.405, latitude=55.705),
                ],
            )
        ],
        mission_altitude_m=100,
        task=MissionTask.RGB,
    )

    response = create_plan(request)

    maintenance = response.drone_plans[0].maintenance
    assert maintenance is not None
    assert maintenance.projected_flights == 80
    assert maintenance.remaining_flights == 0
    assert maintenance.maintenance_due_after_plan
