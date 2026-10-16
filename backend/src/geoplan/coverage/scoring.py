from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from geoplan.coverage.models import CoveragePlanRequest
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.geo import Coordinate
from geoplan.flight.performance import WindPerformanceError, route_duration_minutes
from geoplan.navigation.routing import (
    RouteGeometryError,
    build_safe_route,
    route_bearing_degrees,
    route_distance_km,
    route_is_inside_coverage_area,
)

ROUTE_CLEARANCE_M = 0.5


@dataclass(frozen=True, order=True)
class CoverageRouteScore:
    objective_cost: float
    distance_km: float
    turn_count: int


def _route_choice_score(
    route: list[Coordinate],
    request: CoveragePlanRequest,
    exclusions: list[NoFlyZone],
    survey_speed_limit_kmh: float,
) -> CoverageRouteScore | None:
    safe_route = _safe_route_or_none(route, exclusions)
    if safe_route is None:
        return None
    if not route_is_inside_coverage_area(safe_route, request.area):
        return None
    departure_score = _best_connection_score(
        [[site, safe_route[0]] for site in request.departure_candidates],
        request,
        exclusions,
    )
    if departure_score is None:
        return None
    landing_score = _best_connection_score(
        [[safe_route[-1], site] for site in request.landing_candidates],
        request,
        exclusions,
    )
    if landing_score is None:
        return None
    survey_score = _route_performance_score(
        safe_route,
        request,
        survey_speed_limit_kmh,
        _turn_count(safe_route),
    )
    if survey_score is None:
        return None
    return CoverageRouteScore(
        departure_score.objective_cost + survey_score.objective_cost + landing_score.objective_cost,
        departure_score.distance_km + survey_score.distance_km + landing_score.distance_km,
        survey_score.turn_count,
    )


def _safe_route_or_none(
    route: list[Coordinate],
    exclusions: list[NoFlyZone],
) -> list[Coordinate] | None:
    try:
        return build_safe_route(route, exclusions)
    except RouteGeometryError:
        return None


def _best_connection_score(
    routes: list[list[Coordinate]],
    request: CoveragePlanRequest,
    exclusions: list[NoFlyZone],
) -> CoverageRouteScore | None:
    if not routes:
        return CoverageRouteScore(0, 0, 0)
    scores: list[CoverageRouteScore] = []
    for route in routes:
        safe_route = _safe_route_or_none(route, exclusions)
        if safe_route is None:
            continue
        score = _route_performance_score(safe_route, request, None, 0)
        if score is not None:
            scores.append(score)
    return min(scores) if scores else None


def _route_performance_score(
    route: list[Coordinate],
    request: CoveragePlanRequest,
    ground_speed_limit_kmh: float | None,
    turn_count: int,
) -> CoverageRouteScore | None:
    distance_km = route_distance_km(route)
    if not _uses_wind_adjusted_cost(request):
        return CoverageRouteScore(distance_km, distance_km, turn_count)
    reference_airspeed_kmh = request.reference_airspeed_kmh
    if reference_airspeed_kmh is None:
        return None
    try:
        duration_minutes = route_duration_minutes(
            route,
            distance_km,
            reference_airspeed_kmh,
            request.wind,
            ground_speed_limit_kmh,
        )
    except WindPerformanceError:
        return None
    return CoverageRouteScore(duration_minutes, distance_km, turn_count)


def _uses_wind_adjusted_cost(request: CoveragePlanRequest) -> bool:
    return (
        request.wind is not None
        and request.wind.speed_mps > 0
        and request.reference_airspeed_kmh is not None
    )


def _turn_count(route: list[Coordinate]) -> int:
    headings = [route_bearing_degrees(start, end) for start, end in pairwise(route) if start != end]
    return sum(
        min(abs(end - start), 360 - abs(end - start)) > 5 for start, end in pairwise(headings)
    )


def _scoring_exclusions(request: CoveragePlanRequest) -> list[NoFlyZone]:
    holes = [
        NoFlyZone(
            zone_id=f"coverage-hole-{index + 1}",
            coordinates=ring,
            safety_margin_m=ROUTE_CLEARANCE_M,
        )
        for index, ring in enumerate(request.area.interior_rings)
        if len(ring) >= 3
    ]
    return [*request.exclusions, *holes]
