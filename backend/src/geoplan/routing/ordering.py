"""Geometric cells and order candidates over unchanged service tracks."""

from itertools import pairwise
from math import hypot, inf

from geoplan.navigation.geometry import projection_for_coordinates
from geoplan.routing.model import CostModel


def track_cells(model: CostModel) -> tuple[tuple[int, ...], ...]:
    """Continue only one-to-one overlap links between consecutive parallel sweep rows.

    Splits/merges end a cell. No track is shortened, added or removed. Nonparallel
    inputs fall back to singleton cells rather than inventing sweep adjacency.
    """
    tracks = model.problem.tracks
    projection = projection_for_coordinates([t.start for t in tracks])
    x0, y0 = projection.project(tracks[0].start)
    x1, y1 = projection.project(tracks[0].end)
    length = hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    intervals = []
    for task, track in enumerate(tracks):
        a, b = projection.project(track.start), projection.project(track.end)
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx * uy - dy * ux) > max(0.1, hypot(dx, dy) * 1e-5):
            return tuple((i,) for i in range(len(tracks)))
        low, high = sorted((a[0] * ux + a[1] * uy, b[0] * ux + b[1] * uy))
        row = -(a[0] + b[0]) * uy / 2 + (a[1] + b[1]) * ux / 2
        intervals.append((row, low, high, task))
    rows: list[list[tuple[float, float, float, int]]] = []
    for interval in sorted(intervals):
        if not rows or abs(interval[0] - rows[-1][0][0]) > 0.1:
            rows.append([])
        rows[-1].append(interval)
    gaps = [b[0][0] - a[0][0] for a, b in pairwise(rows)]
    spacing = sorted(gaps)[len(gaps) // 2] if gaps else inf
    cells: list[list[int]] = []
    previous: dict[int, int] = {}
    for index, row in enumerate(rows):
        before = rows[index - 1] if index else []
        links = {
            item[3]: [
                old[3]
                for old in before
                if item[0] - old[0] <= spacing * 1.5
                and min(item[2], old[2]) - max(item[1], old[1]) > 0.1
            ]
            for item in row
        }
        following: dict[int, int] = {}
        for item in row:
            parents = links[item[3]]
            if len(parents) == 1 and sum(parents[0] in p for p in links.values()) == 1:
                cell = previous[parents[0]]
            else:
                cell = len(cells)
                cells.append([])
            cells[cell].append(item[3])
            following[item[3]] = cell
        previous = following
    return tuple(tuple(cell) for cell in cells)


def cell_orders(model: CostModel) -> tuple[tuple[int, ...], ...]:
    cells = track_cells(model)
    orders = [tuple(range(len(model.problem.tracks)))]
    # Nearest cell endpoint determines the cell order; exact safe geometry and
    # orientation are evaluated below. Try starts from both sweep boundaries.
    for first in dict.fromkeys((0, len(cells) - 1)):
        for backwards in (False, True):
            remaining = set(range(len(cells))) - {first}
            current = tuple(reversed(cells[first])) if backwards else cells[first]
            order = list(current)
            while remaining:
                end = model.problem.tracks[order[-1]]
                choices = []
                for cell in sorted(remaining):
                    for flipped in (False, True):
                        candidate = tuple(reversed(cells[cell])) if flipped else cells[cell]
                        track = model.problem.tracks[candidate[0]]
                        distance = min(
                            (a.longitude - b.longitude) ** 2 + (a.latitude - b.latitude) ** 2
                            for a in (end.start, end.end)
                            for b in (track.start, track.end)
                        )
                        choices.append((distance, cell, candidate))
                _, selected, current = min(choices)
                remaining.remove(selected)
                order.extend(current)
            orders.append(tuple(order))
    return tuple(dict.fromkeys(orders))


def order_cost(model: CostModel, order: tuple[int, ...]) -> float:
    """Cheap orientation DP ranks orders; the catalog uses safe geometry and wind.

    Straight-line ranking is only a heuristic for candidate generation.
    """
    projection = projection_for_coordinates([t.start for t in model.problem.tracks])
    home = model.problem.aircraft[0].home
    landing = model.problem.aircraft[0].landing_coordinate

    def distance(a, b):
        ax, ay = projection.project(a)
        bx, by = projection.project(b)
        return hypot(ax - bx, ay - by)

    labels = [0.0, 0.0]
    previous = None
    for task in order:
        updated = []
        for flag in (0, 1):
            start, _ = model.endpoints(2 * task + flag)
            incoming = (
                distance(home, start)
                if previous is None
                else min(
                    labels[f] + distance(model.endpoints(2 * previous + f)[1], start)
                    for f in (0, 1)
                )
            )
            updated.append(incoming)
        previous, labels = task, updated
    return min(labels[f] + distance(model.endpoints(2 * order[-1] + f)[1], landing) for f in (0, 1))
