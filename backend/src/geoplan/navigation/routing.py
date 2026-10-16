from __future__ import annotations

from heapq import heappop, heappush
from itertools import pairwise
from math import asin, atan2, cos, degrees, inf, radians, sin, sqrt

from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geoplan.coverage.models import CoverageArea
from geoplan.domain.airspace import AllowedAirspace, NoFlyZone
from geoplan.domain.geo import Coordinate
from geoplan.navigation.geometry import (
    LocalProjection,
    line_from_coordinates,
    point_from_coordinate,
    polygon_from_coordinates,
    polygon_parts,
    projection_for_coordinates,
)


class RouteGeometryError(Exception):
    pass


def build_safe_route(route: list[Coordinate], no_fly_zones: list[NoFlyZone]) -> list[Coordinate]:
    if not no_fly_zones:
        return list(route)
    projection = projection_for_coordinates(
        [*route, *(point for zone in no_fly_zones for point in zone.coordinates)]
    )
    blocked = _blocked_geometry(no_fly_zones, projection)
    safe_route = [route[0]]
    for start, end in pairwise(route):
        leg = _shortest_safe_leg(start, end, blocked, projection)
        safe_route.extend(leg[1:])
    if not route_is_clear_of_no_fly_zones(safe_route, no_fly_zones):
        raise RouteGeometryError("Построенный маршрут пересекает запретную зону.")
    return safe_route


def calculate_route_multiplier(route: list[Coordinate], safe_route: list[Coordinate]) -> float:
    original_length = route_distance_km(route)
    if original_length == 0:
        return 1.0
    return route_distance_km(safe_route) / original_length


def route_distance_km(route: list[Coordinate]) -> float:
    return sum(coordinate_distance_km(start, end) for start, end in pairwise(route))


def coordinate_distance_km(start: Coordinate, end: Coordinate) -> float:
    latitude_delta = radians(end.latitude - start.latitude)
    longitude_delta = radians(end.longitude - start.longitude)
    start_latitude = radians(start.latitude)
    end_latitude = radians(end.latitude)
    haversine = sin(latitude_delta / 2) ** 2 + (
        cos(start_latitude) * cos(end_latitude) * sin(longitude_delta / 2) ** 2
    )
    return 6_371 * 2 * asin(min(1, sqrt(haversine)))


def route_bearing_degrees(start: Coordinate, end: Coordinate) -> float:
    start_latitude = radians(start.latitude)
    end_latitude = radians(end.latitude)
    longitude_delta = radians(end.longitude - start.longitude)
    x = sin(longitude_delta) * cos(end_latitude)
    y = cos(start_latitude) * sin(end_latitude) - (
        sin(start_latitude) * cos(end_latitude) * cos(longitude_delta)
    )
    return (degrees(atan2(x, y)) + 360) % 360


def route_is_clear_of_no_fly_zones(
    route: list[Coordinate],
    no_fly_zones: list[NoFlyZone],
) -> bool:
    if not no_fly_zones:
        return True
    projection = projection_for_coordinates(
        [*route, *(point for zone in no_fly_zones for point in zone.coordinates)]
    )
    blocked = _blocked_geometry(no_fly_zones, projection)
    route_points = [point_from_coordinate(point, projection) for point in route]
    if any(blocked.contains(point) for point in route_points):
        return False
    return all(
        _line_is_clear(line_from_coordinates([start, end], projection), blocked)
        for start, end in pairwise(route)
    )


def route_is_inside_allowed_airspaces(
    route: list[Coordinate],
    airspaces: list[AllowedAirspace],
) -> bool:
    if not airspaces:
        return True
    projection = projection_for_coordinates(
        [*route, *(point for airspace in airspaces for point in airspace.coordinates)]
    )
    allowed = unary_union(
        [polygon_from_coordinates(airspace.coordinates, [], projection) for airspace in airspaces]
    ).buffer(0.05)
    return all(
        allowed.covers(line_from_coordinates([start, end], projection))
        for start, end in pairwise(route)
    )


def route_is_inside_coverage_area(route: list[Coordinate], area: CoverageArea) -> bool:
    projection = projection_for_coordinates([*route, *area.coordinates])
    target = polygon_from_coordinates(
        area.coordinates,
        area.interior_rings,
        projection,
    ).buffer(0.05)
    return all(
        target.covers(line_from_coordinates([start, end], projection))
        for start, end in pairwise(route)
    )


def _blocked_geometry(
    zones: list[NoFlyZone],
    projection: LocalProjection,
) -> BaseGeometry:
    polygons = [
        polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
            zone.safety_margin_m,
            join_style="mitre",
        )
        for zone in zones
    ]
    if not polygons:
        return GeometryCollection()
    return unary_union(polygons)


def _shortest_safe_leg(
    start: Coordinate,
    end: Coordinate,
    blocked: BaseGeometry,
    projection: LocalProjection,
) -> list[Coordinate]:
    start_xy = projection.project(start)
    end_xy = projection.project(end)
    start_point = Point(start_xy)
    end_point = Point(end_xy)
    if blocked.contains(start_point) or blocked.contains(end_point):
        raise RouteGeometryError("Начальная или конечная точка попала в буфер запретной зоны.")
    direct = LineString([start_xy, end_xy])
    if _line_is_clear(direct, blocked):
        return [start, end]
    corridor = direct.envelope.buffer(max(1_000, direct.length))
    relevant_parts = [part for part in polygon_parts(blocked) if part.intersects(corridor)]
    relevant = unary_union(relevant_parts)
    nodes = [start_xy, end_xy, *_visibility_vertices(relevant_parts)]
    path = _dijkstra_visibility_path(nodes, relevant)
    if path is None:
        raise RouteGeometryError("Между точками нет безопасного пути в свободном пространстве.")
    return [projection.unproject(x, y) for x, y in path]


def _visibility_vertices(polygons: list[Polygon]) -> list[tuple[float, float]]:
    vertices: list[tuple[float, float]] = []
    for polygon in polygons:
        center = polygon.centroid
        vertices.extend(
            _nudged_vertex(float(x), float(y), center.x, center.y)
            for x, y in list(polygon.exterior.coords)[:-1]
        )
        for ring in polygon.interiors:
            vertices.extend(
                _nudged_vertex(float(x), float(y), center.x, center.y)
                for x, y in list(ring.coords)[:-1]
            )
    return vertices


def _nudged_vertex(
    x: float,
    y: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float]:
    delta_x = x - center_x
    delta_y = y - center_y
    length = sqrt(delta_x**2 + delta_y**2)
    if length == 0:
        return x, y
    clearance_m = 0.1
    return x + delta_x / length * clearance_m, y + delta_y / length * clearance_m


def _dijkstra_visibility_path(
    nodes: list[tuple[float, float]],
    blocked: BaseGeometry,
) -> list[tuple[float, float]] | None:
    distances = [inf] * len(nodes)
    previous: list[int | None] = [None] * len(nodes)
    distances[0] = 0
    queue: list[tuple[float, int]] = [(0, 0)]
    visited: set[int] = set()
    while queue:
        distance, index = heappop(queue)
        if index in visited:
            continue
        visited.add(index)
        if index == 1:
            return _restore_path(nodes, previous)
        for neighbor, point in enumerate(nodes):
            if neighbor == index or neighbor in visited:
                continue
            edge = LineString([nodes[index], point])
            if not _line_is_clear(edge, blocked):
                continue
            candidate = distance + edge.length
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                previous[neighbor] = index
                heappush(queue, (candidate, neighbor))
    return None


def _restore_path(
    nodes: list[tuple[float, float]],
    previous: list[int | None],
) -> list[tuple[float, float]]:
    path = [1]
    current = 1
    while current != 0:
        parent = previous[current]
        if parent is None:
            raise RouteGeometryError("Граф видимости не содержит путь к цели.")
        path.append(parent)
        current = parent
    return [nodes[index] for index in reversed(path)]


def _line_is_clear(line: LineString, blocked: BaseGeometry) -> bool:
    if blocked.is_empty:
        return True
    return line.relate(blocked)[0] == "F"
