from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from geoplan.domain.geo import Coordinate
from geoplan.navigation.models import RouteOptimizationRequest, RouteOptimizationResult
from geoplan.navigation.routing import PreparedSafeRoutes, RouteGeometryError, route_distance_km


class RouteOptimizer(Protocol):
    def optimize(self, request: RouteOptimizationRequest) -> RouteOptimizationResult: ...


class RouteOrientation(StrEnum):
    FORWARD = "forward"
    REVERSED = "reversed"


@dataclass(frozen=True)
class EvaluatedRoute:
    route: list[Coordinate]
    route_index: int
    orientation: RouteOrientation
    score: float


@dataclass(frozen=True)
class BidirectionalRouteOptimizer:
    def optimize(self, request: RouteOptimizationRequest) -> RouteOptimizationResult:
        routes = PreparedSafeRoutes.create(
            [
                request.departure,
                *request.landings,
                *(p for route in [request.route, *request.alternative_routes] for p in route),
            ],
            request.no_fly_zones,
        )
        evaluations: list[EvaluatedRoute] = []
        errors: list[RouteGeometryError] = []
        for route_index, source_route in enumerate([request.route, *request.alternative_routes]):
            for orientation in RouteOrientation:
                oriented_route = (
                    source_route
                    if orientation is RouteOrientation.FORWARD
                    else list(reversed(source_route))
                )
                try:
                    evaluation = _evaluate_route(
                        request,
                        oriented_route,
                        route_index,
                        orientation,
                        routes,
                    )
                except RouteGeometryError as error:
                    errors.append(error)
                    continue
                evaluations.append(evaluation)
        if not evaluations:
            if errors:
                raise errors[0]
            raise RouteGeometryError("Допустимые варианты маршрута отсутствуют.")
        selected = min(
            evaluations,
            key=lambda evaluation: (
                evaluation.score,
                evaluation.route_index,
                evaluation.orientation.value,
            ),
        )
        return RouteOptimizationResult(
            route=selected.route,
            reversed_for_shorter_transit=selected.orientation is RouteOrientation.REVERSED,
            selected_route_index=selected.route_index,
        )


def _evaluate_route(
    request: RouteOptimizationRequest,
    route: list[Coordinate],
    route_index: int,
    orientation: RouteOrientation,
    routes: PreparedSafeRoutes,
) -> EvaluatedRoute:
    safe_route = routes.build(route)
    transit = routes.build([request.departure, safe_route[0]])
    score = (
        route_distance_km(transit)
        + route_distance_km(safe_route)
        + _minimum_landing_distance(request, safe_route, routes)
    )
    return EvaluatedRoute(
        route=safe_route,
        route_index=route_index,
        orientation=orientation,
        score=score,
    )


def _minimum_landing_distance(
    request: RouteOptimizationRequest,
    route: list[Coordinate],
    routes: PreparedSafeRoutes,
) -> float:
    if not request.landings:
        return 0.0
    distances: list[float] = []
    for landing in request.landings:
        try:
            landing_route = routes.build([route[-1], landing])
        except RouteGeometryError:
            continue
        distances.append(route_distance_km(landing_route))
    if not distances:
        raise RouteGeometryError("Ни одна площадка посадки недоступна по безопасному маршруту.")
    return min(distances)
