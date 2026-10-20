"""Coverage alternatives and bounded selection, independent of mission orchestration."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import product
from math import isfinite
from typing import Protocol

from geoplan.routing.coverage_patterns import coverage_angles, shell_routes
from geoplan.routing.freeform import (
    GeometryModel,
    Leg,
    Route,
    anchor_contours,
    contour_strokes,
    oriented_order,
    route_legs,
)


class PatternFamily(StrEnum):
    CONTOURS = "contours"
    SHELL = "shell"
    SPLIT_SHELL = "split_shell"


@dataclass(frozen=True)
class CoveragePattern:
    route: Route
    family: PatternFamily
    heading: float | None = None
    layers: int = 0

    @property
    def group(self) -> tuple[PatternFamily, float | None, int]:
        return self.family, self.heading, self.layers


class CoveragePatternGenerator(Protocol):
    def generate(
        self, model: GeometryModel, *, additional_only: bool = False
    ) -> tuple[CoveragePattern, ...]: ...


@dataclass(frozen=True)
class ShellCoveragePatternGenerator:
    layers: tuple[int, ...] = (1, 3, 5, 8, 9)
    maximum_contours: int = 40

    def generate(
        self, model: GeometryModel, *, additional_only: bool = False
    ) -> tuple[CoveragePattern, ...]:
        angles = (
            tuple(angle for angle in coverage_angles(model) if angle not in (0, 90))
            if additional_only
            else (0, 90)
        )
        if not angles:
            return ()
        patterns = []
        rings = contour_strokes(model) if not additional_only else ()
        if len(rings) <= self.maximum_contours:
            for name, order in (
                ("inward", rings),
                ("outward", tuple(reversed(rings))),
                ("loop", (*rings[::2], *reversed(rings[1::2]))),
            ):
                if order:
                    anchored = anchor_contours(model, tuple(order))
                    route = Route(f"contours_{name}", (oriented_order(model, anchored),))
                    patterns.append(CoveragePattern(route, PatternFamily.CONTOURS))
        for layers, angle, split in product(self.layers, angles, (False, True)):
            family = PatternFamily.SPLIT_SHELL if split else PatternFamily.SHELL
            patterns.extend(
                CoveragePattern(route, family, angle, layers)
                for route in shell_routes(model, layers, angle, split=split)
            )
        return tuple(patterns)


@dataclass(frozen=True)
class RankedCoverageCandidate:
    pattern: CoveragePattern
    legs: tuple[Leg, ...]

    @property
    def flight_minutes(self) -> float:
        return sum(leg.minutes for leg in self.legs)

    @property
    def key(self) -> tuple[float, str]:
        return self.flight_minutes, self.pattern.route.family


class CoverageCandidateSelector(Protocol):
    def select(
        self, candidates: Sequence[RankedCoverageCandidate]
    ) -> tuple[RankedCoverageCandidate, ...]: ...


@dataclass(frozen=True)
class DiverseCoverageCandidateSelector:
    """Keep the old cheapest prefix, then cover distinct families/headings/depths.

    A run of reversed versions of the same geometry must not consume the entire
    expensive screening budget. Diversity supplements, never replaces, the old
    prefix; downstream mission construction and audit remain authoritative.
    """

    prefix_size: int = 6
    maximum_candidates: int = 18

    def __post_init__(self) -> None:
        if not 0 < self.prefix_size <= self.maximum_candidates:
            raise ValueError("Candidate budget must include the positive original prefix")

    def select(
        self, candidates: Sequence[RankedCoverageCandidate]
    ) -> tuple[RankedCoverageCandidate, ...]:
        ranked = sorted(candidates, key=lambda candidate: candidate.key)
        selected = ranked[: self.prefix_size]
        groups = {candidate.pattern.group for candidate in selected}
        remaining = []
        for candidate in ranked[self.prefix_size :]:
            if len(selected) < self.maximum_candidates and candidate.pattern.group not in groups:
                selected.append(candidate)
                groups.add(candidate.pattern.group)
            else:
                remaining.append(candidate)
        selected.extend(remaining[: self.maximum_candidates - len(selected)])
        return tuple(selected)


@dataclass(frozen=True)
class CoverageCandidatePool:
    generator: CoveragePatternGenerator = field(default_factory=ShellCoveragePatternGenerator)
    selector: CoverageCandidateSelector = field(default_factory=DiverseCoverageCandidateSelector)

    def candidates(
        self, model: GeometryModel, *, additional_only: bool = False
    ) -> tuple[RankedCoverageCandidate, ...]:
        ranked = []
        for pattern in self.generator.generate(model, additional_only=additional_only):
            try:
                flights = route_legs(model, pattern.route)
            except ValueError:
                # An unreachable connector invalidates this alternative, not
                # the already audited incumbent or the rest of the pool.
                continue
            if len(flights) != 1 or not flights[0]:
                continue
            if not all(isfinite(leg.minutes) and leg.minutes > 0 for leg in flights[0]):
                continue
            ranked.append(RankedCoverageCandidate(pattern, flights[0]))
        return self.selector.select(ranked)
