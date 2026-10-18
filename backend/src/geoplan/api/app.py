from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from geoplan.api.routes import (
    create_router,
    planning_error_handler,
    request_validation_error_handler,
)
from geoplan.composition import AppDependencies
from geoplan.errors import PlanningError


def create_app(dependencies: AppDependencies, *, serve_frontend: bool = True) -> FastAPI:
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
            dependencies.freeform_optimizer,
            dependencies.assignment_alternative,
            dependencies.productive_route_pool,
        )
    )

    @application.get("/api-config.js", include_in_schema=False)
    def get_api_configuration() -> Response:
        return Response(
            content="window.GEOPLAN_API_BASE_URL = '';",
            media_type="application/javascript",
        )

    if serve_frontend:
        application.mount(
            "/",
            StaticFiles(directory=dependencies.frontend_directory, html=True),
            name="frontend",
        )
    return application
