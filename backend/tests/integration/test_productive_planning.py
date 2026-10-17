from dataclasses import replace
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import CoverageArea, SensorProfile, SurveyParameters, WorkSegment
from geoplan.domain.enums import (
    MissionStageType,
    MissionTask,
    OptimizationCriterion,
    SweepDirectionMode,
)
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.mission import Mission
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem, Track, Trip


def survey_request(**overrides) -> PlanRequest:
    projection = LocalProjection(37, 55)
    home = FlightSite(site_id="base", name="Base", coordinate=projection.unproject(-500, -500))
    return PlanRequest.model_validate(
        {
            "criterion": OptimizationCriterion.TOTAL_FLIGHT_TIME,
            "productive_routing": True,
            "drones": [
                DroneSpec(
                    drone_id="fast",
                    name="Survey",
                    cruise_speed_kmh=90,
                    maximum_flight_minutes=60,
                    battery_reserve_percent=20,
                    supported_tasks=[MissionTask.RGB],
                )
            ],
            "coverage_areas": [
                CoverageArea(
                    area_id="field",
                    name="Field",
                    coordinates=[
                        projection.unproject(x, y)
                        for x, y in ((0, 0), (1200, 0), (1200, 600), (0, 600))
                    ],
                )
            ],
            "survey_parameters": SurveyParameters(
                direction_mode=SweepDirectionMode.MANUAL,
                flight_direction_degrees=90,
                side_overlap_percent=60,
                forward_overlap_percent=75,
                maximum_gsd_cm=5,
                strip_width_m=200,
                maximum_segment_distance_km=1.5,
                sensor=SensorProfile(
                    sensor_id="camera",
                    name="Camera",
                    sensor_width_mm=13.2,
                    sensor_height_mm=8.8,
                    focal_length_mm=8.8,
                    image_width_px=5472,
                    image_height_px=3648,
                    minimum_trigger_interval_s=4,
                ),
            ),
            "mission_altitude_m": 120,
            "task": MissionTask.RGB,
            "departure_site": home,
            "landing_site": home,
            "maximum_active_drones": 1,
            **overrides,
        }
    )


def post_plan(request: PlanRequest, dependencies=None) -> PlanResponse:
    with TestClient(
        create_app(dependencies or create_default_dependencies(), serve_frontend=False)
    ) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    return PlanResponse.model_validate(response.json())


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_http_productive_plan_merges_sorties_and_preserves_reserve_camera_and_clock(criterion):
    request = survey_request(criterion=criterion, completion_deadline_minutes=60)
    baseline = post_plan(request.model_copy(update={"productive_routing": False}))
    plan = post_plan(request)
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.algorithm == "productive_route_pool"
    assert plan.metrics.sortie_count < baseline.metrics.sortie_count
    assert plan.metrics.total_flight_minutes < baseline.metrics.total_flight_minutes
    assert plan.metrics.active_drone_count == 1
    for drone in plan.drone_plans:
        for mission in drone.missions:
            assert mission.battery_reserve_margin_percent is not None
            assert mission.battery_reserve_margin_percent >= 0
            assert mission.route_leg_durations_minutes is not None
            assert (
                Mission.model_validate(mission.model_dump()).route_leg_durations_minutes is not None
            )
            assert sum(s.stage_type is MissionStageType.SURVEY for s in mission.stages) > 1
            assert any(s.stage_type is MissionStageType.TRANSIT for s in mission.stages[2:-1])
            service = sum(
                route_distance_km(s.coordinates)
                for s in mission.stages
                if s.stage_type is MissionStageType.SURVEY
            )
            assert mission.survey_distance_km == pytest.approx(service, abs=0.001)
            for stage in mission.stages:
                if stage.stage_type is MissionStageType.SURVEY:
                    for (a, b), duration in zip(
                        pairwise(stage.coordinates), stage.leg_durations_minutes or [], strict=True
                    ):
                        assert route_distance_km([a, b]) * 60 / duration <= 27.0001


def test_distinct_landing_site_is_costed_and_checked_in_shared_routing_model():
    projection = LocalProjection(37, 55)
    start, end = projection.unproject(0, 0), projection.unproject(1000, 0)
    landing = projection.unproject(1100, 0)
    model = CostModel(
        RoutingProblem(
            (Track("strip", start, end, 45),),
            (Aircraft("drone", 60, 30, start, landing=landing),),
            None,
            None,
            (),
            OptimizationCriterion.TOTAL_FLIGHT_TIME,
            None,
        )
    )
    solution = (Trip(0, (0,)),)
    cost = model.trip(solution[0])
    assert cost is not None
    assert cost.returning == pytest.approx(route_distance_km([end, landing]), abs=1e-8)
    assert model.validate_geometry(solution)
    from geoplan.routing.ordering import order_cost

    assert order_cost(model, (0,)) == pytest.approx(100.0, abs=1e-4)


def test_productive_plan_obeys_assigned_distinct_landing_site():
    request = survey_request()
    assert request.landing_site is not None
    landing = request.landing_site.model_copy(
        update={
            "site_id": "landing",
            "coordinate": LocalProjection(37, 55).unproject(-100, -200),
            "assigned_drone_ids": ["fast"],
        }
    )
    plan = post_plan(request.model_copy(update={"landing_site": landing}))
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.algorithm == "productive_route_pool"
    assert all(m.route[-1] == landing.coordinate for d in plan.drone_plans for m in d.missions)


def test_manual_routes_keep_existing_pipeline_with_explicit_explanation():
    request = survey_request()
    manual = WorkSegment(
        segment_id="manual", distance_km=1, route=request.coverage_areas[0].coordinates[:2]
    )
    plan = post_plan(request.model_copy(update={"coverage_areas": [], "segments": [manual]}))
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.productive_routing.selected_variant == "baseline"
    assert plan.optimization.productive_routing.evaluated_variants == 0


def test_failed_reference_split_can_be_rescued_by_battery_bounded_groups():
    request = survey_request()
    assert request.survey_parameters is not None
    parameters = request.survey_parameters.model_copy(update={"maximum_segment_distance_km": 500})
    drone = request.drones[0].model_copy(update={"maximum_flight_minutes": 12})
    plan = post_plan(
        request.model_copy(update={"survey_parameters": parameters, "drones": [drone]})
    )
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.productive_routing.baseline_makespan_minutes is None


def test_failed_audit_cannot_replace_verified_reference_plan():
    deps = create_default_dependencies()

    class RejectProductiveAudit:
        def audit(self, request, segments, missions):
            audit = deps.safety_auditor.audit(request, segments, missions)
            return (
                audit.model_copy(update={"status": "failed"})
                if any("-productive-" in m.segment_id for m in missions)
                else audit
            )

    plan = post_plan(survey_request(), replace(deps, safety_auditor=RejectProductiveAudit()))
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.productive_routing.selected_variant == "baseline"
    assert plan.optimization.productive_routing.evaluated_variants > 0


def test_lower_load_cannot_override_worse_actual_scheduled_completion():
    deps = create_default_dependencies()

    class DelayProductiveSchedule:
        def schedule(self, request):
            result = deps.mission_scheduler.schedule(request)
            if not any("-productive-" in m.segment_id for m in request.missions):
                return result
            shifted = []
            for mission in result.missions:
                assert (
                    mission.start_offset_minutes is not None
                    and mission.end_offset_minutes is not None
                )
                shifted.append(
                    mission.model_copy(
                        update={
                            "start_offset_minutes": mission.start_offset_minutes + 1000,
                            "end_offset_minutes": mission.end_offset_minutes + 1000,
                        }
                    )
                )
            return result.model_copy(
                update={"missions": shifted, "makespan_minutes": result.makespan_minutes + 1000}
            )

    plan = post_plan(
        survey_request(criterion=OptimizationCriterion.COMPLETION_TIME),
        replace(deps, mission_scheduler=DelayProductiveSchedule()),
    )
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.productive_routing.selected_variant == "baseline"
    assert plan.optimization.productive_routing.evaluated_variants > 0
