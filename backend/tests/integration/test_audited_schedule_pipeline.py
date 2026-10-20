import pytest
from fastapi.testclient import TestClient

from benchmarks.vertical_deconfliction import _request
from geoplan.airspace.windows import _vertical_layers_by_segment_id
from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import OptimizationCriterion, SafetyAuditStatus
from geoplan.planning.models import PlanResponse
from geoplan.planning.schedule_portfolio import AuditedSchedulePortfolio
from geoplan.scheduling.models import MissionScheduleRequest


@pytest.fixture(scope="module")
def fixed_flight_problem():
    """Hold assignment/geometry fixed; retain the real flight model, audit and export verifier."""
    request = _request(0, None, True)
    dependencies = create_default_dependencies()
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    plan = PlanResponse.model_validate(response.json())
    assert plan.safety_audit is not None
    assert plan.safety_audit.status is SafetyAuditStatus.PASSED
    assert plan.metrics.active_drone_count == 2
    return dependencies, request, [m for drone in plan.drone_plans for m in drone.missions]


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_real_flight_and_export_audit_passes_for_every_criterion(fixed_flight_problem, criterion):
    dependencies, original_request, missions = fixed_flight_problem
    request = original_request.model_copy(
        update={
            "criterion": criterion,
            "completion_deadline_minutes": 10,
            "maximum_vertical_deconfliction_m": 60,
        }
    )
    schedule_request = MissionScheduleRequest(
        missions=missions,
        minimum_separation_m=request.minimum_separation_m,
        minimum_vertical_separation_m=request.minimum_vertical_separation_m,
        turnaround_minutes=request.turnaround_minutes,
        vertical_layers_by_segment_id=_vertical_layers_by_segment_id(missions, request),
    )
    selected = AuditedSchedulePortfolio(
        dependencies.mission_scheduler, dependencies.safety_auditor
    ).select(request, request.segments, schedule_request)
    assert selected.audit.status is SafetyAuditStatus.PASSED
    assert selected.reference_makespan_minutes is not None
    assert selected.reference_total_flight_minutes is not None
    flight = sum(m.duration_minutes for m in selected.schedule.missions)
    if criterion is OptimizationCriterion.COMPLETION_TIME:
        assert selected.schedule.makespan_minutes <= selected.reference_makespan_minutes
        assert any(m.vertical_deconfliction_offset_m > 0 for m in selected.schedule.missions)
    else:
        assert flight < selected.reference_total_flight_minutes
        assert all(m.vertical_deconfliction_offset_m == 0 for m in selected.schedule.missions)
    assert selected.schedule.makespan_minutes < 10
