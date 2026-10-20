from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from geoplan import budget
from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.domain.enums import AssignmentStrategy, OptimizationCriterion, SafetyAuditStatus
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanResponse
from geoplan.planning.plan_quality import scheduled_plan_objective
from scenarios.planning import parallel_survey_request, productive_survey_request


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_multi_area_pool_protects_complete_audited_plan_and_all_exports(criterion):
    request = parallel_survey_request(criterion=criterion, completion_deadline_minutes=100)
    assert request.survey_parameters is not None
    projection = LocalProjection(37, 55)
    request = request.model_copy(
        update={
            "coverage_areas": [
                request.coverage_areas[0].model_copy(
                    update={
                        "area_id": f"field-{i}",
                        "coordinates": [
                            projection.unproject(x + i * 1800, y)
                            for x, y in ((0, 0), (1200, 0), (1200, 600), (0, 600))
                        ],
                    }
                )
                for i in range(2)
            ],
            "survey_parameters": request.survey_parameters.model_copy(
                update={"maximum_segment_distance_km": 2}
            ),
            "assignment_strategy": AssignmentStrategy.HYBRID,
        }
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        plans = []
        for enabled in (False, True):
            response = client.post(
                "/v1/plans",
                json=request.model_copy(update={"productive_routing": enabled}).model_dump(
                    mode="json"
                ),
            )
            assert response.status_code == 200, response.text
            plan = PlanResponse.model_validate(response.json())
            assert (
                plan.safety_audit is not None
                and plan.safety_audit.status is SafetyAuditStatus.PASSED
            )
            plans.append(plan)
        assert scheduled_plan_objective(request, plans[1]) <= scheduled_plan_objective(
            request, plans[0]
        )
        assert plans[1].optimization is not None
        assert plans[1].optimization.productive_routing is not None
        assert plans[1].optimization.productive_routing.evaluated_variants > 0
        for drone in plans[1].drone_plans:
            if not drone.missions:
                continue
            for kind in ("geojson", "kml", "qgroundcontrol", "mission-planner"):
                exported = client.post(
                    f"/v1/exports/{kind}",
                    json={
                        "plan": plans[1].model_dump(mode="json"),
                        "drone_id": drone.drone_id,
                        "sortie_index": 0,
                    },
                )
                assert exported.status_code == 200, exported.text


def test_timeout_after_audited_baseline_returns_that_plan():
    class TimedOutPool:
        def generate(self, *args, **kwargs):
            raise budget.PlanningTimeExceeded()

    request = productive_survey_request()
    dependencies = replace(create_default_dependencies(), productive_route_pool=TimedOutPool())
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json()["safety_audit"]["status"] == "passed"


def test_timeout_before_audited_baseline_returns_diagnostic_and_does_not_leak(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(budget, "monotonic", lambda: now[0])

    class SlowCoverage(BoustrophedonCoveragePlanner):
        def plan(self, request):
            now[0] += 300
            budget.check_planning_budget()
            return super().plan(request)

    request = productive_survey_request(planning_time_limit_seconds=270)
    dependencies = replace(create_default_dependencies(), coverage_planner=SlowCoverage())
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 422
    assert response.json()["code"] == "planning_time_limit"
    budget.check_planning_budget()


def test_candidate_batch_stops_before_evaluating_the_next_drone_or_segment(monkeypatch):
    from geoplan.navigation.optimizer import BidirectionalRouteOptimizer

    now, calls = [0.0], [0]
    monkeypatch.setattr(budget, "monotonic", lambda: now[0])

    class SlowRoutes(BidirectionalRouteOptimizer):
        def optimize(self, request):
            calls[0] += 1
            now[0] = 2
            return super().optimize(request)

    request = productive_survey_request(
        planning_time_limit_seconds=1,
        optimization_mode="fast_heuristic",
        productive_routing=False,
    )
    dependencies = replace(create_default_dependencies(), route_optimizer=SlowRoutes())
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 422
    assert response.json()["code"] == "planning_time_limit"
    assert calls == [1]
    budget.check_planning_budget()
