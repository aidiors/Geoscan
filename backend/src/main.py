from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from src.adaptive_coverage import AdaptiveCoveragePlanner
from src.assignment import AssignmentOptimizer, HybridAssignmentOptimizer
from src.coverage import BoustrophedonCoveragePlanner, CoveragePlanner
from src.curvature import HybridAStarSweepTransitionPlanner, SweepTransitionPlanner
from src.dubins import ExactDubinsPlanner
from src.exporters import CanonicalMissionExportVerifier
from src.hybrid_astar import HybridAStarPlanner
from src.optimizer import BidirectionalRouteOptimizer, RouteOptimizer
from src.routes import (
    create_router,
    planning_error_handler,
    request_validation_error_handler,
)
from src.safety_audit import IndependentPlanSafetyAuditor, PlanSafetyAuditor
from src.scheduler import ConflictAwareMissionScheduler, MissionScheduler
from src.services import CruiseMissionDurationCalculator, MissionDurationCalculator, PlanningError
from src.terrain import (
    CompositeTerrainProvider,
    GeoTiffTerrainProvider,
    InverseDistanceTerrainProvider,
    TerrainFollowingProfilePlanner,
    VerticalProfilePlanner,
)


@dataclass(frozen=True)
class AppDependencies:
    duration_calculator: MissionDurationCalculator
    route_optimizer: RouteOptimizer
    coverage_planner: CoveragePlanner
    assignment_optimizer: AssignmentOptimizer
    mission_scheduler: MissionScheduler
    frontend_directory: Path
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


def create_app(dependencies: AppDependencies) -> FastAPI:
    application = FastAPI(title="GeoPlan Optimizer", version="1.0.0")
    application.add_exception_handler(PlanningError, planning_error_handler)
    application.add_exception_handler(RequestValidationError, request_validation_error_handler)
    application.include_router(
        create_router(
            dependencies.duration_calculator,
            dependencies.route_optimizer,
            dependencies.coverage_planner,
            dependencies.assignment_optimizer,
            dependencies.mission_scheduler,
            dependencies.radius_coverage_planner,
            dependencies.sweep_transition_planner,
            dependencies.safety_auditor,
            dependencies.vertical_profile_planner,
        )
    )

    @application.get("/api-config.js", include_in_schema=False)
    def get_api_configuration() -> Response:
        return Response(
            content="window.GEOPLAN_API_BASE_URL = '';",
            media_type="application/javascript",
        )

    application.mount(
        "/",
        StaticFiles(directory=dependencies.frontend_directory, html=True),
        name="frontend",
    )
    return application


def create_default_dependencies() -> AppDependencies:
    frontend_directory = Path(__file__).resolve().parents[2] / "dist"
    return AppDependencies(
        duration_calculator=CruiseMissionDurationCalculator(),
        route_optimizer=BidirectionalRouteOptimizer(),
        coverage_planner=AdaptiveCoveragePlanner(),
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


app = create_app(create_default_dependencies())
