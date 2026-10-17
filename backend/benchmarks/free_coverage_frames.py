"""Experimental photo-aware timing with an optional adjacent-frame diagnostic."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, replace
from itertools import pairwise
from math import ceil, hypot, inf

from geoplan.routing.freeform import (
    XY,
    GeometryModel,
    Leg,
    Route,
    _legs,
    image_footprint,
    route_legs,
)


@dataclass(frozen=True)
class RetimingResult:
    flights: tuple[tuple[Leg, ...], ...] | None
    frames_planned: int
    slowed_intervals: int
    reason: str | None = None


class RunPath:
    def __init__(self, legs: tuple[Leg, ...]) -> None:
        self.legs = legs
        self.distances = [0.0]
        self.times = [0.0]
        for leg in legs:
            self.distances.append(
                self.distances[-1] + hypot(leg.end[0] - leg.start[0], leg.end[1] - leg.start[1])
            )
            self.times.append(self.times[-1] + leg.minutes)

    def index(self, distance: float) -> int:
        return max(0, min(len(self.legs) - 1, bisect_left(self.distances, distance) - 1))

    def position(self, distance: float) -> tuple[XY, XY, XY]:
        i = self.index(distance)
        a, b = self.legs[i].start, self.legs[i].end
        fraction = (distance - self.distances[i]) / (self.distances[i + 1] - self.distances[i])
        point = (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]))
        return point, a, b

    def nominal_time(self, distance: float) -> float:
        i = self.index(distance)
        fraction = (distance - self.distances[i]) / (self.distances[i + 1] - self.distances[i])
        return self.times[i] + fraction * self.legs[i].minutes

    def advance(self, distance: float, minutes: float) -> float:
        target = self.nominal_time(distance) + minutes
        if target >= self.times[-1]:
            return self.distances[-1]
        i = max(0, bisect_left(self.times, target) - 1)
        fraction = (target - self.times[i]) / (self.times[i + 1] - self.times[i])
        return self.distances[i] + fraction * (self.distances[i + 1] - self.distances[i])

    def pieces(
        self, model: GeometryModel, start: float, end: float, minutes: float | None = None
    ) -> list[Leg]:
        cuts = [start, *(d for d in self.distances if start + 1e-7 < d < end - 1e-7), end]
        segments = []
        for left, right in pairwise(cuts):
            if right - left > 1e-7:
                a, _, _ = self.position(left)
                b, _, _ = self.position(right)
                segments.extend(_legs(model, (a, b), "survey"))
        if minutes is None:
            return segments
        nominal = sum(leg.minutes for leg in segments)
        if nominal > minutes + 1e-7:
            raise ValueError("Requested frame interval would exceed the physical speed limit")
        return [replace(leg, minutes=minutes * leg.minutes / nominal) for leg in segments]

    def reachable(self, model: GeometryModel, start: float, minutes: float) -> float:
        """Correct the linear arc-length estimate against actual split-leg costs."""
        maximum = self.advance(start, minutes)
        duration = sum(leg.minutes for leg in self.pieces(model, start, maximum))
        if duration <= minutes + 1e-10:
            return maximum
        low, high = start, maximum
        for _ in range(25):
            middle = (low + high) / 2
            duration = sum(leg.minutes for leg in self.pieces(model, start, middle))
            if duration <= minutes:
                low = middle
            else:
                high = middle
        return low


def _retime_run(
    model: GeometryModel, legs: tuple[Leg, ...], first_delay: float
) -> tuple[list[Leg] | None, int, int, str | None]:
    path = RunPath(legs)
    if path.times[-1] <= first_delay + 1e-10:
        return list(legs), 0, 0, None
    current = path.reachable(model, 0, first_delay)
    result = path.pieces(model, 0, current, first_delay) if first_delay > 1e-10 else []
    frames = 1
    slowed = 0
    threshold = model.case.request.parameters.forward_overlap_percent / 100
    while current < path.distances[-1] - 1e-7:
        approximate_remaining = path.times[-1] - path.nominal_time(current)
        remaining = (
            sum(leg.minutes for leg in path.pieces(model, current, path.distances[-1]))
            if approximate_remaining < model.interval_minutes * 1.01
            else inf
        )
        if remaining < model.interval_minutes - 1e-9:
            result.extend(path.pieces(model, current, path.distances[-1], remaining))
            break
        maximum = path.reachable(model, current, model.interval_minutes)
        point, a, b = path.position(current)
        previous = image_footprint(point, a, b, model.width, model.height)

        def allowed(distance, previous=previous):
            position, start, end = path.position(distance)
            frame = image_footprint(position, start, end, model.width, model.height)
            return (
                frame.intersection(previous).area / (model.width * model.height)
                >= threshold - 1e-10
            )

        selected = maximum
        if not allowed(maximum):
            slowed += 1
            # Bounded geometric search, not a globally optimal velocity profile.
            # Check all original vertices too: heading changes are discontinuous.
            samples = {
                maximum - step * 0.5 for step in range(1, ceil((maximum - current) / 0.5) + 1)
            }
            samples.update(d for d in path.distances if current < d < maximum)
            feasible = next(
                (s for s in sorted(samples, reverse=True) if s > current + 0.05 and allowed(s)),
                None,
            )
            if feasible is None:
                return None, frames, slowed, "no_progress_with_required_frame_overlap"
            low, high = feasible, min(maximum, feasible + 0.5)
            # Refine only within the same geometric edge; never bisect across an
            # unchecked change of heading and assume monotonicity there.
            if path.index(low) == path.index(high):
                for _ in range(10):
                    middle = (low + high) / 2
                    if allowed(middle):
                        low = middle
                    else:
                        high = middle
            selected = low
        try:
            result.extend(path.pieces(model, current, selected, model.interval_minutes))
        except ValueError:
            return None, frames, slowed, "physical_timing_roundoff_or_infeasibility"
        current = selected
        frames += 1
        if frames > 5_000:
            return None, frames, slowed, "frame_budget_exhausted"
    return result, frames, slowed, None


def retime(model: GeometryModel, route: Route) -> RetimingResult:
    flights = []
    frames = slowed = 0
    for flight in route_legs(model, route):
        result = []
        clock = 0.0
        last_image = -inf
        index = 0
        while index < len(flight):
            if flight[index].phase != "survey":
                leg = flight[index]
                result.append(leg)
                clock += leg.minutes
                index += 1
                continue
            end = index + 1
            while end < len(flight) and flight[end].phase == "survey":
                end += 1
            delay = max(0, last_image + model.interval_minutes - clock)
            run, count, changes, reason = _retime_run(model, flight[index:end], delay)
            frames += count
            slowed += changes
            if run is None:
                return RetimingResult(None, frames, slowed, reason)
            result.extend(run)
            if count:
                last_image = clock + delay + (count - 1) * model.interval_minutes
            clock += sum(leg.minutes for leg in run)
            index = end
        flights.append(tuple(result))
    return RetimingResult(tuple(flights), frames, slowed)
