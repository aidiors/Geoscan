from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from geoplan.api.models import ErrorResponse, HealthReport
from geoplan.api.planning_transport import PlanningTransport
from geoplan.assignment.alternatives import AssignmentAlternativeProvider
from geoplan.assignment.solver import AssignmentOptimizer
from geoplan.budget import planning_budget
from geoplan.coverage.sweep import CoveragePlanner
from geoplan.domain.enums import ErrorCode, HealthStatus, OptimizationMode
from geoplan.errors import PlanningError
from geoplan.flight.duration import MissionDurationCalculator
from geoplan.flight.terrain import VerticalProfilePlanner
from geoplan.geodata.exporters import (
    build_geojson_export,
    build_kml_export,
    build_mission_planner_export,
    build_qgroundcontrol_export,
)
from geoplan.geodata.flight_package import FlightPackageBuilder
from geoplan.geodata.importers import import_geojson, import_kml
from geoplan.geodata.models import (
    ExportRequest,
    FlightPackageRequest,
    GeoJsonFeatureCollection,
    GeoJsonImportRequest,
    ImportResponse,
    KmlExport,
    KmlImportRequest,
    MissionPlannerExport,
    QGroundControlExport,
)
from geoplan.geodata.release import PlanReleaseAuthority
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.optimizer import RouteOptimizer
from geoplan.planning.advisor import AdvisorRequest, AdvisorResponse, FeasibilityAdvisor
from geoplan.planning.cross_area import CrossAreaOptimizer
from geoplan.planning.engine import build_plan
from geoplan.planning.freeform_ports import FreeformRouteOptimizer
from geoplan.planning.models import (
    PlanRequest,
    PlanResponse,
    TurnRadiusAnalysisRequest,
    TurnRadiusAnalysisResult,
)
from geoplan.planning.progress import PlanObserver
from geoplan.planning.quality_models import SurveyQualityReport, SurveyQualityRequest
from geoplan.planning.service import AuditedPlanningService
from geoplan.planning.survey_quality import SurveyQualityAnalyzer
from geoplan.planning.turn_radius import analyze_turn_radii
from geoplan.routing.pool import RoutePoolGenerator
from geoplan.safety.audit import PlanSafetyAuditor
from geoplan.scheduling.scheduler import MissionScheduler


def create_plan(
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    coverage_planner: CoveragePlanner,
    assignment_optimizer: AssignmentOptimizer,
    mission_scheduler: MissionScheduler,
    sweep_transition_planner: SweepTransitionPlanner,
    safety_auditor: PlanSafetyAuditor,
    vertical_profile_planner: VerticalProfilePlanner,
    freeform_optimizer: FreeformRouteOptimizer,
    assignment_alternative: AssignmentAlternativeProvider | None = None,
    productive_route_pool: RoutePoolGenerator | None = None,
    progress: PlanObserver | None = None,
    cross_area_optimizer: CrossAreaOptimizer | None = None,
) -> PlanResponse:
    return build_plan(
        request,
        duration_calculator,
        route_optimizer,
        coverage_planner,
        assignment_optimizer,
        mission_scheduler,
        sweep_transition_planner,
        safety_auditor,
        vertical_profile_planner,
        freeform_optimizer,
        assignment_alternative,
        productive_route_pool,
        progress,
        cross_area_optimizer,
    )


def health_report() -> HealthReport:
    return HealthReport(status=HealthStatus.HEALTHY, message="Сервис планирования доступен.")


def create_geojson_export(request: ExportRequest) -> GeoJsonFeatureCollection:
    return build_geojson_export(request)


def create_kml_export(request: ExportRequest) -> KmlExport:
    return build_kml_export(request)


def create_qgroundcontrol_export(request: ExportRequest) -> QGroundControlExport:
    return build_qgroundcontrol_export(request)


def create_mission_planner_export(request: ExportRequest) -> MissionPlannerExport:
    return build_mission_planner_export(request)


def create_geojson_import(request: GeoJsonImportRequest) -> ImportResponse:
    return import_geojson(request)


def create_kml_import(request: KmlImportRequest) -> ImportResponse:
    return import_kml(request)


def planning_error_handler(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, PlanningError):
        raise RuntimeError("Обработчик получил неподдерживаемую ошибку.") from error
    response = ErrorResponse(
        code=error.code,
        message=error.message,
        recommendations=error.recommendations,
    )
    return JSONResponse(
        status_code=503 if error.code is ErrorCode.PLANNING_BUSY else 422,
        content=response.model_dump(mode="json"),
    )


def request_validation_error_handler(
    _: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise RuntimeError("Обработчик получил неподдерживаемую ошибку.") from error
    response = ErrorResponse(
        code=ErrorCode.INVALID_REQUEST,
        message="Запрос не соответствует контракту API.",
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


def create_router(
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    coverage_planner: CoveragePlanner,
    assignment_optimizer: AssignmentOptimizer,
    mission_scheduler: MissionScheduler,
    radius_coverage_planner: CoveragePlanner,
    sweep_transition_planner: SweepTransitionPlanner,
    safety_auditor: PlanSafetyAuditor,
    vertical_profile_planner: VerticalProfilePlanner,
    freeform_optimizer: FreeformRouteOptimizer,
    assignment_alternative: AssignmentAlternativeProvider | None = None,
    productive_route_pool: RoutePoolGenerator | None = None,
    alternative_coverage_planner: CoveragePlanner | None = None,
    cross_area_optimizer: CrossAreaOptimizer | None = None,
) -> APIRouter:
    router = APIRouter()

    def run(
        request: PlanRequest, observer: PlanObserver, planner: CoveragePlanner = coverage_planner
    ) -> PlanResponse:
        return create_plan(
            request,
            duration_calculator,
            route_optimizer,
            planner,
            assignment_optimizer,
            mission_scheduler,
            sweep_transition_planner,
            safety_auditor,
            vertical_profile_planner,
            freeform_optimizer,
            assignment_alternative,
            productive_route_pool,
            observer,
            cross_area_optimizer,
        )

    alternative = None
    if alternative_coverage_planner is not None:

        def alternative(request: PlanRequest, observer: PlanObserver) -> PlanResponse:
            assert alternative_coverage_planner is not None
            return run(request, observer, alternative_coverage_planner)

    def initial(request: PlanRequest, observer: PlanObserver) -> PlanResponse:
        seed = request.model_copy(
            update={
                "optimization_mode": OptimizationMode.FAST_HEURISTIC,
                "productive_routing": False,
            }
        )
        return run(seed, observer)

    authority = PlanReleaseAuthority.from_environment()
    transport = PlanningTransport(
        AuditedPlanningService(run, alternative, initial), release_authority=authority
    )

    @router.get("/health", response_model=HealthReport)
    def get_health() -> HealthReport:
        return health_report()

    @router.post(
        "/v1/plans",
        response_model=PlanResponse,
        responses={
            200: {
                "description": "JSON plan, or NDJSON events when Accept: application/x-ndjson.",
                "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            },
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def post_plan(
        request: PlanRequest, http_request: Request
    ) -> PlanResponse | StreamingResponse | JSONResponse:
        if "application/x-ndjson" in http_request.headers.get("accept", ""):
            return transport.stream(request)
        return transport.plan(request)

    @router.post(
        "/v1/feasibility-advice",
        response_model=AdvisorResponse,
        responses={422: {"model": ErrorResponse}},
    )
    def post_feasibility_advice(query: AdvisorRequest) -> AdvisorResponse:
        with planning_budget(query.request.planning_time_limit_seconds):
            return FeasibilityAdvisor(transport.plan).advise(query)

    @router.post(
        "/v1/survey-quality",
        response_model=SurveyQualityReport,
        responses={422: {"model": ErrorResponse}},
    )
    def post_survey_quality(query: SurveyQualityRequest) -> SurveyQualityReport:
        return SurveyQualityAnalyzer().analyze(query.request, query.plan)

    @router.post(
        "/v1/turn-radius-analysis",
        response_model=TurnRadiusAnalysisResult,
        responses={422: {"model": ErrorResponse}},
    )
    def post_turn_radius_analysis(
        request: TurnRadiusAnalysisRequest,
    ) -> TurnRadiusAnalysisResult:
        return analyze_turn_radii(request, radius_coverage_planner, sweep_transition_planner)

    @router.post(
        "/v1/exports/geojson",
        response_model=GeoJsonFeatureCollection,
        responses={422: {"model": ErrorResponse}},
    )
    def post_geojson_export(request: ExportRequest) -> GeoJsonFeatureCollection:
        return create_geojson_export(request)

    @router.post(
        "/v1/exports/kml",
        response_model=KmlExport,
        responses={422: {"model": ErrorResponse}},
    )
    def post_kml_export(request: ExportRequest) -> KmlExport:
        return create_kml_export(request)

    @router.post(
        "/v1/exports/qgroundcontrol",
        response_model=QGroundControlExport,
        responses={422: {"model": ErrorResponse}},
    )
    def post_qgroundcontrol_export(request: ExportRequest) -> QGroundControlExport:
        authority.verify(request.plan)
        return create_qgroundcontrol_export(request)

    @router.post(
        "/v1/exports/mission-planner",
        response_model=MissionPlannerExport,
        responses={422: {"model": ErrorResponse}},
    )
    def post_mission_planner_export(request: ExportRequest) -> MissionPlannerExport:
        authority.verify(request.plan)
        return create_mission_planner_export(request)

    @router.post("/v1/exports/flight-package", responses={422: {"model": ErrorResponse}})
    def post_flight_package(request: FlightPackageRequest) -> Response:
        authority.verify(request.plan)
        content = FlightPackageBuilder().build(request)
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="flight-package.zip"',
                "Cache-Control": "no-store",
            },
        )

    @router.post(
        "/v1/imports/geojson",
        response_model=ImportResponse,
        responses={422: {"model": ErrorResponse}},
    )
    def post_geojson_import(request: GeoJsonImportRequest) -> ImportResponse:
        return create_geojson_import(request)

    @router.post(
        "/v1/imports/kml",
        response_model=ImportResponse,
        responses={422: {"model": ErrorResponse}},
    )
    def post_kml_import(request: KmlImportRequest) -> ImportResponse:
        return create_kml_import(request)

    return router
