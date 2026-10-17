"""Closed contour shells and endpoint-aware arc partitions around a swept core."""

from __future__ import annotations

from math import ceil

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
