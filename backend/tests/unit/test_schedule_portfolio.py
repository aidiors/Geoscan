from dataclasses import dataclass

import pytest

from geoplan.domain.diagnostics import SafetyAuditCheck, SafetyAuditReport
from geoplan.domain.enums import (
    OptimizationCriterion,
    OptimizationMode,
    SafetyAuditStatus,
    SafetyCheckCode,
)
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.planning.schedule_portfolio import AuditedSchedulePortfolio
from geoplan.scheduling.models import (
    DispatchPriority,
    LayerPreference,
    MissionScheduleRequest,
    ScheduleTimeWindow,
    VerticalLayerOption,
)
from geoplan.scheduling.scheduler import (
    ConflictAwareMissionScheduler,
    ScheduleWindowUnavailableError,
)
from scenarios.planning import productive_survey_request


@dataclass
class RecordingAuditor:
    reject_ground_delay: bool = False
    reject_vertical: bool = False
    calls: int = 0

    def audit(self, request, segments, missions):
        self.calls += 1
        rejected = self.reject_ground_delay and any(m.start_offset_minutes > 0 for m in missions)
        rejected |= self.reject_vertical and any(
            m.vertical_deconfliction_offset_m for m in missions
        )
        status = SafetyAuditStatus.FAILED if rejected else SafetyAuditStatus.PASSED
        return SafetyAuditReport(
            status=status,
            audited_mission_count=len(missions),
            checks=[
                SafetyAuditCheck(
                    code=SafetyCheckCode.DRONE_SEPARATION, status=status, message="Test audit"
                )
            ],
        )


def crossing_request():
    def mission(drone_id, segment_id, coordinates):
        return Mission(
            drone_id=drone_id,
            segment_id=segment_id,
            route=[Coordinate(longitude=x, latitude=y) for x, y in coordinates],
            duration_minutes=10,
            distance_km=1,
            altitude_m=120,
            cruise_speed_kmh=60,
            effective_speed_kmh=60,
            estimated_battery_percent=20,
        )

    return MissionScheduleRequest(
        missions=[
            mission("alpha", "east", [(37.39, 55.7), (37.41, 55.7)]),
            mission("bravo", "north", [(37.4, 55.694), (37.4, 55.706)]),
        ],
        minimum_separation_m=50,
        turnaround_minutes=0,
        delay_step_seconds=1,
        vertical_layers_by_segment_id={
            "north": [
                VerticalLayerOption(
                    offset_m=0, extra_energy_equivalent_minutes=0, extra_battery_percent=0
                ),
                VerticalLayerOption(
                    offset_m=30,
                    climb_duration_minutes=0.05,
                    descent_duration_minutes=0.05,
                    extra_energy_equivalent_minutes=0.2,
                    extra_battery_percent=1,
                ),
            ]
        },
    )


@pytest.mark.parametrize(
    "criterion,deadline,expected_flight,preference",
    [
        (OptimizationCriterion.TOTAL_FLIGHT_TIME, None, 20, LayerPreference.MINIMUM_FLIGHT),
        (OptimizationCriterion.COMPLETION_TIME, None, 20.1, LayerPreference.EARLIEST_FINISH),
        (OptimizationCriterion.BALANCED, 10.15, 20.1, LayerPreference.EARLIEST_FINISH),
        (OptimizationCriterion.BALANCED, 20, 20, LayerPreference.MINIMUM_FLIGHT),
    ],
)
def test_final_objective_selects_ground_wait_or_climb(
    criterion, deadline, expected_flight, preference
):
    request = productive_survey_request(criterion=criterion, completion_deadline_minutes=deadline)
    schedule_request = crossing_request()
    original = schedule_request.model_dump_json()
    selection = AuditedSchedulePortfolio(
        ConflictAwareMissionScheduler(), RecordingAuditor()
    ).select(request, [], schedule_request)
    assert sum(m.duration_minutes for m in selection.schedule.missions) == pytest.approx(
        expected_flight
    )
    assert selection.layer_preference is preference
    if criterion is OptimizationCriterion.BALANCED:
        assert selection.schedule.makespan_minutes <= deadline
    assert schedule_request.model_dump_json() == original
    assert selection.reference_total_flight_minutes == pytest.approx(20.1)


def test_audit_rejection_retains_the_longer_flight_reference():
    auditor = RecordingAuditor(reject_ground_delay=True)
    selection = AuditedSchedulePortfolio(ConflictAwareMissionScheduler(), auditor).select(
        productive_survey_request(), [], crossing_request()
    )
    assert selection.audit.status is SafetyAuditStatus.PASSED
    assert selection.layer_preference is LayerPreference.EARLIEST_FINISH
    assert sum(m.duration_minutes for m in selection.schedule.missions) == pytest.approx(20.1)
    assert auditor.calls >= 2


def test_urgent_sortie_can_be_scheduled_before_long_sortie_on_same_drone():
    base = crossing_request()
    long, urgent = base.missions
    base = base.model_copy(
        update={
            "missions": [
                long.model_copy(update={"duration_minutes": 20}),
                urgent.model_copy(update={"drone_id": "alpha", "duration_minutes": 2}),
            ],
            "vertical_layers_by_segment_id": {},
            "time_windows_by_segment_id": {
                "north": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=3)]
            },
        }
    )
    with pytest.raises(ScheduleWindowUnavailableError):
        ConflictAwareMissionScheduler().schedule(base)
    selected = AuditedSchedulePortfolio(ConflictAwareMissionScheduler(), RecordingAuditor()).select(
        productive_survey_request(), [], base
    )
    assert selected.dispatch_priority is DispatchPriority.TIGHTEST_WINDOW
    assert selected.reference_audited is None
    assert [m.segment_id for m in selected.schedule.missions] == ["north", "east"]
    assert selected.schedule.makespan_minutes == 22
    assert selected.reference_makespan_minutes is None


def test_infeasible_vertical_option_does_not_discard_feasible_base_altitude():
    base = crossing_request()
    base = base.model_copy(
        update={
            "missions": [base.missions[1]],
            "vertical_layers_by_segment_id": {
                "north": list(reversed(base.vertical_layers_by_segment_id["north"]))
            },
            "time_windows_by_segment_id": {
                "north": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=10)]
            },
        }
    )
    schedule = ConflictAwareMissionScheduler().schedule(base)
    assert schedule.makespan_minutes == 10
    assert schedule.missions[0].vertical_deconfliction_offset_m == 0


def test_no_conflicts_skips_redundant_search_and_audit():
    base = crossing_request()
    base = base.model_copy(update={"missions": base.missions[:1]})
    auditor = RecordingAuditor()
    selected = AuditedSchedulePortfolio(ConflictAwareMissionScheduler(), auditor).select(
        productive_survey_request(), [], base
    )
    assert selected.variants_evaluated == auditor.calls == 1


def test_all_failed_policies_preserve_original_window_error():
    base = crossing_request()
    base = base.model_copy(
        update={
            "time_windows_by_segment_id": {
                "east": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=1)]
            },
        }
    )
    with pytest.raises(ScheduleWindowUnavailableError, match="east"):
        AuditedSchedulePortfolio(ConflictAwareMissionScheduler(), RecordingAuditor()).select(
            productive_survey_request(), [], base
        )


def test_failed_reference_audit_can_be_repaired_even_with_slower_completion():
    selected = AuditedSchedulePortfolio(
        ConflictAwareMissionScheduler(), RecordingAuditor(reject_vertical=True)
    ).select(
        productive_survey_request(criterion=OptimizationCriterion.COMPLETION_TIME),
        [],
        crossing_request(),
    )
    assert selected.audit.status is SafetyAuditStatus.PASSED
    assert selected.reference_makespan_minutes is not None
    assert selected.reference_audited is False
    assert selected.schedule.makespan_minutes > selected.reference_makespan_minutes
    assert selected.layer_preference is LayerPreference.MINIMUM_FLIGHT


def test_failed_alternative_keeps_successful_reference():
    class FailingAlternatives:
        def schedule(self, request):
            if request.dispatch_priority is not DispatchPriority.READY_FIRST:
                raise RuntimeError("Alternative search exhausted")
            return ConflictAwareMissionScheduler().schedule(request)

    selected = AuditedSchedulePortfolio(FailingAlternatives(), RecordingAuditor()).select(
        productive_survey_request(criterion=OptimizationCriterion.COMPLETION_TIME),
        [],
        crossing_request(),
    )
    assert selected.audit.status is SafetyAuditStatus.PASSED
    assert selected.dispatch_priority is DispatchPriority.READY_FIRST


def test_fast_mode_preserves_single_schedule_policy():
    selected = AuditedSchedulePortfolio(ConflictAwareMissionScheduler(), RecordingAuditor()).select(
        productive_survey_request(optimization_mode=OptimizationMode.FAST_HEURISTIC),
        [],
        crossing_request(),
    )
    assert selected.variants_evaluated == 1
    assert selected.layer_preference is LayerPreference.EARLIEST_FINISH
