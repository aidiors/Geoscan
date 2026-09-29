from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import ceil, cos, hypot, pi, radians, sin

from shapely.geometry import GeometryCollection, LineString, Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from src.dubins import DubinsPlanner
from src.geometry import LocalProjection, polygon_from_coordinates, projection_for_coordinates
from src.routing import route_distance_km
from src.schemas import (
    Coordinate,
    DubinsPathRequest,
    DubinsPathResult,
    DubinsPose,
    HybridAStarPathRequest,
    HybridAStarPathResult,
)


@dataclass(frozen=True)
class SearchPose:
    x: float
    y: float
    heading_radians: float


@dataclass(frozen=True)
class SearchStateKey:
    x_index: int
    y_index: int
    heading_index: int


@dataclass(frozen=True)
class SearchRecord:
    pose: SearchPose
    distance_m: float
    parent: SearchStateKey | None
    primitive_route: list[Coordinate]


@dataclass(frozen=True)
class SearchContext:
    projection: LocalProjection
    blocked_geometry: BaseGeometry
    bounds: tuple[float, float, float, float]
    end_x: float
    end_y: float
    heading_bin_count: int


@dataclass(frozen=True)
class HybridAStarPlanner:
    dubins_planner: DubinsPlanner

    def plan(self, request: HybridAStarPathRequest) -> HybridAStarPathResult:
        context = _build_context(request)
        initial = _shortest_clear_dubins(
            request.start,
            request.end,
            request,
            context,
            self.dubins_planner,
        )
        if initial is not None:
            return _result(initial.route, 0)
        start_x, start_y = context.projection.project(request.start.coordinate)
        start_pose = SearchPose(
            x=start_x,
            y=start_y,
            heading_radians=_compass_to_radians(request.start.heading_degrees),
        )
        start_key = _state_key(start_pose, request, context.heading_bin_count)
        records = {
            start_key: SearchRecord(
                pose=start_pose,
                distance_m=0,
                parent=None,
                primitive_route=[request.start.coordinate],
            )
        }
        frontier: list[tuple[float, float, int, SearchStateKey]] = []
        tie_breaker = count()
        heappush(
            frontier,
            (
                _distance_to_goal(start_pose, context),
                0,
                next(tie_breaker),
                start_key,
            ),
        )
        expanded_states = 0
        while frontier and expanded_states < request.maximum_expansions:
            _, queued_distance, _, state_key = heappop(frontier)
            record = records[state_key]
            if queued_distance > record.distance_m + 1e-9:
                continue
            expanded_states += 1
            analytic = _analytic_expansion(
                record.pose,
                request,
                context,
                self.dubins_planner,
            )
            if analytic is not None:
                prefix = _restore_route(records, state_key)
                return _result(_join_routes(prefix, analytic.route), expanded_states)
            for primitive in _motion_primitives(record.pose, request, context):
                primitive_pose, primitive_route = primitive
                candidate_distance = record.distance_m + request.primitive_length_m
                candidate_key = _state_key(
                    primitive_pose,
                    request,
                    context.heading_bin_count,
                )
                current = records.get(candidate_key)
                if current is not None and current.distance_m <= candidate_distance + 1e-9:
                    continue
                records[candidate_key] = SearchRecord(
                    pose=primitive_pose,
                    distance_m=candidate_distance,
                    parent=state_key,
                    primitive_route=primitive_route,
                )
                priority = candidate_distance + _distance_to_goal(primitive_pose, context)
                heappush(
                    frontier,
                    (
                        priority,
                        candidate_distance,
                        next(tie_breaker),
                        candidate_key,
                    ),
                )
        raise RuntimeError(
            "Hybrid A* не нашёл допустимый маршрут в пределах заданного лимита поиска."
        )


def _build_context(request: HybridAStarPathRequest) -> SearchContext:
    coordinates = [request.start.coordinate, request.end.coordinate]
    for zone in request.no_fly_zones:
        coordinates.extend(zone.coordinates)
        for ring in zone.interior_rings:
            coordinates.extend(ring)
    projection = projection_for_coordinates(coordinates)
    blocked_parts = [
        polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
            zone.safety_margin_m,
            join_style="mitre",
        )
        for zone in request.no_fly_zones
    ]
    blocked_geometry: BaseGeometry = (
        unary_union(blocked_parts) if blocked_parts else GeometryCollection()
    )
    start_x, start_y = projection.project(request.start.coordinate)
    end_x, end_y = projection.project(request.end.coordinate)
    if blocked_geometry.contains(Point(start_x, start_y)):
        raise RuntimeError("Начальная поза находится внутри запретной зоны.")
    if blocked_geometry.contains(Point(end_x, end_y)):
        raise RuntimeError("Конечная поза находится внутри запретной зоны.")
    return SearchContext(
        projection=projection,
        blocked_geometry=blocked_geometry,
        bounds=_search_bounds(start_x, start_y, end_x, end_y, blocked_parts, request),
        end_x=end_x,
        end_y=end_y,
        heading_bin_count=max(1, round(360 / request.heading_bin_degrees)),
    )


def _search_bounds(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    blocked_parts: list[BaseGeometry],
    request: HybridAStarPathRequest,
) -> tuple[float, float, float, float]:
    direct_distance = hypot(end_x - start_x, end_y - start_y)
    margin = max(
        request.minimum_turn_radius_m * 8,
        direct_distance * 0.4,
        request.primitive_length_m * 5,
    )
    minimum_x = min(start_x, end_x) - margin
    minimum_y = min(start_y, end_y) - margin
    maximum_x = max(start_x, end_x) + margin
    maximum_y = max(start_y, end_y) + margin
    initial_envelope = box(minimum_x, minimum_y, maximum_x, maximum_y)
    relevant = [part for part in blocked_parts if part.intersects(initial_envelope)]
    for part in relevant:
        part_minimum_x, part_minimum_y, part_maximum_x, part_maximum_y = part.bounds
        minimum_x = min(minimum_x, part_minimum_x - margin)
        minimum_y = min(minimum_y, part_minimum_y - margin)
        maximum_x = max(maximum_x, part_maximum_x + margin)
        maximum_y = max(maximum_y, part_maximum_y + margin)
    return minimum_x, minimum_y, maximum_x, maximum_y


def _analytic_expansion(
    pose: SearchPose,
    request: HybridAStarPathRequest,
    context: SearchContext,
    dubins_planner: DubinsPlanner,
) -> DubinsPathResult | None:
    if _distance_to_goal(pose, context) > request.analytic_expansion_distance_m:
        return None
    start = DubinsPose(
        coordinate=context.projection.unproject(pose.x, pose.y),
        heading_degrees=_radians_to_compass(pose.heading_radians),
    )
    return _shortest_clear_dubins(start, request.end, request, context, dubins_planner)


def _shortest_clear_dubins(
    start: DubinsPose,
    end: DubinsPose,
    request: HybridAStarPathRequest,
    context: SearchContext,
    dubins_planner: DubinsPlanner,
) -> DubinsPathResult | None:
    path_request = DubinsPathRequest(
        start=start,
        end=end,
        minimum_turn_radius_m=request.minimum_turn_radius_m,
        sampling_step_m=request.sampling_step_m,
    )
    try:
        candidates = dubins_planner.plan_candidates(path_request)
    except RuntimeError:
        return None
    return next(
        (candidate for candidate in candidates if _route_is_clear(candidate.route, context)),
        None,
    )


def _motion_primitives(
    pose: SearchPose,
    request: HybridAStarPathRequest,
    context: SearchContext,
) -> list[tuple[SearchPose, list[Coordinate]]]:
    primitives: list[tuple[SearchPose, list[Coordinate]]] = []
    maximum_curvature = 1 / request.minimum_turn_radius_m
    for curvature in (-maximum_curvature, 0.0, maximum_curvature):
        candidate = _sample_primitive(pose, curvature, request, context.projection)
        candidate_pose, route = candidate
        if not _pose_is_inside_bounds(candidate_pose, context.bounds):
            continue
        if not _route_is_clear(route, context):
            continue
        primitives.append(candidate)
    return primitives


def _sample_primitive(
    pose: SearchPose,
    curvature: float,
    request: HybridAStarPathRequest,
    projection: LocalProjection,
) -> tuple[SearchPose, list[Coordinate]]:
    sample_count = max(1, ceil(request.primitive_length_m / request.sampling_step_m))
    route = [projection.unproject(pose.x, pose.y)]
    current = pose
    step_m = request.primitive_length_m / sample_count
    for _ in range(sample_count):
        current = _advance(current, curvature, step_m)
        route.append(projection.unproject(current.x, current.y))
    return current, route


def _advance(pose: SearchPose, curvature: float, distance_m: float) -> SearchPose:
    if abs(curvature) <= 1e-12:
        return SearchPose(
            x=pose.x + distance_m * cos(pose.heading_radians),
            y=pose.y + distance_m * sin(pose.heading_radians),
            heading_radians=pose.heading_radians,
        )
    next_heading = pose.heading_radians + curvature * distance_m
    return SearchPose(
        x=pose.x + (sin(next_heading) - sin(pose.heading_radians)) / curvature,
        y=pose.y + (-cos(next_heading) + cos(pose.heading_radians)) / curvature,
        heading_radians=next_heading % (2 * pi),
    )


def _state_key(
    pose: SearchPose,
    request: HybridAStarPathRequest,
    heading_bin_count: int,
) -> SearchStateKey:
    heading_fraction = (pose.heading_radians % (2 * pi)) / (2 * pi)
    return SearchStateKey(
        x_index=round(pose.x / request.grid_resolution_m),
        y_index=round(pose.y / request.grid_resolution_m),
        heading_index=round(heading_fraction * heading_bin_count) % heading_bin_count,
    )


def _distance_to_goal(pose: SearchPose, context: SearchContext) -> float:
    return hypot(context.end_x - pose.x, context.end_y - pose.y)


def _route_is_clear(route: list[Coordinate], context: SearchContext) -> bool:
    if context.blocked_geometry.is_empty:
        return True
    projected = [context.projection.project(coordinate) for coordinate in route]
    return LineString(projected).relate(context.blocked_geometry)[0] == "F"


def _pose_is_inside_bounds(
    pose: SearchPose,
    bounds: tuple[float, float, float, float],
) -> bool:
    minimum_x, minimum_y, maximum_x, maximum_y = bounds
    return minimum_x <= pose.x <= maximum_x and minimum_y <= pose.y <= maximum_y


def _restore_route(
    records: dict[SearchStateKey, SearchRecord],
    final_key: SearchStateKey,
) -> list[Coordinate]:
    segments: list[list[Coordinate]] = []
    current_key = final_key
    while True:
        record = records[current_key]
        segments.append(record.primitive_route)
        if record.parent is None:
            break
        current_key = record.parent
    route: list[Coordinate] = []
    for segment in reversed(segments):
        route = _join_routes(route, segment)
    return route


def _join_routes(first: list[Coordinate], second: list[Coordinate]) -> list[Coordinate]:
    if not first:
        return list(second)
    if not second:
        return list(first)
    if first[-1] == second[0]:
        return [*first, *second[1:]]
    return [*first, *second]


def _result(route: list[Coordinate], expanded_states: int) -> HybridAStarPathResult:
    return HybridAStarPathResult(
        route=route,
        distance_km=route_distance_km(route),
        expanded_states=expanded_states,
    )


def _compass_to_radians(heading_degrees: float) -> float:
    return radians(90 - heading_degrees) % (2 * pi)


def _radians_to_compass(heading_radians: float) -> float:
    return (90 - heading_radians * 180 / pi) % 360
