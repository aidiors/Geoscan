"""Compare bounded scheduling policies using the final objective and independent audit."""

from dataclasses import dataclass
from math import fsum

from geoplan.coverage.models import WorkSegment
from geoplan.domain.diagnostics import SafetyAuditReport
from geoplan.domain.enums import OptimizationMode, SafetyAuditStatus
from geoplan.planning.models import PlanRequest
from geoplan.routing.model import routing_objective
from geoplan.safety.audit import PlanSafetyAuditor
from geoplan.scheduling.models import (
    DispatchPriority,
    LayerPreference,
    MissionScheduleRequest,
    MissionScheduleResult,
)
from geoplan.scheduling.scheduler import MissionScheduler


@dataclass(frozen=True)
class AuditedScheduleSelection:
    schedule: MissionScheduleResult
    audit: SafetyAuditReport
    dispatch_priority: DispatchPriority
    layer_preference: LayerPreference
    variants_evaluated: int
    reference_makespan_minutes: float | None
    reference_total_flight_minutes: float | None
    reference_audited: bool | None


@dataclass(frozen=True)
class AuditedSchedulePortfolio:
    scheduler: MissionScheduler
    auditor: PlanSafetyAuditor

    def select(
        self,
        request: PlanRequest,
        segments: list[WorkSegment],
        schedule_request: MissionScheduleRequest,
    ) -> AuditedScheduleSelection:
        best: AuditedScheduleSelection | None = None
        first_error: RuntimeError | None = None
        evaluated = 0
        seen: set[str] = set()
        reference: MissionScheduleResult | None = None
        reference_audited: bool | None = None
        for policy in self._policies(request, schedule_request):
            evaluated += 1
            try:
                candidate = self.scheduler.schedule(policy)
            except RuntimeError as error:
                first_error = first_error or error
                continue
            if evaluated == 1:
                reference = candidate
            fingerprint = candidate.model_dump_json(exclude={"resolved_conflicts"})
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            audit = self.auditor.audit(request, segments, candidate.missions)
            passed = audit.status is SafetyAuditStatus.PASSED
            if evaluated == 1:
                reference_audited = passed
            if best is None or (
                passed
                and (
                    best.audit.status is not SafetyAuditStatus.PASSED
                    or self._objective(request, candidate) < self._objective(request, best.schedule)
                )
            ):
                best = AuditedScheduleSelection(
                    candidate,
                    audit,
                    policy.dispatch_priority,
                    policy.layer_preference,
                    evaluated,
                    reference.makespan_minutes if reference else None,
                    fsum(m.duration_minutes for m in reference.missions) if reference else None,
                    reference_audited,
                )
            if (
                evaluated == 1
                and passed
                and candidate.resolved_conflict_count == 0
                and not schedule_request.time_windows_by_segment_id
                and not any(m.vertical_deconfliction_offset_m for m in candidate.missions)
            ):
                break
        if best is None:
            assert first_error is not None
            raise first_error
        return AuditedScheduleSelection(
            best.schedule,
            best.audit,
            best.dispatch_priority,
            best.layer_preference,
            evaluated,
            best.reference_makespan_minutes,
            best.reference_total_flight_minutes,
            best.reference_audited,
        )

    @staticmethod
    def _policies(
        request: PlanRequest, base: MissionScheduleRequest
    ) -> list[MissionScheduleRequest]:
        policies = [base]
        if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
            return policies
        layers = [LayerPreference.EARLIEST_FINISH]
        if any(
            option.climb_duration_minutes + option.descent_duration_minutes > 0
            for options in base.vertical_layers_by_segment_id.values()
            for option in options
        ):
            layers.append(LayerPreference.MINIMUM_FLIGHT)
            policies.append(base.model_copy(update={"layer_preference": layers[-1]}))
        priorities = [DispatchPriority.LONGEST_REMAINING]
        if base.time_windows_by_segment_id:
            priorities.append(DispatchPriority.TIGHTEST_WINDOW)
        for priority in priorities:
            for layer in layers:
                policies.append(
                    base.model_copy(
                        update={"dispatch_priority": priority, "layer_preference": layer}
                    )
                )
        return policies

    @staticmethod
    def _objective(request: PlanRequest, schedule: MissionScheduleResult) -> tuple[float, ...]:
        return routing_objective(
            request.criterion,
            request.completion_deadline_minutes,
            fsum(m.duration_minutes for m in schedule.missions),
            fsum(m.transit_duration_minutes or 0 for m in schedule.missions),
            schedule.makespan_minutes,
            len({m.drone_id for m in schedule.missions}),
        )
