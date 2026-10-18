"""Parallel-strip neighbors can change at every longitudinal endpoint."""

from collections.abc import Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import pairwise
from math import hypot

from shapely.geometry import Polygon


@dataclass(frozen=True)
class SurveyStrip:
    mission_id: tuple[str, str]
    start: tuple[float, float]
    end: tuple[float, float]
    width: float

    @property
    def direction(self) -> tuple[float, float]:
        length = hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])
        return (self.end[0] - self.start[0]) / length, (self.end[1] - self.start[1]) / length


def lateral_overlaps(
    strips: Sequence[SurveyStrip], required: float
) -> tuple[list[float], int, list[Polygon]]:
    if len(strips) > 1000:
        return [], 0, []
    pairs, problems = {}, []
    for i, source in enumerate(strips):
        ux, uy = source.direction
        length = hypot(source.end[0] - source.start[0], source.end[1] - source.start[1])
        spans = []
        for j, target in enumerate(strips):
            if i == j:
                continue
            vx, vy = target.direction
            if abs(ux * vx + uy * vy) < 0.99985:
                continue
            delta = [
                (p[0] - source.start[0], p[1] - source.start[1]) for p in (target.start, target.end)
            ]
            along = sorted(x * ux + y * uy for x, y in delta)
            start, end = max(0.0, along[0]), min(length, along[1])
            offset = sum(-x * uy + y * ux for x, y in delta) / 2
            if end - start >= 0.1 and abs(offset) >= 0.1:
                spans.append((start, end, offset, j))
        boundaries = sorted({point for start, end, _, _ in spans for point in (start, end)})
        pending = iter(sorted(spans))
        upcoming = next(pending, None)
        active: dict[bool, list[tuple[float, int, float]]] = {False: [], True: []}
        for start, end in pairwise(boundaries):
            while upcoming is not None and upcoming[0] <= start + 1e-7:
                _, stop, offset, j = upcoming
                heappush(active[offset > 0], (abs(offset), j, stop))
                upcoming = next(pending, None)
            for heap in active.values():
                while heap and heap[0][2] <= start + 1e-7:
                    heappop(heap)
                if not heap or end - start < 1e-7:
                    continue
                distance, j, _ = heap[0]
                target = strips[j]
                percent = max(
                    0.0,
                    min(
                        100.0,
                        ((source.width + target.width) / 2 - distance)
                        / min(source.width, target.width)
                        * 100,
                    ),
                )
                key = tuple(sorted((i, j)))
                pairs[key] = min(pairs.get(key, 100.0), percent)
                if percent < required - 0.1:
                    points = [
                        (source.start[0] + t * ux, source.start[1] + t * uy) for t in (start, end)
                    ]
                    tx, ty = target.start
                    vx, vy = target.end[0] - tx, target.end[1] - ty
                    denominator = vx * ux + vy * uy
                    target_points = [
                        (tx + vx * fraction, ty + vy * fraction)
                        for t in (start, end)
                        for fraction in [
                            ((source.start[0] - tx) * ux + (source.start[1] - ty) * uy + t)
                            / denominator
                        ]
                    ]
                    problems.append(Polygon([*points, *reversed(target_points)]))
    cross = sum(strips[i].mission_id != strips[j].mission_id for i, j in pairs)
    return list(pairs.values()), cross, problems
