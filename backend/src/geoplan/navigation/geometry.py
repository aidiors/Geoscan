from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians

from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from geoplan.domain.geo import Coordinate


@dataclass(frozen=True)
class LocalProjection:
    origin_longitude: float
    origin_latitude: float

    @property
    def longitude_scale(self) -> float:
        return 111_320 * max(cos(radians(self.origin_latitude)), 0.01)

    def project(self, coordinate: Coordinate) -> tuple[float, float]:
        return (
            (coordinate.longitude - self.origin_longitude) * self.longitude_scale,
            (coordinate.latitude - self.origin_latitude) * 110_574,
        )

    def unproject(self, x: float, y: float) -> Coordinate:
        return Coordinate(
            longitude=self.origin_longitude + x / self.longitude_scale,
            latitude=self.origin_latitude + y / 110_574,
        )


def projection_for_coordinates(coordinates: list[Coordinate]) -> LocalProjection:
    if not coordinates:
        raise ValueError("Для локальной проекции нужны координаты.")
    return LocalProjection(
        origin_longitude=sum(point.longitude for point in coordinates) / len(coordinates),
        origin_latitude=sum(point.latitude for point in coordinates) / len(coordinates),
    )


def polygon_from_coordinates(
    coordinates: list[Coordinate],
    interior_rings: list[list[Coordinate]],
    projection: LocalProjection,
) -> BaseGeometry:
    polygon = Polygon(
        shell=[projection.project(point) for point in coordinates],
        holes=[
            [projection.project(point) for point in ring]
            for ring in interior_rings
            if len(ring) >= 3
        ],
    )
    geometry = make_valid(polygon)
    if geometry.is_empty:
        raise ValueError("Полигон не образует допустимую геометрию.")
    return geometry


def line_from_coordinates(
    coordinates: list[Coordinate],
    projection: LocalProjection,
) -> LineString:
    return LineString([projection.project(point) for point in coordinates])


def point_from_coordinate(coordinate: Coordinate, projection: LocalProjection) -> Point:
    return Point(projection.project(coordinate))


def polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [part for part in geometry.geoms if isinstance(part, Polygon)]
    return []
