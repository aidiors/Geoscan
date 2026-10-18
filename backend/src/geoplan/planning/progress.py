"""Publish only independently audited incumbents under one request's objective."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter

from geoplan.budget import PlanningCancelled, PlanningTimeExceeded
from geoplan.errors import PlanningError
from geoplan.planning.models import (
    PlanCheckpoint,
    PlanPipelineReport,
    PlanRequest,
    PlanResponse,
    PlanSearchSummary,
)
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective

PlanObserver = Callable[[str, PlanResponse], None]


@dataclass
class AuditedPlanProgress:
    request: PlanRequest
    observer: PlanObserver | None = None
    started: float = field(default_factory=perf_counter)
    best: PlanResponse | None = None
    checkpoints: list[PlanCheckpoint] = field(default_factory=list)
    pipelines_started: int = 0
    pipelines: list[PlanPipelineReport] = field(default_factory=list)

    @contextmanager
    def pipeline(self, name: str) -> Iterator[PlanObserver]:
        """Record observed outcomes, including stages that produce no usable plan."""
        index = len(self.pipelines)
        self.pipelines.append(PlanPipelineReport(name=name))
        self.pipelines_started += 1
        started = perf_counter()

        def observe(stage: str, plan: PlanResponse) -> None:
            report = self.pipelines[index]
            previous = scheduled_plan_objective(self.request, self.best) if self.best else None
            audited = plan_is_audited(plan)
            improved = (
                audited
                and (previous is None or scheduled_plan_objective(self.request, plan) < previous)
                and (
                    self.request.maximum_active_drones is None
                    or plan.metrics.active_drone_count <= self.request.maximum_active_drones
                )
            )
            failed = set(report.failed_check_codes)
            if plan.safety_audit is not None:
                failed.update(
                    c.code.value for c in plan.safety_audit.checks if c.status == "failed"
                )
            self.pipelines[index] = report.model_copy(
                update={
                    "candidate_events": report.candidate_events + 1,
                    "audited_events": report.audited_events + int(audited),
                    "improvements": report.improvements + int(improved),
                    "failed_check_codes": sorted(failed)[:32],
                    "elapsed_seconds": perf_counter() - started,
                }
            )
            self.offer(stage if name == "primary" else f"{name}:{stage}", plan)

        status, error_code = "completed", None
        try:
            yield observe
        except PlanningError as error:
            status = (
                "cancelled"
                if isinstance(error, PlanningCancelled)
                else ("time_limit" if isinstance(error, PlanningTimeExceeded) else "error")
            )
            error_code = error.code.value
            raise
        except Exception:
            status = "error"
            raise
        else:
            report = self.pipelines[index]
            status = (
                "improved"
                if report.improvements
                else ("retained" if report.audited_events else "rejected")
            )
        finally:
            self.pipelines[index] = self.pipelines[index].model_copy(
                update={
                    "status": status,
                    "error_code": error_code,
                    "elapsed_seconds": perf_counter() - started,
                }
            )

    def offer(self, stage: str, plan: PlanResponse) -> None:
        if not plan_is_audited(plan) or (
            self.request.maximum_active_drones is not None
            and plan.metrics.active_drone_count > self.request.maximum_active_drones
        ):
            return
        score = scheduled_plan_objective(self.request, plan)
        previous = scheduled_plan_objective(self.request, self.best) if self.best else None
        if previous is not None and score > previous:
            return
        self.best = plan
        if previous is not None and score == previous:
            return  # Keep the richer final metadata without a duplicate improvement.
        point = PlanCheckpoint(
            stage=stage,
            elapsed_seconds=perf_counter() - self.started,
            completion_minutes=plan.metrics.completion_minutes,
            total_flight_minutes=plan.metrics.total_flight_minutes,
            sortie_count=plan.metrics.sortie_count,
            objective=list(score),
        )
        if len(self.checkpoints) >= 64:
            self.checkpoints.pop(1)  # Retain the first result and the latest improvements.
        self.checkpoints.append(point)
        if self.observer is not None:
            self.observer(stage, self.snapshot(plan, "searching"))

    def snapshot(self, plan: PlanResponse, status: str) -> PlanResponse:
        summary = PlanSearchSummary(
            pipelines_started=self.pipelines_started,
            status=status,
            elapsed_seconds=perf_counter() - self.started,
            time_limit_seconds=self.request.planning_time_limit_seconds,
            request_fingerprint=sha256(self.request.model_dump_json().encode()).hexdigest(),
            checkpoints=list(self.checkpoints),
            pipelines=list(self.pipelines),
        )
        return plan.model_copy(update={"search": summary})
