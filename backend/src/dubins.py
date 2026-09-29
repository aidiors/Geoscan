from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import acos, atan2, ceil, cos, hypot, pi, radians, sin, sqrt
from typing import Protocol

from src.geometry import LocalProjection, projection_for_coordinates
from src.schemas import Coordinate, DubinsPathRequest, DubinsPathResult, DubinsPathType


class SegmentMode(StrEnum):
    LEFT = "left"
    STRAIGHT = "straight"
    RIGHT = "right"


@dataclass(frozen=True)
class NormalizedPath:
    path_type: DubinsPathType
    modes: tuple[SegmentMode, SegmentMode, SegmentMode]
    lengths: tuple[float, float, float]

    @property
    def total_length(self) -> float:
        return sum(self.lengths)


@dataclass(frozen=True)
class DubinsProblem:
    projection: LocalProjection
    start_x: float
    start_y: float
    direction: float
    initial_yaw: float
    paths: list[NormalizedPath]


class DubinsPlanner(Protocol):
    def plan(self, request: DubinsPathRequest) -> DubinsPathResult: ...

    def plan_candidates(self, request: DubinsPathRequest) -> list[DubinsPathResult]: ...


@dataclass(frozen=True)
class ExactDubinsPlanner:
    def plan(self, request: DubinsPathRequest) -> DubinsPathResult:
        problem = _prepare_problem(request)
        return _path_result(problem.paths[0], problem, request)

    def plan_candidates(self, request: DubinsPathRequest) -> list[DubinsPathResult]:
        problem = _prepare_problem(request)
        return [_path_result(path, problem, request) for path in problem.paths]


def _prepare_problem(request: DubinsPathRequest) -> DubinsProblem:
    projection = projection_for_coordinates([request.start.coordinate, request.end.coordinate])
    start_x, start_y = projection.project(request.start.coordinate)
    end_x, end_y = projection.project(request.end.coordinate)
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    direction = atan2(delta_y, delta_x)
    normalized_distance = hypot(delta_x, delta_y) / request.minimum_turn_radius_m
    start_yaw = radians(90 - request.start.heading_degrees)
    end_yaw = radians(90 - request.end.heading_degrees)
    alpha = _mod_two_pi(start_yaw - direction)
    beta = _mod_two_pi(end_yaw - direction)
    candidates = [
        path
        for builder in _PATH_BUILDERS
        if (path := builder(alpha, beta, normalized_distance)) is not None
    ]
    if not candidates:
        raise RuntimeError("Допустимая Dubins-траектория отсутствует.")
    return DubinsProblem(
        projection=projection,
        start_x=start_x,
        start_y=start_y,
        direction=direction,
        initial_yaw=alpha,
        paths=sorted(
            candidates,
            key=lambda path: (path.total_length, path.path_type.value),
        ),
    )


def _path_result(
    path: NormalizedPath,
    problem: DubinsProblem,
    request: DubinsPathRequest,
) -> DubinsPathResult:
    return DubinsPathResult(
        path_type=path.path_type,
        route=_sample_path(
            path,
            problem.initial_yaw,
            problem.direction,
            problem.start_x,
            problem.start_y,
            request,
            problem.projection,
        ),
        distance_km=path.total_length * request.minimum_turn_radius_m / 1_000,
    )


class PathBuilder(Protocol):
    def __call__(self, alpha: float, beta: float, distance: float) -> NormalizedPath | None: ...


def _left_straight_left(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    squared = 2 + distance**2 - 2 * cosine_difference + 2 * distance * (sine_alpha - sine_beta)
    if squared < 0:
        return None
    tangent = atan2(cosine_beta - cosine_alpha, distance + sine_alpha - sine_beta)
    return NormalizedPath(
        path_type=DubinsPathType.LEFT_STRAIGHT_LEFT,
        modes=(SegmentMode.LEFT, SegmentMode.STRAIGHT, SegmentMode.LEFT),
        lengths=(
            _mod_two_pi(-alpha + tangent),
            sqrt(squared),
            _mod_two_pi(beta - tangent),
        ),
    )


def _right_straight_right(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    squared = 2 + distance**2 - 2 * cosine_difference + 2 * distance * (sine_beta - sine_alpha)
    if squared < 0:
        return None
    tangent = atan2(cosine_alpha - cosine_beta, distance - sine_alpha + sine_beta)
    return NormalizedPath(
        path_type=DubinsPathType.RIGHT_STRAIGHT_RIGHT,
        modes=(SegmentMode.RIGHT, SegmentMode.STRAIGHT, SegmentMode.RIGHT),
        lengths=(
            _mod_two_pi(alpha - tangent),
            sqrt(squared),
            _mod_two_pi(-beta + tangent),
        ),
    )


def _left_straight_right(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    squared = -2 + distance**2 + 2 * cosine_difference + 2 * distance * (sine_alpha + sine_beta)
    if squared < 0:
        return None
    straight = sqrt(squared)
    tangent = atan2(
        -cosine_alpha - cosine_beta,
        distance + sine_alpha + sine_beta,
    ) - atan2(-2, straight)
    return NormalizedPath(
        path_type=DubinsPathType.LEFT_STRAIGHT_RIGHT,
        modes=(SegmentMode.LEFT, SegmentMode.STRAIGHT, SegmentMode.RIGHT),
        lengths=(
            _mod_two_pi(-alpha + tangent),
            straight,
            _mod_two_pi(-beta + tangent),
        ),
    )


def _right_straight_left(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    squared = distance**2 - 2 + 2 * cosine_difference - 2 * distance * (sine_alpha + sine_beta)
    if squared < 0:
        return None
    straight = sqrt(squared)
    tangent = atan2(
        cosine_alpha + cosine_beta,
        distance - sine_alpha - sine_beta,
    ) - atan2(2, straight)
    return NormalizedPath(
        path_type=DubinsPathType.RIGHT_STRAIGHT_LEFT,
        modes=(SegmentMode.RIGHT, SegmentMode.STRAIGHT, SegmentMode.LEFT),
        lengths=(
            _mod_two_pi(alpha - tangent),
            straight,
            _mod_two_pi(beta - tangent),
        ),
    )


def _right_left_right(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    cosine_middle = (
        6 - distance**2 + 2 * cosine_difference + 2 * distance * (sine_alpha - sine_beta)
    ) / 8
    if abs(cosine_middle) > 1:
        return None
    middle = _mod_two_pi(2 * pi - acos(_clamp_unit(cosine_middle)))
    first = _mod_two_pi(
        alpha - atan2(cosine_alpha - cosine_beta, distance - sine_alpha + sine_beta) + middle / 2
    )
    return NormalizedPath(
        path_type=DubinsPathType.RIGHT_LEFT_RIGHT,
        modes=(SegmentMode.RIGHT, SegmentMode.LEFT, SegmentMode.RIGHT),
        lengths=(first, middle, _mod_two_pi(alpha - beta - first + middle)),
    )


def _left_right_left(
    alpha: float,
    beta: float,
    distance: float,
) -> NormalizedPath | None:
    sine_alpha = sin(alpha)
    sine_beta = sin(beta)
    cosine_alpha = cos(alpha)
    cosine_beta = cos(beta)
    cosine_difference = cos(alpha - beta)
    cosine_middle = (
        6 - distance**2 + 2 * cosine_difference + 2 * distance * (-sine_alpha + sine_beta)
    ) / 8
    if abs(cosine_middle) > 1:
        return None
    middle = _mod_two_pi(2 * pi - acos(_clamp_unit(cosine_middle)))
    first = _mod_two_pi(
        -alpha - atan2(cosine_alpha - cosine_beta, distance + sine_alpha - sine_beta) + middle / 2
    )
    return NormalizedPath(
        path_type=DubinsPathType.LEFT_RIGHT_LEFT,
        modes=(SegmentMode.LEFT, SegmentMode.RIGHT, SegmentMode.LEFT),
        lengths=(first, middle, _mod_two_pi(beta - alpha - first + middle)),
    )


_PATH_BUILDERS: tuple[PathBuilder, ...] = (
    _left_straight_left,
    _right_straight_right,
    _left_straight_right,
    _right_straight_left,
    _right_left_right,
    _left_right_left,
)


def _sample_path(
    path: NormalizedPath,
    initial_yaw: float,
    direction: float,
    start_x: float,
    start_y: float,
    request: DubinsPathRequest,
    projection: LocalProjection,
) -> list[Coordinate]:
    normalized_points = [(0.0, 0.0)]
    current_x = 0.0
    current_y = 0.0
    current_yaw = initial_yaw
    for mode, length in zip(path.modes, path.lengths, strict=True):
        if length <= 1e-12:
            continue
        sample_count = max(
            1,
            ceil(length * request.minimum_turn_radius_m / request.sampling_step_m),
        )
        increment = length / sample_count
        for _ in range(sample_count):
            current_x, current_y, current_yaw = _advance_pose(
                current_x,
                current_y,
                current_yaw,
                mode,
                increment,
            )
            normalized_points.append((current_x, current_y))
            if len(normalized_points) > 100_000:
                raise RuntimeError("Dubins-траектория требует более 100 000 точек.")
    direction_cosine = cos(direction)
    direction_sine = sin(direction)
    route = [
        projection.unproject(
            start_x + request.minimum_turn_radius_m * (direction_cosine * x - direction_sine * y),
            start_y + request.minimum_turn_radius_m * (direction_sine * x + direction_cosine * y),
        )
        for x, y in normalized_points
    ]
    route[0] = request.start.coordinate
    route[-1] = request.end.coordinate
    return route


def _advance_pose(
    x: float,
    y: float,
    yaw: float,
    mode: SegmentMode,
    distance: float,
) -> tuple[float, float, float]:
    if mode is SegmentMode.STRAIGHT:
        return x + distance * cos(yaw), y + distance * sin(yaw), yaw
    if mode is SegmentMode.LEFT:
        next_yaw = yaw + distance
        return (
            x + sin(next_yaw) - sin(yaw),
            y - cos(next_yaw) + cos(yaw),
            next_yaw,
        )
    next_yaw = yaw - distance
    return (
        x + sin(yaw) - sin(next_yaw),
        y + cos(next_yaw) - cos(yaw),
        next_yaw,
    )


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _mod_two_pi(angle: float) -> float:
    return angle % (2 * pi)
