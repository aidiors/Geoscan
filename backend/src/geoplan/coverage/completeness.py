"""Reject incomplete straight-strip layouts before mission assignment."""

from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


@dataclass(frozen=True)
class SweepCoverageValidator:
    minimum_percent: float = 99

    def covered_percent(
        self, target: BaseGeometry, strips: Sequence[LineString], footprint_width_m: float
    ) -> float:
        if not strips or target.area <= 0:
            return 0
        swaths = [line.buffer(footprint_width_m / 2, cap_style="square") for line in strips]
        return min(100.0, unary_union(swaths).intersection(target).area / target.area * 100)

    def accepts(
        self, target: BaseGeometry, strips: Sequence[LineString], footprint_width_m: float
    ) -> bool:
        return self.covered_percent(target, strips, footprint_width_m) >= self.minimum_percent
