from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.assignment.alternatives import AssignmentAlternative, CpSatAssignmentAlternative
from geoplan.assignment.models import (
    AssignmentOptimizationRequest,
    AssignmentOptimizationResult,
    MissionAssignmentCandidate,
)
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import (
    AssignmentAlgorithm,
    ErrorCode,
    MissionTask,
    OptimizationCriterion,
    OptimizationMode,
)
from geoplan.errors import PlanningError
from geoplan.planning.assignment_portfolio import AuditedAssignmentPortfolio
from geoplan.planning.models import PlanResponse
from scenarios.planning import productive_survey_request


@pytest.fixture(scope="module")
def baseline():
    dependencies = create_default_dependencies()
    request = productive_survey_request(productive_routing=False)
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        return PlanResponse.model_validate(response.json())


@pytest.fixture
def problem(baseline):
    candidates = [
        MissionAssignmentCandidate(segment_id=m.segment_id, drone_id=m.drone_id, mission=m)
        for drone in baseline.drone_plans
        for m in drone.missions
    ]
    request = AssignmentOptimizationRequest(
        criterion=baseline.criterion,
        drone_ids=list(dict.fromkeys(c.drone_id for c in candidates)),
        candidate_groups=[[c] for c in candidates],
    )
    result = AssignmentOptimizationResult(
        assignments=candidates,
        algorithm=baseline.optimization.algorithm,
        optimality=baseline.optimization.optimality,
        completion_minutes=baseline.metrics.completion_minutes,
        total_flight_minutes=baseline.metrics.total_flight_minutes,
        active_drone_count=baseline.metrics.active_drone_count,
    )
    return request, result


def with_completion(plan, minutes):
    return plan.model_copy(
        update={
            "schedule": plan.schedule.model_copy(update={"makespan_minutes": minutes}),
            "metrics": plan.metrics.model_copy(update={"completion_minutes": minutes}),
        }
    )


class FixedAlternative:
    def __init__(self, result=True, status="candidate"):
        self.result = result
        self.status = status

    def propose(self, request, incumbent):
        return AssignmentAlternative(incumbent if self.result else None, self.status)


@pytest.mark.parametrize("completion,accepted", [(19, True), (20, False), (26, False)])
def test_selection_uses_final_schedule_and_preserves_deadline(
    baseline, problem, completion, accepted
):
    request = productive_survey_request(
        criterion=OptimizationCriterion.BALANCED, completion_deadline_minutes=25
    )
    # Proxy assignment costs are deliberately absent: only scheduled costs may decide.
    incumbent = with_completion(baseline, 20)
    candidate = with_completion(baseline, completion)
    selected, report = AuditedAssignmentPortfolio(FixedAlternative()).improve(
        request, problem[0], problem[1], incumbent, lambda _: candidate
    )
    assert selected is (candidate if accepted else incumbent)
    assert report.candidate_accepted is accepted
    assert report.candidate_audited


def test_faster_plan_that_fails_audit_cannot_replace_baseline(baseline, problem):
    candidate = with_completion(baseline, 1).model_copy(
        update={"safety_audit": baseline.safety_audit.model_copy(update={"status": "failed"})}
    )
    selected, report = AuditedAssignmentPortfolio(FixedAlternative()).improve(
        productive_survey_request(), problem[0], problem[1], baseline, lambda _: candidate
    )
    assert selected is baseline
    assert not report.candidate_audited and not report.candidate_accepted


def test_schedule_failure_retains_existing_plan(baseline, problem):
    def reject(_):
        raise PlanningError(code=ErrorCode.SCHEDULE_GENERATION_FAILED, message="No window")

    selected, report = AuditedAssignmentPortfolio(FixedAlternative()).improve(
        productive_survey_request(), problem[0], problem[1], baseline, reject
    )
    assert selected is baseline
    assert report.status == "schedule_rejected"


@pytest.mark.parametrize("status", ["unavailable", "no_solution"])
def test_unavailable_solver_or_timeout_does_not_reschedule(baseline, problem, status):
    def never_evaluate(_):
        raise AssertionError("No candidate to schedule")

    selected, report = AuditedAssignmentPortfolio(FixedAlternative(None, status)).improve(
        productive_survey_request(), problem[0], problem[1], baseline, never_evaluate
    )
    assert selected is baseline and report.status == status


def test_api_reports_optional_solver_unavailable_and_fast_mode_skips_it():
    dependencies = replace(
        create_default_dependencies(), assignment_alternative=FixedAlternative(None, "unavailable")
    )
    request = productive_survey_request(
        productive_routing=False, assignment_strategy="cp_sat_portfolio"
    )
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        plan = PlanResponse.model_validate(response.json())
        assert plan.optimization is not None and plan.optimization.assignment_search is not None
        assert plan.optimization.assignment_search.status == "unavailable"
        fast = request.model_copy(update={"optimization_mode": OptimizationMode.FAST_HEURISTIC})
        response = client.post("/v1/plans", json=fast.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.json()["optimization"]["assignment_search"] is None


def test_real_cp_sat_api_keeps_audited_plan_and_reports_attempt():
    pytest.importorskip("ortools")
    request = productive_survey_request(
        productive_routing=False, assignment_strategy="cp_sat_portfolio"
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = PlanResponse.model_validate(response.json())
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.optimization is not None
    report = plan.optimization.assignment_search
    assert report is not None
    if report.status == "no_solution":
        # A wall-clock budget may expire under load, even on this small instance.
        assert not report.solver_statuses or report.solver_statuses == ["UNKNOWN"]
        assert not report.candidate_audited and not report.candidate_accepted
        assert plan.optimization.algorithm is not AssignmentAlgorithm.CP_SAT
    else:
        assert report.status in {"accepted", "retained_baseline"}
        assert report.candidate_audited and report.solver_statuses
    if plan.optimization.algorithm is AssignmentAlgorithm.CP_SAT:
        assert plan.optimization.optimality == "heuristic"


def test_real_cp_sat_exhausted_budget_retains_audited_api_baseline(baseline):
    pytest.importorskip("ortools")
    dependencies = replace(
        create_default_dependencies(),
        assignment_alternative=CpSatAssignmentAlternative(time_limit_seconds=1e-9),
    )
    request = productive_survey_request(
        productive_routing=False, assignment_strategy="cp_sat_portfolio"
    )
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = PlanResponse.model_validate(response.json())
    assert plan.safety_audit is not None and plan.safety_audit.status == "passed"
    assert plan.metrics == baseline.metrics
    assert plan.optimization is not None and plan.optimization.assignment_search is not None
    report = plan.optimization.assignment_search
    assert report.status == "no_solution"
    assert not report.candidate_accepted and not report.candidate_audited
    assert not report.integer_assignment_proven


def test_fleet_cap_can_exceed_number_of_task_eligible_aircraft():
    request = productive_survey_request(productive_routing=False)
    thermal = request.drones[0].model_copy(
        update={
            "drone_id": "thermal",
            "supported_tasks": [MissionTask.THERMAL],
        }
    )
    request = request.model_copy(
        update={
            "maximum_active_drones": 2,
            "drones": [request.drones[0], thermal],
        }
    )
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json()["metrics"]["active_drone_count"] == 1
