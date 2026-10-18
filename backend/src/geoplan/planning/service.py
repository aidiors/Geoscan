"""A bounded portfolio retains the best audited result across entire pipelines."""

from dataclasses import dataclass
from typing import Protocol

from geoplan.budget import (
    PlanningCancelled,
    PlanningTimeExceeded,
    check_planning_budget,
    improvement_allowed,
    planning_budget,
)
from geoplan.domain.enums import OptimizationMode, SweepDirectionMode
from geoplan.errors import PlanningError
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.progress import AuditedPlanProgress, PlanObserver


class PlanRunner(Protocol):
    def __call__(self, request: PlanRequest, observer: PlanObserver) -> PlanResponse: ...


@dataclass(frozen=True)
class AuditedPlanningService:
    primary: PlanRunner
    alternative: PlanRunner | None = None
    initial: PlanRunner | None = None
    alternative_seconds: float = 90

    def plan(self, request: PlanRequest, observer: PlanObserver | None = None) -> PlanResponse:
        progress = AuditedPlanProgress(request, observer)
        status = "completed"
        result = None
        with planning_budget(request.planning_time_limit_seconds):
            try:
                if (
                    self.initial is not None
                    and request.optimization_mode is OptimizationMode.OPTIMIZED
                ):
                    try:
                        with progress.pipeline("initial") as observe:
                            initial = self.initial(request, observe)
                            observe("result", initial)
                            check_planning_budget()
                    except PlanningTimeExceeded, PlanningCancelled:
                        raise
                    except PlanningError:
                        pass
                with progress.pipeline("primary") as observe:
                    result = self.primary(request, observe)
                    observe("result", result)
                    check_planning_budget()
                if (
                    self.alternative is not None
                    and self._try_alternative(request)
                    and improvement_allowed(20)
                ):
                    try:
                        with (
                            progress.pipeline("adaptive") as observe,
                            planning_budget(self.alternative_seconds),
                        ):
                            alternative = self.alternative(request, observe)
                            observe("result", alternative)
                            check_planning_budget()
                    except PlanningTimeExceeded:
                        status = "stage_time_limit"
                    except PlanningCancelled:
                        raise
                    except PlanningError:
                        pass  # A failed alternative does not invalidate the incumbent.
            except PlanningTimeExceeded:
                if progress.best is None:
                    raise
                status = "time_limit"
            except PlanningCancelled:
                if progress.best is None:
                    raise
                status = "cancelled"
            except PlanningError:
                if progress.best is None:
                    raise
                status = "retained_after_error"
        selected = progress.best or result
        assert selected is not None
        return progress.snapshot(selected, status)

    def _try_alternative(self, request: PlanRequest) -> bool:
        return (
            request.productive_routing
            and request.optimization_mode is OptimizationMode.OPTIMIZED
            and 2 <= len(request.coverage_areas) <= 8
            and not request.segments
            and sum(len(a.coordinates) for a in request.coverage_areas) <= 2048
            and request.survey_parameters is not None
            and request.survey_parameters.direction_mode is SweepDirectionMode.AUTOMATIC
        )
