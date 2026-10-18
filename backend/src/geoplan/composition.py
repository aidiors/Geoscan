from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from geoplan.assignment.alternatives import (
    AssignmentAlternativeProvider,
    CpSatAssignmentAlternative,
)
from geoplan.assignment.solver import AssignmentOptimizer, HybridAssignmentOptimizer
from geoplan.coverage.adaptive import AdaptiveCoveragePlanner
from geoplan.coverage.production import ProductionCoveragePlanner
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner, CoveragePlanner
from geoplan.flight.duration import CruiseMissionDurationCalculator, MissionDurationCalculator
from geoplan.flight.terrain import (
    CompositeTerrainProvider,
    GeoTiffTerrainProvider,
    InverseDistanceTerrainProvider,
    TerrainFollowingProfilePlanner,
    VerticalProfilePlanner,
)
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.navigation.curvature import HybridAStarSweepTransitionPlanner, SweepTransitionPlanner
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.hybrid_astar import HybridAStarPlanner
from geoplan.navigation.optimizer import BidirectionalRouteOptimizer, RouteOptimizer
from geoplan.planning.cross_area import AuditedCrossAreaPlanner, CrossAreaOptimizer
from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
from geoplan.planning.freeform_ports import FreeformRouteOptimizer
from geoplan.routing.pool import ProductiveRoutePool, RoutePoolGenerator
from geoplan.safety.audit import IndependentPlanSafetyAuditor, PlanSafetyAuditor
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler, MissionScheduler


@dataclass(frozen=True)
class AppDependencies:
    duration_calculator: MissionDurationCalculator
    route_optimizer: RouteOptimizer
    coverage_planner: CoveragePlanner
    assignment_optimizer: AssignmentOptimizer
    mission_scheduler: MissionScheduler
    frontend_directory: Path
    cross_area_optimizer: CrossAreaOptimizer = field(default_factory=AuditedCrossAreaPlanner)
    alternative_coverage_planner: CoveragePlanner | None = field(
        default_factory=AdaptiveCoveragePlanner
    )
    productive_route_pool: RoutePoolGenerator = field(default_factory=ProductiveRoutePool)
    assignment_alternative: AssignmentAlternativeProvider | None = field(
        default_factory=CpSatAssignmentAlternative
    )
    freeform_optimizer: FreeformRouteOptimizer = field(
        default_factory=AuditedFreeformRouteOptimizer
    )
    vertical_profile_planner: VerticalProfilePlanner = field(
        default_factory=lambda: TerrainFollowingProfilePlanner(
            terrain_provider=CompositeTerrainProvider(
                inline_provider=InverseDistanceTerrainProvider(),
                geotiff_provider=GeoTiffTerrainProvider(),
            )
        )
    )
    safety_auditor: PlanSafetyAuditor = field(
        default_factory=lambda: IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier())
    )
    radius_coverage_planner: CoveragePlanner = field(default_factory=BoustrophedonCoveragePlanner)
    sweep_transition_planner: SweepTransitionPlanner = field(
        default_factory=lambda: HybridAStarSweepTransitionPlanner(
            planner=HybridAStarPlanner(dubins_planner=ExactDubinsPlanner()),
            primitive_length_m=15,
            grid_resolution_m=8,
            heading_bin_degrees=15,
            analytic_expansion_distance_m=180,
            maximum_expansions=20_000,
        )
    )


def create_default_dependencies() -> AppDependencies:
    frontend_directory = Path(__file__).resolve().parents[3] / "dist"
    return AppDependencies(
        duration_calculator=CruiseMissionDurationCalculator(),
        route_optimizer=BidirectionalRouteOptimizer(),
        coverage_planner=ProductionCoveragePlanner(),
        assignment_optimizer=HybridAssignmentOptimizer(),
        mission_scheduler=ConflictAwareMissionScheduler(),
        safety_auditor=IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()),
        frontend_directory=frontend_directory,
        vertical_profile_planner=TerrainFollowingProfilePlanner(
            terrain_provider=CompositeTerrainProvider(
                inline_provider=InverseDistanceTerrainProvider(),
                geotiff_provider=GeoTiffTerrainProvider(),
            )
        ),
    )
