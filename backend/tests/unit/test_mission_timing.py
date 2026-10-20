import pytest

from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters, WorkSegment
from geoplan.domain.enums import MissionStageType, MissionTask, OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import DronePlan, Mission, MissionStage
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.flight.models import MissionDurationRequest
from geoplan.flight.performance import route_duration_minutes
from geoplan.flight.trajectory import mission_route_clock
from geoplan.geodata.exporters import _mission_commands
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.models import PlanRequest
from geoplan.planning.reporting import _create_metrics
from geoplan.safety.audit import _coverage_check
from geoplan.safety.audit import _timed_legs as audit_legs
from geoplan.scheduling.models import VerticalLayerOption
from geoplan.scheduling.scheduler import _timed_legs as schedule_legs
from geoplan.scheduling.vertical import apply_vertical_layer


def phased_mission():
    projection = LocalProjection(37, 55)
    a, b, c = [projection.unproject(x, 0) for x in (0, 1000, 2000)]
    return Mission(
        drone_id="uav",
        segment_id="area",
        duration_minutes=3,
        distance_km=route_distance_km([a, b, c]),
        route=[a, b, c],
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=40,
        estimated_battery_percent=10,
        start_offset_minutes=0,
        end_offset_minutes=3,
        survey_distance_km=route_distance_km([b, c]),
        image_count=11,
        stages=[
            MissionStage(stage_type=MissionStageType.TAKEOFF, coordinates=[a]),
            MissionStage(
                stage_type=MissionStageType.TRANSIT, coordinates=[a, b], duration_minutes=1
            ),
            MissionStage(
                stage_type=MissionStageType.SURVEY, coordinates=[b, c], duration_minutes=2
            ),
            MissionStage(stage_type=MissionStageType.LANDING, coordinates=[c]),
        ],
    )


def test_scheduler_and_audit_respect_phase_time_instead_of_uniform_route_speed():
    mission = phased_mission()
    projection = LocalProjection(37, 55)
    assert schedule_legs(mission, 0, projection)[0].end_minutes == pytest.approx(1)
    assert audit_legs(mission, projection)[0].end_minutes == pytest.approx(1)


def test_export_commands_the_survey_ground_speed_before_entering_service():
    mission = phased_mission()
    commands = _mission_commands(mission)
    survey_speed_mps = mission.survey_distance_km * 1000 / 120
    assert any(
        c.command == 178 and c.params[0] == 1 and c.params[1] == pytest.approx(survey_speed_mps)
        for c in commands
    )


def test_wind_leg_times_survive_estimation_and_vertical_deconfliction():
    mission = phased_mission()
    a, b, c = mission.route
    wind = WindCondition(speed_mps=5, direction_degrees=90)
    # Return in the opposite direction inside the same SURVEY phase.
    route = [a, b, c, b]
    estimate = CruiseMissionDurationCalculator().calculate(
        MissionDurationRequest(
            route=route,
            distance_km=route_distance_km(route),
            cruise_speed_kmh=60,
            wind=wind,
            transit_route=[a, b],
            transit_distance_km=route_distance_km([a, b]),
            survey_route=[b, c, b],
            survey_distance_km=route_distance_km([b, c, b]),
            speed_limit_kmh=45,
        )
    )
    assert estimate.transit_leg_durations_minutes is not None
    assert estimate.survey_leg_durations_minutes is not None
    transit = estimate.transit_leg_durations_minutes
    survey = estimate.survey_leg_durations_minutes
    assert survey[0] != pytest.approx(survey[1])
    mission = mission.model_copy(
        update={
            "route": route,
            "duration_minutes": estimate.precise_duration_minutes,
            "end_offset_minutes": estimate.precise_duration_minutes,
            "stages": [
                MissionStage(stage_type=MissionStageType.TAKEOFF, coordinates=[a]),
                MissionStage(
                    stage_type=MissionStageType.TRANSIT,
                    coordinates=[a, b],
                    duration_minutes=sum(transit),
                    leg_durations_minutes=transit,
                ),
                MissionStage(
                    stage_type=MissionStageType.SURVEY,
                    coordinates=[b, c, b],
                    duration_minutes=sum(survey),
                    leg_durations_minutes=survey,
                ),
                MissionStage(stage_type=MissionStageType.LANDING, coordinates=[b]),
            ],
        }
    )
    expected = route_duration_minutes([a, b], route_distance_km([a, b]), 60, wind)
    expected += route_duration_minutes([b, c], route_distance_km([b, c]), 60, wind, 45)
    clock = mission_route_clock(mission)
    assert clock.offsets_minutes[2] == pytest.approx(expected)
    shifted = apply_vertical_layer(
        mission,
        VerticalLayerOption(
            offset_m=30,
            climb_duration_minutes=0.5,
            descent_duration_minutes=0.4,
            extra_energy_equivalent_minutes=1,
            extra_battery_percent=1,
        ),
    )
    assert mission_route_clock(shifted).offsets_minutes == pytest.approx(clock.offsets_minutes)
    projection = LocalProjection(37, 55)
    assert schedule_legs(shifted, 0, projection)[2].end_minutes == pytest.approx(expected + 0.5)


@pytest.mark.parametrize("times", ([1], [-1, 2], [float("nan"), 1], [0, 1]))
def test_explicit_leg_clock_rejects_missing_invalid_or_zero_movement_time(times):
    mission = phased_mission()
    with pytest.raises(ValueError):
        MissionStage(
            stage_type=MissionStageType.SURVEY,
            coordinates=mission.route,
            duration_minutes=3,
            leg_durations_minutes=times,
        )


def test_fleet_utilization_does_not_divide_rounded_flight_time_by_precise_schedule():
    mission = phased_mission().model_copy(
        update={"duration_minutes": 1.2356, "end_offset_minutes": 1.236}
    )
    plan = DronePlan(
        drone_id=mission.drone_id, mission_count=1, total_duration_minutes=1.24, missions=[mission]
    )
    metrics = _create_metrics([plan], 1)
    assert metrics.average_fleet_utilization_percent == 100
    assert metrics.total_flight_minutes == 1.24


def test_explicit_mission_clock_rejects_partial_route_and_inconsistent_total():
    mission = phased_mission()
    stage = mission.stages[1].model_copy(update={"leg_durations_minutes": [1.0]})
    with pytest.raises(ValueError, match="All moving stages"):
        Mission.model_validate(
            {**mission.model_dump(), "stages": [mission.stages[0], stage, *mission.stages[2:]]}
        )
    stages = [
        s.model_copy(update={"leg_durations_minutes": [s.duration_minutes]})
        if len(s.coordinates) == 2
        else s
        for s in mission.stages
    ]
    with pytest.raises(ValueError, match="complete horizontal"):
        Mission.model_validate({**mission.model_dump(), "stages": stages, "duration_minutes": 4})


def test_coverage_does_not_join_separate_service_stages():
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    projection = LocalProjection(37, 55)
    a, b, c, d = [
        projection.unproject(x, y) for x, y in ((100, 100), (200, 100), (800, 900), (900, 900))
    ]
    mission = phased_mission().model_copy(
        update={
            "route": [a, b, c, d],
            "stages": [
                MissionStage(
                    stage_type=MissionStageType.SURVEY, coordinates=[a, b], duration_minutes=1
                ),
                MissionStage(
                    stage_type=MissionStageType.TRANSIT, coordinates=[b, c], duration_minutes=1
                ),
                MissionStage(
                    stage_type=MissionStageType.SURVEY, coordinates=[c, d], duration_minutes=1
                ),
            ],
        }
    )
    area = CoverageArea(
        area_id="area",
        name="Area",
        coordinates=[
            projection.unproject(x, y) for x, y in ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        ],
    )
    parameters = SurveyParameters(
        flight_direction_degrees=90,
        side_overlap_percent=50,
        forward_overlap_percent=70,
        maximum_gsd_cm=20,
        strip_width_m=100,
        sensor=SensorProfile(
            sensor_id="cam",
            name="Camera",
            sensor_width_mm=35.9,
            sensor_height_mm=24,
            focal_length_mm=35,
            image_width_px=7952,
            image_height_px=5304,
            minimum_trigger_interval_s=0.5,
        ),
    )
    request = PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=[
            DroneSpec(
                drone_id="uav",
                name="UAV",
                cruise_speed_kmh=60,
                maximum_flight_minutes=30,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
        ],
        coverage_areas=[area],
        survey_parameters=parameters,
        mission_altitude_m=120,
        task=MissionTask.RGB,
    )
    segment = WorkSegment(
        segment_id="area",
        distance_km=2,
        route=mission.route,
        coverage_area_id="area",
        strip_spacing_m=50,
    )
    expected = (
        100
        * unary_union(
            [
                LineString([projection.project(x) for x in route]).buffer(50.5, cap_style="square")
                for route in ([a, b], [c, d])
            ]
        )
        .intersection(Polygon([projection.project(p) for p in area.coordinates]))
        .area
        / 1_000_000
    )
    assert _coverage_check(request, [segment], [mission]).measured_value == pytest.approx(
        expected, abs=0.01
    )


def test_legacy_distance_estimate_handles_stationary_geometry_without_division_by_zero():
    point = phased_mission().route[0]
    estimate = CruiseMissionDurationCalculator().calculate(
        MissionDurationRequest(
            route=[point, point],
            distance_km=2,
            cruise_speed_kmh=60,
            survey_route=[point, point],
            survey_distance_km=2,
        )
    )
    assert estimate.duration_minutes == 2


def test_explicit_stage_clock_cannot_hide_unmodelled_dwell_time():
    mission = phased_mission()
    stages = [
        s.model_copy(update={"leg_durations_minutes": [s.duration_minutes]})
        if len(s.coordinates) == 2
        else s
        for s in mission.stages
    ]
    stages[1] = stages[1].model_copy(update={"duration_minutes": 1.1})
    with pytest.raises(ValueError, match="Stage duration"):
        Mission.model_validate({**mission.model_dump(), "stages": stages})
