from itertools import pairwise

from shapely.geometry import LineString, Polygon
from shapely.ops import split

from src.geometry import polygon_from_coordinates, polygon_parts, projection_for_coordinates
from src.schemas import CoverageArea, CoveragePlanRequest


def automatic_partition_requests(
    request: CoveragePlanRequest,
) -> list[CoveragePlanRequest]:
    projection = projection_for_coordinates(request.area.coordinates)
    geometry = polygon_from_coordinates(
        request.area.coordinates,
        request.area.interior_rings,
        projection,
    )
    polygons = [
        partition
        for polygon in polygon_parts(geometry)
        for partition in _recursive_partitions(polygon, 0)
    ]
    return [
        CoveragePlanRequest(
            area=CoverageArea(
                area_id=f"{request.area.area_id}-adaptive-{index + 1}"[:64],
                name=f"{request.area.name}, adaptive part {index + 1}"[:128],
                coordinates=[
                    projection.unproject(float(x), float(y))
                    for x, y in list(polygon.exterior.coords)[:-1]
                ],
                interior_rings=[
                    [projection.unproject(float(x), float(y)) for x, y in list(ring.coords)[:-1]]
                    for ring in polygon.interiors
                ],
            ),
            mission_altitude_m=request.mission_altitude_m,
            parameters=request.parameters,
            exclusions=request.exclusions,
            departure_candidates=request.departure_candidates,
            landing_candidates=request.landing_candidates,
            wind=request.wind,
            reference_airspeed_kmh=request.reference_airspeed_kmh,
        )
        for index, polygon in enumerate(polygons)
    ]


def _recursive_partitions(polygon: Polygon, depth: int) -> list[Polygon]:
    if depth >= 3 or _rectangularity(polygon) >= 0.82:
        return [polygon]
    candidate_parts = _split_across_major_axis(polygon)
    if len(candidate_parts) < 2:
        return [polygon]
    minimum_area = max(10_000.0, polygon.area * 0.08)
    if any(part.area < minimum_area for part in candidate_parts):
        return [polygon]
    before = _rectangularity(polygon)
    after = sum(part.area * _rectangularity(part) for part in candidate_parts) / polygon.area
    if before >= 0.55 and after < before + 0.04:
        return [polygon]
    return [nested for part in candidate_parts for nested in _recursive_partitions(part, depth + 1)]


def _split_across_major_axis(polygon: Polygon) -> list[Polygon]:
    rectangle = polygon.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    start, end = max(
        pairwise(coordinates),
        key=lambda edge: (edge[1][0] - edge[0][0]) ** 2 + (edge[1][1] - edge[0][1]) ** 2,
    )
    delta_x = float(end[0] - start[0])
    delta_y = float(end[1] - start[1])
    length = (delta_x**2 + delta_y**2) ** 0.5
    if length <= 0:
        return [polygon]
    perpendicular_x = -delta_y / length
    perpendicular_y = delta_x / length
    center_x = float(polygon.centroid.x)
    center_y = float(polygon.centroid.y)
    span = (
        max(
            polygon.bounds[2] - polygon.bounds[0],
            polygon.bounds[3] - polygon.bounds[1],
        )
        * 3
    )
    cutting_line = LineString(
        [
            (center_x - perpendicular_x * span, center_y - perpendicular_y * span),
            (center_x + perpendicular_x * span, center_y + perpendicular_y * span),
        ]
    )
    return polygon_parts(split(polygon, cutting_line))


def _rectangularity(polygon: Polygon) -> float:
    rectangle = polygon.minimum_rotated_rectangle
    if rectangle.area <= 0:
        return 1.0
    return float(polygon.area / rectangle.area)
