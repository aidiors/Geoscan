from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.assignment import AssignmentOptimizer
from src.coverage import CoveragePlanner
from src.curvature import SweepTransitionPlanner
from src.exporters import (
    build_geojson_export,
    build_kml_export,
    build_mission_planner_export,
    build_qgroundcontrol_export,
)
from src.imports import import_geojson, import_kml
from src.optimizer import RouteOptimizer
from src.safety_audit import PlanSafetyAuditor
from src.scheduler import MissionScheduler
from src.schemas import (
    ErrorCode,
    ErrorResponse,
    ExportRequest,
    GeoJsonFeatureCollection,
    GeoJsonImportRequest,
    HealthReport,
    HealthStatus,
    ImportResponse,
    KmlExport,
    KmlImportRequest,
    MissionPlannerExport,
    PlanRequest,
    PlanResponse,
    QGroundControlExport,
    TurnRadiusAnalysisRequest,
    TurnRadiusAnalysisResult,
)
from src.services import MissionDurationCalculator, PlanningError, build_plan
from src.terrain import VerticalProfilePlanner
from src.turn_radius_analysis import analyze_turn_radii


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
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


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
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthReport)
    def get_health() -> HealthReport:
        return health_report()

    @router.post(
        "/v1/plans",
        response_model=PlanResponse,
        responses={422: {"model": ErrorResponse}},
    )
    def post_plan(request: PlanRequest) -> PlanResponse:
        return create_plan(
            request,
            duration_calculator,
            route_optimizer,
            coverage_planner,
            assignment_optimizer,
            mission_scheduler,
            sweep_transition_planner,
            safety_auditor,
            vertical_profile_planner,
        )

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
        return create_qgroundcontrol_export(request)

    @router.post(
        "/v1/exports/mission-planner",
        response_model=MissionPlannerExport,
        responses={422: {"model": ErrorResponse}},
    )
    def post_mission_planner_export(request: ExportRequest) -> MissionPlannerExport:
        return create_mission_planner_export(request)

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
