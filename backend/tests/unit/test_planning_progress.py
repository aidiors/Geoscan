from dataclasses import replace
from threading import Event

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from geoplan import budget
from geoplan.api.app import create_app
from geoplan.api.planning_transport import CancellablePlanningResponse, PlanningTransport
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import ErrorCode, SafetyAuditStatus, SweepDirectionMode
from geoplan.errors import PlanningError
from geoplan.planning.models import PlanResponse
from geoplan.planning.progress import AuditedPlanProgress
from geoplan.planning.service import AuditedPlanningService
from scenarios.neighbor_fields import neighbor_fields_request
from scenarios.planning import productive_survey_request


@pytest.fixture(scope="module")
def audited_plan():
    request = productive_survey_request(productive_routing=False)
    dependencies = replace(create_default_dependencies(), alternative_coverage_planner=None)
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    return PlanResponse.model_validate(response.json())


def scaled_result(plan, factor):
    """Stub pipeline results; orchestration tests do not replace physical audits."""
    plans = [
        p.model_copy(
            update={
                "missions": [
                    m.model_copy(update={"duration_minutes": m.duration_minutes * factor})
                    for m in p.missions
                ]
            }
        )
        for p in plan.drone_plans
    ]
    return plan.model_copy(
        update={
            "drone_plans": plans,
            "metrics": plan.metrics.model_copy(
                update={
                    "completion_minutes": plan.metrics.completion_minutes * factor,
                    "total_flight_minutes": plan.metrics.total_flight_minutes * factor,
                }
            ),
            "schedule": plan.schedule.model_copy(
                update={"makespan_minutes": plan.schedule.makespan_minutes * factor}
            ),
        }
    )


def test_timeout_keeps_latest_audited_incumbent_not_only_original_baseline(audited_plan):
    improved = scaled_result(audited_plan, 0.8)

    def runner(request, observer):
        observer("baseline", audited_plan)
        observer("improved", improved)
        raise budget.PlanningTimeExceeded()

    received = []
    result = AuditedPlanningService(runner).plan(
        productive_survey_request(), lambda _, p: received.append(p)
    )
    assert result.metrics == improved.metrics
    assert result.search is not None and result.search.status == "time_limit"
    assert len(received) == 2
    assert (
        received[-1].search.checkpoints[-1].objective < received[0].search.checkpoints[-1].objective
    )


def test_worse_or_unaudited_candidates_never_replace_the_incumbent(audited_plan):
    progress = AuditedPlanProgress(productive_survey_request())
    progress.offer("base", audited_plan)
    progress.offer("worse", scaled_result(audited_plan, 2))
    failed = scaled_result(audited_plan, 0.1).model_copy(
        update={
            "safety_audit": audited_plan.safety_audit.model_copy(
                update={
                    "status": SafetyAuditStatus.FAILED,
                    "checks": [
                        c.model_copy(update={"status": SafetyAuditStatus.FAILED})
                        if c.code.value == "coverage_completeness"
                        else c
                        for c in audited_plan.safety_audit.checks
                    ],
                }
            )
        }
    )
    progress.offer("failed", failed)
    assert progress.best is audited_plan
    assert len(progress.checkpoints) == 1


def test_pipeline_report_explains_rejected_candidates_and_preserves_seed(audited_plan):
    failed = scaled_result(audited_plan, 0.5).model_copy(
        update={
            "safety_audit": audited_plan.safety_audit.model_copy(
                update={
                    "status": SafetyAuditStatus.FAILED,
                    "checks": [
                        c.model_copy(update={"status": SafetyAuditStatus.FAILED})
                        if c.code.value == "coverage_completeness"
                        else c
                        for c in audited_plan.safety_audit.checks
                    ],
                }
            )
        }
    )
    result = AuditedPlanningService(
        primary=lambda request, observer: failed,
        initial=lambda request, observer: audited_plan,
    ).plan(productive_survey_request())
    assert result.metrics == audited_plan.metrics
    assert result.search is not None
    initial, primary = result.search.pipelines
    assert initial.status == "improved"
    assert primary.status == "rejected" and primary.audited_events == 0
    assert primary.failed_check_codes == ["coverage_completeness"]


def test_pipeline_error_reason_is_retained_without_discarding_incumbent(audited_plan):
    def failed(request, observer):
        raise PlanningError(ErrorCode.COVERAGE_GENERATION_FAILED, "No valid coverage")

    result = AuditedPlanningService(
        primary=failed,
        initial=lambda request, observer: audited_plan,
    ).plan(productive_survey_request())
    assert result.search is not None
    assert result.search.status == "retained_after_error"
    assert result.search.pipelines[-1].status == "error"
    assert result.search.pipelines[-1].error_code == "coverage_generation_failed"


def test_all_pipeline_variants_share_the_request_deadline(audited_plan, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(budget, "monotonic", lambda: now[0])
    request = neighbor_fields_request(planning_time_limit_seconds=60)
    assert request.survey_parameters is not None
    request = request.model_copy(
        update={
            "survey_parameters": request.survey_parameters.model_copy(
                update={"direction_mode": SweepDirectionMode.AUTOMATIC}
            )
        }
    )

    def primary(request, observer):
        now[0] += 30
        return audited_plan

    def alternative(request, observer):
        now[0] += 31
        budget.check_planning_budget()
        return scaled_result(audited_plan, 0.5)

    result = AuditedPlanningService(primary, alternative).plan(request)
    assert result.metrics == audited_plan.metrics
    assert result.search is not None and result.search.status == "stage_time_limit"
    budget.check_planning_budget()


def test_stream_has_bounded_admission_and_disconnect_releases_worker(audited_plan):
    entered, stopped = Event(), Event()

    def runner(request, observer):
        observer("base", audited_plan)
        entered.set()
        try:
            while not stopped.wait(0.01):
                budget.check_planning_budget()
        finally:
            stopped.set()
        return audited_plan

    transport = PlanningTransport(AuditedPlanningService(runner), maximum_concurrent=1)
    response = transport.stream(productive_survey_request())
    assert isinstance(response, CancellablePlanningResponse)
    try:
        assert entered.wait(2)
        rejected = transport.stream(productive_survey_request())
        assert isinstance(rejected, JSONResponse) and rejected.status_code == 503
        with pytest.raises(PlanningError):
            transport.plan(productive_survey_request())
    finally:
        response.cancelled.set()
        assert stopped.wait(2)
        assert transport._slots.acquire(timeout=2)
        transport._slots.release()
    budget.check_planning_budget()
