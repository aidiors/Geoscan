"""Publish only independently audited incumbents under one request's objective."""

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter

from geoplan.planning.models import PlanCheckpoint, PlanRequest, PlanResponse, PlanSearchSummary
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
        )
        return plan.model_copy(update={"search": summary})
