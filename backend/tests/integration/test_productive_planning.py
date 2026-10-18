from dataclasses import replace
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import (
    MissionStageType,
    OptimizationCriterion,
    SafetyAuditStatus,
)
from geoplan.domain.mission import Mission
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import scheduled_plan_objective
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem, Track, Trip
from geoplan.routing.pool import RoutePoolBatch
from scenarios.optimization_scale import scale_scenarios
from scenarios.planning import parallel_survey_request
from scenarios.planning import productive_survey_request as survey_request


class FixedTrackOnly:
    """Isolate fixed-track acceptance from the separately tested freeform search."""

    def improve(self, request, source, drones, baseline, build, evaluate, reference=None):
        return baseline


def test_route_pool_is_injected_through_http_composition():
    class EmptyPool:
        def __init__(self):
            self.calls = []

        def generate(self, model, maximum_active_drones=None):
            self.calls.append((len(model.problem.tracks), maximum_active_drones))
            return RoutePoolBatch()

    pool = EmptyPool()
    dependencies = replace(
        create_default_dependencies(),
        productive_route_pool=pool,
        freeform_optimizer=FixedTrackOnly(),
    )
    plan = post_plan(parallel_survey_request(), dependencies)
    assert pool.calls and pool.calls[0][0] > 1 and pool.calls[0][1] == 2
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"


def test_deadline_mode_considers_fast_assignments_of_productive_groups():
    source = next(
        case.request for case in scale_scenarios() if case.name == "evaluation_long_windy_100"
    )
    request = source.model_copy(
        update={
            "criterion": OptimizationCriterion.BALANCED,
            "completion_deadline_minutes": 170,
        }
    )
    plan = post_plan(request)
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.schedule is not None and plan.schedule.makespan_minutes <= 170
    assert plan.feasibility is not None and plan.feasibility.status == "feasible"


def test_completion_mode_uses_economical_groupings_as_search_seeds():
    source = next(case.request for case in scale_scenarios() if case.name == "flat_100")
    request = source.model_copy(
        update={
            "criterion": OptimizationCriterion.COMPLETION_TIME,
            "completion_deadline_minutes": 170,
        }
    )
    plan = post_plan(request)
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.schedule is not None and plan.schedule.makespan_minutes < 140
    assert plan.criterion is OptimizationCriterion.COMPLETION_TIME
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    exchange = plan.optimization.productive_routing
    assert exchange.pre_cross_objective is not None
    assert scheduled_plan_objective(request, plan) <= tuple(exchange.pre_cross_objective)
    assert exchange.cross_objective_variants_evaluated > 0


def test_internal_search_deadline_does_not_leak_into_http_response():
    plan = post_plan(parallel_survey_request(completion_deadline_minutes=None))
    assert plan.feasibility is not None
    assert plan.feasibility.requested_deadline_minutes is None


@pytest.mark.parametrize(
    "criterion", [OptimizationCriterion.COMPLETION_TIME, OptimizationCriterion.BALANCED]
)
def test_parallel_sorties_meet_deadline_when_single_sortie_exceeds_it(criterion):
    request = parallel_survey_request(criterion=criterion, completion_deadline_minutes=25)
    baseline = post_plan(request.model_copy(update={"productive_routing": False}))
    plan = post_plan(request)
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.feasibility is not None and plan.feasibility.status == "feasible"
    assert plan.metrics.completion_minutes < 20 < baseline.metrics.completion_minutes
    assert plan.metrics.active_drone_count == 2
    assert plan.metrics.sortie_count == 2
    assert plan.optimization is not None and plan.schedule is not None
    assert plan.optimization.completion_minutes == plan.schedule.makespan_minutes


def test_failed_audit_does_not_claim_a_feasible_plan():
    dependencies = create_default_dependencies()

    class RejectAudit:
        def audit(self, request, segments, missions):
            report = dependencies.safety_auditor.audit(request, segments, missions)
            return report.model_copy(update={"status": SafetyAuditStatus.FAILED})

    plan = post_plan(
        survey_request(productive_routing=False),
        replace(dependencies, safety_auditor=RejectAudit()),
    )
    assert plan.safety_audit is not None and plan.safety_audit.status == "failed"
    assert plan.feasibility is not None and plan.feasibility.status == "infeasible"


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

    plan = post_plan(
        survey_request(),
        replace(deps, safety_auditor=RejectProductiveAudit(), freeform_optimizer=FixedTrackOnly()),
    )
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
        replace(
            deps, mission_scheduler=DelayProductiveSchedule(), freeform_optimizer=FixedTrackOnly()
        ),
    )
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None and plan.optimization.productive_routing is not None
    assert plan.optimization.productive_routing.selected_variant == "baseline"
    assert plan.optimization.productive_routing.evaluated_variants > 0
