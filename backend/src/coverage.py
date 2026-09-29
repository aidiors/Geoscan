from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from math import atan2, ceil, degrees, hypot
from typing import Protocol

from shapely.affinity import rotate
from shapely.geometry import GeometryCollection, LineString, MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from src.flight_performance import WindPerformanceError, route_duration_minutes
from src.geometry import LocalProjection, polygon_from_coordinates, projection_for_coordinates
from src.routing import (
    RouteGeometryError,
    build_safe_route,
    coordinate_distance_km,
    route_bearing_degrees,
    route_distance_km,
    route_is_inside_coverage_area,
)
from src.schemas import (
    Coordinate,
    CoverageAlgorithm,
    CoveragePlanRequest,
    CoveragePlanResult,
    ErrorCode,
    NoFlyZone,
    SweepAngleAlgorithm,
    SweepDirectionMode,
    WorkSegment,
)

ROUTE_CLEARANCE_M = 0.5


class SweepEntrySide(StrEnum):
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class CoverageRouteSet:
    primary: list[Coordinate]
    alternatives: list[list[Coordinate]]


@dataclass(frozen=True)
class AnglePlan:
    route_sets: list[CoverageRouteSet]
    angle_degrees: float
    objective_cost: float
    distance_km: float
    turn_count: int


@dataclass(frozen=True, order=True)
class CoverageRouteScore:
    objective_cost: float
    distance_km: float
    turn_count: int


class CoveragePlanner(Protocol):
    @property
    def coverage_algorithm(self) -> CoverageAlgorithm: ...

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult: ...


class CoveragePlanningError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class BoustrophedonCoveragePlanner:
    automatic_angle_algorithm: SweepAngleAlgorithm = SweepAngleAlgorithm.GEOMETRY_AWARE

    @property
    def coverage_algorithm(self) -> CoverageAlgorithm:
        return CoverageAlgorithm.BOUSTROPHEDON_SWEEP

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult:
        sensor = request.parameters.sensor
        footprint_width_m = (
            request.mission_altitude_m * sensor.sensor_width_mm / sensor.focal_length_mm
        )
        footprint_height_m = (
            request.mission_altitude_m * sensor.sensor_height_mm / sensor.focal_length_mm
        )
        computed_gsd_cm = footprint_width_m / sensor.image_width_px * 100
        if computed_gsd_cm > request.parameters.maximum_gsd_cm:
            raise CoveragePlanningError(
                code=ErrorCode.GSD_REQUIREMENT_UNSATISFIED,
                message=(
                    f"Расчётный GSD {computed_gsd_cm:.2f} см/пикс. превышает "
                    f"допустимые {request.parameters.maximum_gsd_cm:.2f} см/пикс."
                ),
            )
        effective_swath_m = min(footprint_width_m, request.parameters.strip_width_m)
        strip_spacing_m = effective_swath_m * (1 - request.parameters.side_overlap_percent / 100)
        trigger_spacing_m = footprint_height_m * (
            1 - request.parameters.forward_overlap_percent / 100
        )
        if strip_spacing_m <= 0 or trigger_spacing_m <= 0:
            raise CoveragePlanningError(
                code=ErrorCode.COVERAGE_GENERATION_FAILED,
                message="Перекрытие не оставляет допустимого шага маршрута или съёмки.",
            )
        survey_speed_limit_kmh = trigger_spacing_m / sensor.minimum_trigger_interval_s * 3.6
        angle_plan, angle_algorithm = _select_angle_plan(
            request,
            strip_spacing_m,
            request.parameters.maximum_segment_distance_km,
            survey_speed_limit_kmh,
            self.automatic_angle_algorithm,
        )
        segments = [
            _work_segment(
                request,
                route_set.primary,
                route_set.alternatives,
                index,
                computed_gsd_cm,
                strip_spacing_m,
                trigger_spacing_m,
                survey_speed_limit_kmh,
                angle_plan.angle_degrees,
            )
            for index, route_set in enumerate(angle_plan.route_sets)
        ]
        return CoveragePlanResult(
            segments=segments,
            computed_gsd_cm=round(computed_gsd_cm, 3),
            footprint_width_m=round(footprint_width_m, 3),
            footprint_height_m=round(footprint_height_m, 3),
            strip_spacing_m=round(strip_spacing_m, 3),
            trigger_spacing_m=round(trigger_spacing_m, 3),
            selected_flight_direction_degrees=round(angle_plan.angle_degrees, 3),
            angle_algorithm=angle_algorithm,
        )


def _select_angle_plan(
    request: CoveragePlanRequest,
    strip_spacing_m: float,
    maximum_distance_km: float,
    survey_speed_limit_kmh: float,
    automatic_algorithm: SweepAngleAlgorithm,
) -> tuple[AnglePlan, SweepAngleAlgorithm]:
    if request.parameters.direction_mode is SweepDirectionMode.MANUAL:
        selected = _best_angle_plan(
            request,
            strip_spacing_m,
            maximum_distance_km,
            survey_speed_limit_kmh,
            [request.parameters.flight_direction_degrees],
        )
        return selected, SweepAngleAlgorithm.FIXED_INPUT
    if automatic_algorithm is SweepAngleAlgorithm.GEOMETRY_AWARE:
        selected_algorithm = _geometry_aware_angle_algorithm(request)
        selected, _ = _select_angle_plan(
            request,
            strip_spacing_m,
            maximum_distance_km,
            survey_speed_limit_kmh,
            selected_algorithm,
        )
        return selected, automatic_algorithm
    if automatic_algorithm is SweepAngleAlgorithm.COARSE_TO_FINE:
        coarse_angles = _candidate_angles(request, SweepAngleAlgorithm.GRID_10_DEGREES)
        coarse_angles.extend(_candidate_angles(request, SweepAngleAlgorithm.CONVEX_HULL_EDGES))
        coarse = _best_angle_plan(
            request,
            strip_spacing_m,
            maximum_distance_km,
            survey_speed_limit_kmh,
            coarse_angles,
        )
        refined_angles = [coarse.angle_degrees + offset for offset in range(-9, 10)]
        refined_angles.extend(coarse_angles)
        refined = _best_angle_plan(
            request,
            strip_spacing_m,
            maximum_distance_km,
            survey_speed_limit_kmh,
            refined_angles,
        )
        return refined, automatic_algorithm
    angles = _candidate_angles(request, automatic_algorithm)
    selected = _best_angle_plan(
        request,
        strip_spacing_m,
        maximum_distance_km,
        survey_speed_limit_kmh,
        angles,
    )
    return selected, automatic_algorithm


def _best_angle_plan(
    request: CoveragePlanRequest,
    strip_spacing_m: float,
    maximum_distance_km: float,
    survey_speed_limit_kmh: float,
    angles: list[float],
) -> AnglePlan:
    best: AnglePlan | None = None
    for angle in _unique_angles(angles):
        try:
            route_sets = _coverage_route_sets(
                request,
                strip_spacing_m,
                maximum_distance_km,
                angle,
            )
        except CoveragePlanningError:
            continue
        score = _route_sets_score(
            route_sets,
            request,
            _scoring_exclusions(request),
            survey_speed_limit_kmh,
        )
        if score is None:
            continue
        candidate = AnglePlan(
            route_sets=route_sets,
            angle_degrees=angle,
            objective_cost=score.objective_cost,
            distance_km=score.distance_km,
            turn_count=score.turn_count,
        )
        if best is None or _angle_plan_is_better(candidate, best, request):
            best = candidate
    if best is not None:
        return best
    for angle in _unique_angles(angles):
        try:
            route_sets = _coverage_route_sets(
                request,
                strip_spacing_m,
                maximum_distance_km,
                angle,
                split_disconnected_transitions=True,
            )
        except CoveragePlanningError:
            continue
        score = _route_sets_score(
            route_sets,
            request,
            _scoring_exclusions(request),
            survey_speed_limit_kmh,
        )
        if score is None:
            continue
        candidate = AnglePlan(
            route_sets=route_sets,
            angle_degrees=angle,
            objective_cost=score.objective_cost,
            distance_km=score.distance_km,
            turn_count=score.turn_count,
        )
        if best is None or _angle_plan_is_better(candidate, best, request):
            best = candidate
    if best is not None:
        return best
    raise CoveragePlanningError(
        code=ErrorCode.COVERAGE_GENERATION_FAILED,
        message=f"Для целевой зоны {request.area.name} не найден допустимый угол галсов.",
    )


def _candidate_angles(
    request: CoveragePlanRequest,
    algorithm: SweepAngleAlgorithm,
) -> list[float]:
    if algorithm is SweepAngleAlgorithm.FIXED_INPUT:
        return [request.parameters.flight_direction_degrees]
    if algorithm is SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE:
        geometry, _ = _flyable_geometry(request)
        return [_minimum_rectangle_angle(geometry)]
    if algorithm is SweepAngleAlgorithm.MINIMUM_RECTANGLE_REFINED:
        geometry, _ = _flyable_geometry(request)
        rectangle_angle = _minimum_rectangle_angle(geometry)
        return [rectangle_angle + offset for offset in range(-5, 6)]
    if algorithm is SweepAngleAlgorithm.CONVEX_HULL_EDGES:
        geometry, _ = _flyable_geometry(request)
        return _convex_hull_edge_angles(geometry)
    grid_steps = {
        SweepAngleAlgorithm.GRID_30_DEGREES: 30,
        SweepAngleAlgorithm.GRID_15_DEGREES: 15,
        SweepAngleAlgorithm.GRID_10_DEGREES: 10,
        SweepAngleAlgorithm.GRID_5_DEGREES: 5,
        SweepAngleAlgorithm.EXHAUSTIVE_1_DEGREE: 1,
    }
    step = grid_steps.get(algorithm)
    if step is None:
        raise ValueError(f"Алгоритм угла {algorithm} требует отдельной стратегии поиска.")
    return [float(angle) for angle in range(0, 180, step)]


def _unique_angles(angles: list[float]) -> list[float]:
    return sorted({round(angle % 180, 6) for angle in angles})


def _geometry_aware_angle_algorithm(request: CoveragePlanRequest) -> SweepAngleAlgorithm:
    if _uses_wind_adjusted_cost(request):
        return SweepAngleAlgorithm.COARSE_TO_FINE
    geometry, _ = _flyable_geometry(request)
    rectangle = geometry.minimum_rotated_rectangle
    if rectangle.area <= 0:
        return SweepAngleAlgorithm.COARSE_TO_FINE
    rectangularity = float(geometry.area / rectangle.area)
    if rectangularity >= 0.92 and not request.exclusions and not request.area.interior_rings:
        return SweepAngleAlgorithm.MINIMUM_RECTANGLE_REFINED
    return SweepAngleAlgorithm.COARSE_TO_FINE


def _minimum_rectangle_angle(geometry: BaseGeometry) -> float:
    rectangle = geometry.convex_hull.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    edges = list(pairwise(coordinates))
    start, end = max(
        edges,
        key=lambda edge: hypot(edge[1][0] - edge[0][0], edge[1][1] - edge[0][1]),
    )
    return _compass_angle(float(end[0] - start[0]), float(end[1] - start[1]))


def _convex_hull_edge_angles(geometry: BaseGeometry) -> list[float]:
    coordinates = list(geometry.convex_hull.exterior.coords)
    return [
        _compass_angle(float(end[0] - start[0]), float(end[1] - start[1]))
        for start, end in pairwise(coordinates)
        if start != end
    ]


def _compass_angle(delta_x: float, delta_y: float) -> float:
    return degrees(atan2(delta_x, delta_y)) % 180


def _angle_plan_is_better(
    candidate: AnglePlan,
    incumbent: AnglePlan,
    request: CoveragePlanRequest,
) -> bool:
    objective_tolerance = 0.1 if _uses_wind_adjusted_cost(request) else 0.0
    objective_difference = candidate.objective_cost - incumbent.objective_cost
    if abs(objective_difference) > objective_tolerance:
        return objective_difference < 0
    candidate_tiebreaker = (
        candidate.distance_km,
        candidate.turn_count,
        candidate.angle_degrees,
    )
    incumbent_tiebreaker = (
        incumbent.distance_km,
        incumbent.turn_count,
        incumbent.angle_degrees,
    )
    return candidate_tiebreaker < incumbent_tiebreaker


def _route_sets_score(
    route_sets: list[CoverageRouteSet],
    request: CoveragePlanRequest,
    exclusions: list[NoFlyZone],
    survey_speed_limit_kmh: float,
) -> CoverageRouteScore | None:
    total_objective_cost = 0.0
    total_distance_km = 0.0
    total_turns = 0
    for route_set in route_sets:
        choices: list[CoverageRouteScore] = []
        for route in [route_set.primary, *route_set.alternatives]:
            for oriented_route in [route, list(reversed(route))]:
                choice = _route_choice_score(
                    oriented_route,
                    request,
                    exclusions,
                    survey_speed_limit_kmh,
                )
                if choice is not None:
                    choices.append(choice)
        if not choices:
            return None
        selected = min(choices)
        total_objective_cost += selected.objective_cost
        total_distance_km += selected.distance_km
        total_turns += selected.turn_count
    return CoverageRouteScore(total_objective_cost, total_distance_km, total_turns)


def _route_choice_score(
    route: list[Coordinate],
    request: CoveragePlanRequest,
    exclusions: list[NoFlyZone],
    survey_speed_limit_kmh: float,
) -> CoverageRouteScore | None:
    safe_route = _safe_route_or_none(route, exclusions)
    if safe_route is None:
        return None
    if not route_is_inside_coverage_area(safe_route, request.area):
        return None
    departure_score = _best_connection_score(
        [[site, safe_route[0]] for site in request.departure_candidates],
        request,
        exclusions,
    )
    if departure_score is None:
        return None
    landing_score = _best_connection_score(
        [[safe_route[-1], site] for site in request.landing_candidates],
        request,
        exclusions,
    )
    if landing_score is None:
        return None
    survey_score = _route_performance_score(
        safe_route,
        request,
        survey_speed_limit_kmh,
        _turn_count(safe_route),
    )
    if survey_score is None:
        return None
    return CoverageRouteScore(
        departure_score.objective_cost + survey_score.objective_cost + landing_score.objective_cost,
        departure_score.distance_km + survey_score.distance_km + landing_score.distance_km,
        survey_score.turn_count,
    )


def _safe_route_or_none(
    route: list[Coordinate],
    exclusions: list[NoFlyZone],
) -> list[Coordinate] | None:
    try:
        return build_safe_route(route, exclusions)
    except RouteGeometryError:
        return None


def _best_connection_score(
    routes: list[list[Coordinate]],
    request: CoveragePlanRequest,
    exclusions: list[NoFlyZone],
) -> CoverageRouteScore | None:
    if not routes:
        return CoverageRouteScore(0, 0, 0)
    scores: list[CoverageRouteScore] = []
    for route in routes:
        safe_route = _safe_route_or_none(route, exclusions)
        if safe_route is None:
            continue
        score = _route_performance_score(safe_route, request, None, 0)
        if score is not None:
            scores.append(score)
    return min(scores) if scores else None


def _route_performance_score(
    route: list[Coordinate],
    request: CoveragePlanRequest,
    ground_speed_limit_kmh: float | None,
    turn_count: int,
) -> CoverageRouteScore | None:
    distance_km = route_distance_km(route)
    if not _uses_wind_adjusted_cost(request):
        return CoverageRouteScore(distance_km, distance_km, turn_count)
    reference_airspeed_kmh = request.reference_airspeed_kmh
    if reference_airspeed_kmh is None:
        return None
    try:
        duration_minutes = route_duration_minutes(
            route,
            distance_km,
            reference_airspeed_kmh,
            request.wind,
            ground_speed_limit_kmh,
        )
    except WindPerformanceError:
        return None
    return CoverageRouteScore(duration_minutes, distance_km, turn_count)


def _uses_wind_adjusted_cost(request: CoveragePlanRequest) -> bool:
    return (
        request.wind is not None
        and request.wind.speed_mps > 0
        and request.reference_airspeed_kmh is not None
    )


def _turn_count(route: list[Coordinate]) -> int:
    headings = [route_bearing_degrees(start, end) for start, end in pairwise(route) if start != end]
    return sum(
        min(abs(end - start), 360 - abs(end - start)) > 5 for start, end in pairwise(headings)
    )


def _scoring_exclusions(request: CoveragePlanRequest) -> list[NoFlyZone]:
    holes = [
        NoFlyZone(
            zone_id=f"coverage-hole-{index + 1}",
            coordinates=ring,
            safety_margin_m=ROUTE_CLEARANCE_M,
        )
        for index, ring in enumerate(request.area.interior_rings)
        if len(ring) >= 3
    ]
    return [*request.exclusions, *holes]


def _coverage_route_sets(
    request: CoveragePlanRequest,
    strip_spacing_m: float,
    maximum_distance_km: float,
    angle_degrees: float,
    split_disconnected_transitions: bool = False,
) -> list[CoverageRouteSet]:
    flyable, projection = _flyable_geometry(request)
    rotation = angle_degrees - 90
    rotated = rotate(flyable, rotation, origin="centroid")
    center = (float(flyable.centroid.x), float(flyable.centroid.y))
    rows = _sweep_rows(rotated, strip_spacing_m)
    if not rows:
        raise CoveragePlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Для целевой зоны {request.area.name} не удалось построить галсы.",
        )
    route_sets: list[CoverageRouteSet] = []
    for batch in _split_sweep_rows(rows, maximum_distance_km):
        primary_xy = _route_from_rows(batch, SweepEntrySide.LEFT)
        alternative_xy = _route_from_rows(batch, SweepEntrySide.RIGHT)
        if not split_disconnected_transitions:
            primary = _unproject_route(primary_xy, rotation, center, projection)
            alternative = _unproject_route(alternative_xy, rotation, center, projection)
            if (
                max(
                    _projected_distance_km(primary_xy),
                    _projected_distance_km(alternative_xy),
                )
                <= maximum_distance_km
            ):
                alternatives = [alternative] if alternative != primary else []
                route_sets.append(CoverageRouteSet(primary=primary, alternatives=alternatives))
                continue
            route_sets.extend(
                CoverageRouteSet(primary=chunk, alternatives=[])
                for chunk in _split_route(primary, maximum_distance_km)
            )
            continue
        for primary_part, alternative_part in _sweep_cell_route_pairs(batch, rotated):
            primary = _unproject_route(primary_part, rotation, center, projection)
            alternative = _unproject_route(alternative_part, rotation, center, projection)
            if (
                max(
                    _projected_distance_km(primary_part),
                    _projected_distance_km(alternative_part),
                )
                <= maximum_distance_km
            ):
                alternatives = [alternative] if alternative != primary else []
                route_sets.append(CoverageRouteSet(primary=primary, alternatives=alternatives))
                continue
            route_sets.extend(
                CoverageRouteSet(primary=chunk, alternatives=[])
                for chunk in _split_route(primary, maximum_distance_km)
            )
    return route_sets


def _sweep_cell_route_pairs(
    rows: list[list[LineString]],
    flyable: BaseGeometry,
) -> list[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    tracks: list[list[LineString]] = []
    previous_row: list[LineString] = []
    previous_track_indices: list[int] = []
    for row in rows:
        previous_neighbors = [
            [index for index, current in enumerate(row) if _x_ranges_overlap(previous, current)]
            for previous in previous_row
        ]
        current_neighbors = [
            [
                index
                for index, previous in enumerate(previous_row)
                if _x_ranges_overlap(previous, current)
            ]
            for current in row
        ]
        current_track_indices: list[int] = []
        for current_index, line in enumerate(row):
            neighbors = current_neighbors[current_index]
            if len(neighbors) == 1 and len(previous_neighbors[neighbors[0]]) == 1:
                track_index = previous_track_indices[neighbors[0]]
                tracks[track_index].append(line)
            else:
                track_index = len(tracks)
                tracks.append([line])
            current_track_indices.append(track_index)
        previous_row = row
        previous_track_indices = current_track_indices
    pairs: list[tuple[list[tuple[float, float]], list[tuple[float, float]]]] = []
    for track in tracks:
        track_rows = [[line] for line in track]
        primary = _route_from_rows(track_rows, SweepEntrySide.LEFT)
        alternative = _route_from_rows(track_rows, SweepEntrySide.RIGHT)
        primary_parts = _split_at_disconnected_sweep_transitions(primary, flyable)
        alternative_parts = _split_at_disconnected_sweep_transitions(alternative, flyable)
        if len(primary_parts) == 1 and len(alternative_parts) == 1:
            pairs.append((primary, alternative))
        else:
            pairs.extend((part, list(reversed(part))) for part in primary_parts)
    return pairs


def _x_ranges_overlap(first: LineString, second: LineString) -> bool:
    return max(first.bounds[0], second.bounds[0]) <= min(first.bounds[2], second.bounds[2])


def _split_at_disconnected_sweep_transitions(
    route: list[tuple[float, float]],
    flyable: BaseGeometry,
) -> list[list[tuple[float, float]]]:
    survey_legs = [route[index : index + 2] for index in range(0, len(route), 2)]
    if not survey_legs:
        return []
    covered_geometry = flyable.buffer(0.05)
    parts = [list(survey_legs[0])]
    for leg in survey_legs[1:]:
        connector = LineString([parts[-1][-1], leg[0]])
        if covered_geometry.covers(connector):
            parts[-1].extend(leg)
        else:
            parts.append(list(leg))
    return parts


def _flyable_geometry(request: CoveragePlanRequest) -> tuple[BaseGeometry, LocalProjection]:
    all_coordinates = [
        *request.area.coordinates,
        *(point for ring in request.area.interior_rings for point in ring),
        *(point for zone in request.exclusions for point in zone.coordinates),
    ]
    projection = projection_for_coordinates(all_coordinates)
    target = polygon_from_coordinates(
        request.area.coordinates,
        request.area.interior_rings,
        projection,
    )
    exclusion_geometries = [
        polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
            zone.safety_margin_m + ROUTE_CLEARANCE_M,
            join_style="mitre",
        )
        for zone in request.exclusions
    ]
    hole_geometries = [
        polygon_from_coordinates(ring, [], projection).buffer(
            ROUTE_CLEARANCE_M,
            join_style="mitre",
        )
        for ring in request.area.interior_rings
        if len(ring) >= 3
    ]
    blocked_geometries = [*exclusion_geometries, *hole_geometries]
    flyable = target.difference(unary_union(blocked_geometries)) if blocked_geometries else target
    if flyable.is_empty:
        raise CoveragePlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Целевая зона {request.area.name} полностью закрыта ограничениями.",
        )
    return flyable, projection


def _sweep_rows(geometry: BaseGeometry, spacing_m: float) -> list[list[LineString]]:
    minimum_x, minimum_y, maximum_x, maximum_y = geometry.bounds
    span_y = maximum_y - minimum_y
    if span_y <= 0:
        return []
    y_values: list[float] = []
    if span_y <= spacing_m:
        y_values.append((minimum_y + maximum_y) / 2)
    else:
        y = minimum_y + spacing_m / 2
        while y < maximum_y:
            y_values.append(y)
            y += spacing_m
    if len(y_values) > 10_000:
        raise CoveragePlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message="Для выбранных параметров требуется более 10 000 галсов.",
        )
    rows: list[list[LineString]] = []
    for y in y_values:
        scanline = LineString([(minimum_x - spacing_m, y), (maximum_x + spacing_m, y)])
        row_segments = _line_parts(geometry.intersection(scanline))
        ordered = sorted(row_segments, key=lambda line: line.bounds[0])
        if ordered:
            rows.append(ordered)
    return rows


def _route_from_rows(
    rows: list[list[LineString]],
    entry_side: SweepEntrySide,
) -> list[tuple[float, float]]:
    route: list[tuple[float, float]] = []
    for row_index, row in enumerate(rows):
        left_to_right = (row_index % 2 == 0) == (entry_side is SweepEntrySide.LEFT)
        lines = row if left_to_right else list(reversed(row))
        for line in lines:
            coordinates = list(line.coords)
            oriented = coordinates if left_to_right else list(reversed(coordinates))
            route.extend((float(x), float(y)) for x, y in oriented)
    return route


def _split_sweep_rows(
    rows: list[list[LineString]],
    maximum_distance_km: float,
) -> list[list[list[LineString]]]:
    batches: list[list[list[LineString]]] = []
    current: list[list[LineString]] = []
    for row in rows:
        candidate = [*current, row]
        candidate_distance = max(
            _projected_distance_km(_route_from_rows(candidate, SweepEntrySide.LEFT)),
            _projected_distance_km(_route_from_rows(candidate, SweepEntrySide.RIGHT)),
        )
        if current and candidate_distance > maximum_distance_km:
            batches.append(current)
            current = [row]
            continue
        current = candidate
    if current:
        batches.append(current)
    return batches


def _projected_distance_km(route: list[tuple[float, float]]) -> float:
    return (
        sum(hypot(end[0] - start[0], end[1] - start[1]) for start, end in pairwise(route)) / 1_000
    )


def _unproject_route(
    route: list[tuple[float, float]],
    rotation: float,
    center: tuple[float, float],
    projection: LocalProjection,
) -> list[Coordinate]:
    unrotated = rotate(LineString(route), -rotation, origin=center)
    return [projection.unproject(float(x), float(y)) for x, y in unrotated.coords]


def _line_parts(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 0.01 else []
    if isinstance(geometry, MultiLineString):
        return [line for line in geometry.geoms if line.length > 0.01]
    if isinstance(geometry, GeometryCollection):
        return [
            part for part in geometry.geoms if isinstance(part, LineString) and part.length > 0.01
        ]
    return []


def _split_route(route: list[Coordinate], maximum_distance_km: float) -> list[list[Coordinate]]:
    chunks: list[list[Coordinate]] = []
    current = [route[0]]
    current_distance = 0.0
    for coordinate in route[1:]:
        leg_distance = coordinate_distance_km(current[-1], coordinate)
        if len(current) >= 2 and current_distance + leg_distance > maximum_distance_km:
            chunks.append(current)
            current = [current[-1], coordinate]
            current_distance = leg_distance
        else:
            current.append(coordinate)
            current_distance += leg_distance
    if len(current) >= 2:
        chunks.append(current)
    return chunks


def _work_segment(
    request: CoveragePlanRequest,
    route: list[Coordinate],
    alternative_routes: list[list[Coordinate]],
    index: int,
    computed_gsd_cm: float,
    strip_spacing_m: float,
    trigger_spacing_m: float,
    survey_speed_limit_kmh: float,
    flight_direction_degrees: float,
) -> WorkSegment:
    distance_km = sum(coordinate_distance_km(start, end) for start, end in pairwise(route))
    image_count = ceil(distance_km * 1_000 / trigger_spacing_m) + 1
    return WorkSegment(
        segment_id=f"{request.area.area_id}-{index + 1}",
        distance_km=round(distance_km, 6),
        route=route,
        alternative_routes=alternative_routes,
        coverage_area_id=request.area.area_id,
        image_count=image_count,
        computed_gsd_cm=round(computed_gsd_cm, 3),
        strip_spacing_m=round(strip_spacing_m, 3),
        survey_speed_limit_kmh=round(survey_speed_limit_kmh, 3),
        flight_direction_degrees=round(flight_direction_degrees, 3),
    )
