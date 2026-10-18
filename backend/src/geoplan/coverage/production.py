"""One coverage policy for the service; expensive searches do not scale per area."""

from dataclasses import dataclass, field

from geoplan.coverage.adaptive import AdaptiveCoveragePlanner
from geoplan.coverage.models import CoveragePlanRequest, CoveragePlanResult
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.domain.enums import CoverageAlgorithm, OptimizationMode, SweepAngleAlgorithm


@dataclass(frozen=True)
class ProductionCoveragePlanner:
    single: AdaptiveCoveragePlanner = field(default_factory=AdaptiveCoveragePlanner)
    batch: BoustrophedonCoveragePlanner = field(
        default_factory=lambda: BoustrophedonCoveragePlanner(SweepAngleAlgorithm.BOUNDED_PORTFOLIO)
    )
    fast: BoustrophedonCoveragePlanner = field(
        default_factory=lambda: BoustrophedonCoveragePlanner(
            SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE
        )
    )

    @property
    def coverage_algorithm(self) -> CoverageAlgorithm:
        return CoverageAlgorithm.AUTOMATIC_PORTFOLIO

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult:
        if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
            return self.fast.plan(request)
        if (
            request.planning_area_count > 1
            or len(request.area.coordinates) > 128
            or len(request.area.interior_rings) > 4
        ):
            return self.batch.plan(request)
        return self.single.plan(request)
