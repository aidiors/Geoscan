"""Reference candidates for freeform benchmarks; the app never imports this module."""

from dataclasses import replace
from itertools import pairwise
from math import atan2, ceil, degrees

from shapely.affinity import rotate
from shapely.geometry import LineString
from shapely.ops import substring

from geoplan.coverage.sweep import BoustrophedonCoveragePlanner, _sweep_rows
from geoplan.domain.enums import OptimizationCriterion
from geoplan.navigation.geometry import polygon_parts
from geoplan.planning.productive import _route_pool
from geoplan.routing.freeform import (
    GeometryModel,
    Route,
    anchor_contours,
    contour_strokes,
    oriented_order,
)
from geoplan.routing.model import CostModel, RoutingProblem, Track


def production_reference(model: GeometryModel) -> Route:
    """Use the current production coverage generator and productive route pool."""
    request = model.case.request.model_copy(
        update={
            "departure_candidates": [model.coordinate(model.case.home)],
            "landing_candidates": [model.coordinate(model.case.landing)],
            "reference_airspeed_kmh": model.case.cruise_speed_kmh,
        }
    )
    result = BoustrophedonCoveragePlanner().plan(request)
    tracks = tuple(
        Track(str(i), segment.route[j], segment.route[j + 1], model.survey_cap)
        for i, segment in enumerate(result.segments)
        for j in range(0, len(segment.route) - 1, 2)
    )
    if any(len(s.route) % 2 for s in result.segments):
        raise ValueError("Reference cannot be converted to exact service pairs")
    physical = CostModel(
        RoutingProblem(
            tracks,
            model.cost.problem.aircraft,
            None,
            model.case.request.wind,
            tuple(model.case.request.exclusions),
            OptimizationCriterion.TOTAL_FLIGHT_TIME,
            None,
        )
    )
    pool = _route_pool(physical)
    if not pool:
        raise ValueError("Production reference has no feasible resource split")
    _, solution = min(pool, key=lambda item: physical.score(item[1]))
    return Route(
        "production_reference",
        tuple(
            tuple(
                tuple(model.projection.project(p) for p in physical.endpoints(token))
                for token in trip.visits
            )
            for trip in solution
        ),
    )


def candidates(model: GeometryModel) -> tuple[Route, ...]:
    reference = production_reference(model)
    flat = tuple(stroke for sortie in reference.sorties for stroke in sortie)
    # Keep the exact reference alongside an out-and-back ordering of its service
    # tracks. New contour families below have different geometric service paths.
    loop = (*flat[::2], *reversed(flat[1::2]))
    contours = contour_strokes(model)
    orders = (
        ("contours_inward", contours),
        ("contours_outward", tuple(reversed(contours))),
        ("contours_loop", (*contours[::2], *reversed(contours[1::2]))),
    )
    routes = [
        reference,
        replace(reference, family="reference_camera_on_connections", scan_connections=True),
        Route("tracks_out_and_back", (oriented_order(model, loop),)),
    ]
    for name, order in orders:
        if order:
            anchored = anchor_contours(model, tuple(order))
            routes.append(Route(name, (oriented_order(model, anchored),), True))
    routes.extend(boundary_return_routes(model))
    return tuple(routes)


def boundary_return_routes(model: GeometryModel) -> tuple[Route, ...]:
    """Split one boundary loop around interior coverage, finishing near launch.

    The first half of the boundary is scanned on departure, the other half on
    return. Interior strokes cover residual area, rather than retaining the
    reference generator's mandatory tracks. The full area/image screen decides
    whether this hybrid actually meets the experiment's quality conditions.
    """
    polygons = polygon_parts(model.target.buffer(-model.spacing / 2, join_style="mitre"))
    if len(polygons) != 1:
        return ()
    ring = tuple((float(x), float(y)) for x, y in polygons[0].exterior.coords)
    anchored = anchor_contours(model, (ring,))[0]
    boundary = LineString(anchored)
    half = boundary.length / 2
    outgoing = tuple((float(x), float(y)) for x, y in substring(boundary, 0, half).coords)
    incoming = tuple(
        (float(x), float(y)) for x, y in substring(boundary, half, boundary.length).coords
    )
    interior = model.target.buffer(-max(model.width, model.height) / 2)
    missing = interior.difference(boundary.buffer(model.spacing / 2))
    if missing.is_empty:
        return (Route("boundary_return", ((outgoing, incoming),), True),)
    rectangle = list(missing.minimum_rotated_rectangle.exterior.coords)
    a, b = max(pairwise(rectangle), key=lambda edge: LineString(edge).length)
    angle = degrees(atan2(b[1] - a[1], b[0] - a[0]))
    local = GeometryModel(replace(model.case, home=outgoing[-1], landing=incoming[0]))
    routes = []
    for name, direction in (("boundary_return", angle), ("boundary_return_cross", angle + 90)):
        rotated = rotate(missing, -direction, origin=(0, 0))
        span = rotated.bounds[3] - rotated.bounds[1]
        count = max(1, ceil(span / model.spacing))
        # Balanced rows leave at most half a spacing at both boundaries.
        spacing = span / count
        strokes = tuple(
            tuple((float(x), float(y)) for x, y in rotate(line, direction, origin=(0, 0)).coords)
            for row in _sweep_rows(rotated, spacing)
            for line in row
        )
        alternatives = (
            oriented_order(local, strokes),
            oriented_order(local, tuple(reversed(strokes))),
        )

        def cost(order):
            result = local.travel_minutes(outgoing[-1], order[0][0])
            for previous, following in pairwise(order):
                result += local.travel_minutes(previous[-1], following[0])
            return result + local.travel_minutes(order[-1][-1], incoming[0])

        best = min(alternatives, key=cost)
        routes.append(Route(name, ((outgoing, *best, incoming),), True))
        routes.append(Route(name + "_transit", ((outgoing, *best, incoming),), False))
    return tuple(routes)
