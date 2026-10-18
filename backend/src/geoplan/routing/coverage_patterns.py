"""Closed contour shells and endpoint-aware arc partitions around a swept core."""

from __future__ import annotations

from itertools import pairwise
from math import atan2, ceil, degrees, hypot

from shapely.affinity import rotate
from shapely.geometry import LineString, Point
from shapely.ops import substring

from geoplan.coverage.sweep import _sweep_rows
from geoplan.navigation.geometry import polygon_parts
from geoplan.routing.freeform import (
    XY,
    GeometryModel,
    Route,
    Stroke,
    anchor_contours,
    oriented_order,
)


def coverage_angles(model: GeometryModel) -> tuple[float, ...]:
    """Bounded Cartesian headings for a shell's swept core; never change overlap.

    Retain the original axes. Add the long axis of the minimum enclosing
    rectangle and its perpendicular, then wind or distinct-site directions.
    Near-circular shapes have no meaningful major axis, so do not spend search
    budget on a polygon-tessellation-dependent orientation for them.
    """
    angles = [0.0, 90.0]

    def add_pair(angle: float) -> None:
        for value in (angle, angle + 90):
            normalized = round(value % 180, 6) % 180
            if all(
                min(abs(normalized - old), 180 - abs(normalized - old)) > 1e-5 for old in angles
            ):
                angles.append(normalized)

    rectangle = model.target.minimum_rotated_rectangle
    if rectangle.geom_type == "Polygon" and rectangle.area > 0:
        edges = list(pairwise(rectangle.exterior.coords))
        a, b = max(edges, key=lambda edge: hypot(edge[1][0] - edge[0][0], edge[1][1] - edge[0][1]))
        lengths = [hypot(end[0] - start[0], end[1] - start[1]) for start, end in edges]
        if max(lengths) > min(lengths) * 1.05 or model.target.area / rectangle.area > 0.95:
            add_pair(degrees(atan2(b[1] - a[1], b[0] - a[0])))
    wind = model.case.request.wind
    if wind is not None and wind.speed_mps > 0:
        # Wind bearing is clockwise from north; shell angle is CCW from east.
        add_pair(90 - wind.direction_degrees)
    if len(angles) < 6:
        dx = model.case.landing[0] - model.case.home[0]
        dy = model.case.landing[1] - model.case.home[1]
        if hypot(dx, dy) > 1e-6:
            add_pair(degrees(atan2(dy, dx)))
    return tuple(angles[:6])


def split_ring(ring: Stroke, landing: XY) -> tuple[Stroke, Stroke]:
    """Partition the full perimeter without gaps or duplicated service distance.

    The ring was anchored near departure. With coincident sites, a midpoint
    split avoids an almost empty arc. Both arcs retain their shared endpoints.
    """
    line = LineString(ring)
    cut = line.project(Point(landing))
    if min(cut, line.length - cut) < line.length * 0.05:
        cut = line.length / 2
    first = substring(line, 0, cut)
    second = substring(line, cut, line.length)
    return tuple(first.coords), tuple(second.coords)


def shell_routes(
    model: GeometryModel, layers: int, angle: float, *, seam_ratio: float = 0.1, split: bool = False
) -> tuple[Route, ...]:
    if layers < 1 or not 0 <= seam_ratio <= 0.5:
        raise ValueError("Shell layers and seam ratio are outside the experiment bounds")
    rings = []
    for layer in range(layers):
        geometry = model.target.buffer(-(layer + 0.5) * model.spacing, join_style="mitre")
        if geometry.is_empty:
            return ()
        for polygon in polygon_parts(geometry):
            for boundary in (polygon.exterior, *polygon.interiors):
                rings.append(tuple((float(x), float(y)) for x, y in boundary.coords))
    rings = anchor_contours(model, tuple(rings))
    inner = model.target.buffer(-(layers - seam_ratio) * model.spacing, join_style="mitre")
    if inner.is_empty:
        return ()
    rotated = rotate(inner, -angle, origin=(0, 0))
    span = rotated.bounds[3] - rotated.bounds[1]
    spacing = span / max(1, ceil(span / model.spacing))
    strokes = tuple(
        tuple((float(x), float(y)) for x, y in rotate(line, angle, origin=(0, 0)).coords)
        for row in _sweep_rows(rotated, spacing)
        for line in row
    )
    routes = []
    for backwards in (False, True):
        middle = tuple(reversed(strokes)) if backwards else strokes
        if split:
            arcs = [split_ring(ring, model.case.landing) for ring in rings]
            order = (*[a for a, _ in arcs], *middle, *[b for _, b in reversed(arcs)])
        else:
            order = (*rings[::2], *middle, *reversed(rings[1::2]))
        ordered = oriented_order(model, order)
        routes.append(
            Route(
                f"{'split_shell' if split else 'shell'}_{layers}_angle_{angle:g}_"
                f"seam_{seam_ratio:g}_reverse_{backwards}",
                (ordered,),
            )
        )
    return tuple(routes)
