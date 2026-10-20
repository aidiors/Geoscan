"""Application ports for audited coverage improvement; no concrete algorithms."""

from typing import Protocol

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.planning.models import PlanRequest, PlanResponse


class MissionCandidateBuilder(Protocol):
    def __call__(self, segment: WorkSegment, drone: DroneSpec, /) -> Mission | None: ...


class ScheduledPlanEvaluator(Protocol):
    def __call__(
        self, segments: list[WorkSegment], assignments: list[MissionAssignmentCandidate], /
    ) -> PlanResponse: ...


class MissionPhotography(Protocol):
    def prepare(self, request: CoveragePlanRequest, mission: Mission) -> Mission | None: ...


class MissionCostProtection(Protocol):
    def allows(self, missions: list[Mission], baseline: PlanResponse) -> bool: ...

    def allows_plan(self, candidate: PlanResponse, baseline: PlanResponse) -> bool: ...


class FreeformRouteOptimizer(Protocol):
    def improve(
        self,
        request: PlanRequest,
        source: list[WorkSegment],
        drones: list[DroneSpec],
        baseline: PlanResponse,
        build: MissionCandidateBuilder,
        evaluate: ScheduledPlanEvaluator,
        reference: PlanResponse | None = None,
    ) -> PlanResponse: ...
