from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from pydantic import ValidationError

from benchmarks.models import CoverageBenchmarkCase, CoverageBenchmarkResult
from geoplan.coverage.adaptive import AdaptiveCoveragePlanner
from geoplan.coverage.decomposition import automatic_partition_requests
from geoplan.coverage.models import CoveragePlanRequest, CoveragePlanResult
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner, CoveragePlanningError
from geoplan.domain.enums import SweepAngleAlgorithm
from geoplan.domain.geo import Coordinate
from geoplan.navigation.curvature import (
    DubinsSweepTransitionPlanner,
    HybridAStarSweepTransitionPlanner,
    SweepTransitionPlanner,
    build_curvature_aware_sweep_route_with_planner,
)
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.hybrid_astar import HybridAStarPlanner
from geoplan.navigation.models import (
    SectionRouteOptimizationRequest,
    SectionRouteVariant,
    SweepRouteCurvatureRequest,
    SweepRouteCurvatureResult,
    TurnAwareSweepRequest,
    TurnAwareSweepResult,
)
from geoplan.navigation.routing import RouteGeometryError, route_is_clear_of_no_fly_zones
from geoplan.navigation.sections import (
    DubinsSectionTransitionPlanner,
    ExactSectionRouteOptimizer,
    HybridAStarSectionTransitionPlanner,
)
from geoplan.navigation.turn_aware import build_turn_aware_sweep_route

BENCHMARK_ALGORITHMS = [
    SweepAngleAlgorithm.FIXED_INPUT,
    SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE,
    SweepAngleAlgorithm.MINIMUM_RECTANGLE_REFINED,
    SweepAngleAlgorithm.CONVEX_HULL_EDGES,
    SweepAngleAlgorithm.GRID_30_DEGREES,
    SweepAngleAlgorithm.GRID_15_DEGREES,
    SweepAngleAlgorithm.GRID_10_DEGREES,
    SweepAngleAlgorithm.GRID_5_DEGREES,
    SweepAngleAlgorithm.COARSE_TO_FINE,
    SweepAngleAlgorithm.EXHAUSTIVE_1_DEGREE,
    SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE,
    SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
    SweepAngleAlgorithm.ADAPTIVE_SECTION_DP,
    SweepAngleAlgorithm.ADAPTIVE_SECTION_DUBINS,
    SweepAngleAlgorithm.ADAPTIVE_FULL_DUBINS,
    SweepAngleAlgorithm.ADAPTIVE_HYBRID_ASTAR,
    SweepAngleAlgorithm.ADAPTIVE_TURN_AWARE,
    SweepAngleAlgorithm.PRODUCTION_ADAPTIVE,
    SweepAngleAlgorithm.GEOMETRY_AWARE,
]


SECTION_VARIANT_ALGORITHMS = [
    SweepAngleAlgorithm.GEOMETRY_AWARE,
    SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE,
    SweepAngleAlgorithm.CONVEX_HULL_EDGES,
    SweepAngleAlgorithm.GRID_15_DEGREES,
]


OPTIMISTIC_LOWER_BOUND_ALGORITHMS = {
    SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE,
    SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
}


@dataclass(frozen=True)
class MeasuredCoveragePlan:
    algorithm: SweepAngleAlgorithm
    selected_angle_degrees: float | None
    route_distance_km: float | None
    turn_count: int | None
    segment_count: int
    runtime_ms: float
    geometry_valid: bool
    error_message: str | None


@dataclass(frozen=True)
class PlannedSectionGroups:
    variant_groups: list[list[SectionRouteVariant]]
    selected_angles: set[float]


class SectionVariantFactory(Protocol):
    def build(
        self,
        section_id: str,
        routes: list[list[Coordinate]],
        request: CoveragePlanRequest,
    ) -> list[SectionRouteVariant]: ...


@dataclass(frozen=True)
class PolylineSectionVariantFactory:
    def build(
        self,
        section_id: str,
        routes: list[list[Coordinate]],
        request: CoveragePlanRequest,
    ) -> list[SectionRouteVariant]:
        return _section_variants(section_id, routes)


@dataclass(frozen=True)
class CurvatureSectionVariantFactory:
    minimum_turn_radius_m: float
    sampling_step_m: float
    transition_planner: SweepTransitionPlanner

    def build(
        self,
        section_id: str,
        routes: list[list[Coordinate]],
        request: CoveragePlanRequest,
    ) -> list[SectionRouteVariant]:
        return _curvature_section_variants(section_id, routes, request, self)

    def build_curve(
        self,
        route: list[Coordinate],
        request: CoveragePlanRequest,
    ) -> SweepRouteCurvatureResult:
        curvature_request = SweepRouteCurvatureRequest(
            route=route,
            coverage_area=request.area,
            no_fly_zones=request.exclusions,
            minimum_turn_radius_m=self.minimum_turn_radius_m,
            sampling_step_m=self.sampling_step_m,
        )
        return build_curvature_aware_sweep_route_with_planner(
            curvature_request,
            self.transition_planner,
        )


class CurvedSectionVariantFactory(SectionVariantFactory, Protocol):
    def build_curve(
        self,
        route: list[Coordinate],
        request: CoveragePlanRequest,
    ) -> SweepRouteCurvatureResult | TurnAwareSweepResult: ...


@dataclass(frozen=True)
class TurnAwareSectionVariantFactory:
    minimum_turn_radius_m: float
    sampling_step_m: float
    transition_planner: SweepTransitionPlanner

    def build(
        self,
        section_id: str,
        routes: list[list[Coordinate]],
        request: CoveragePlanRequest,
    ) -> list[SectionRouteVariant]:
        return _curvature_section_variants(section_id, routes, request, self)

    def build_curve(
        self,
        route: list[Coordinate],
        request: CoveragePlanRequest,
    ) -> TurnAwareSweepResult:
        sensor = request.parameters.sensor
        footprint_width_m = (
            request.mission_altitude_m * sensor.sensor_width_mm / sensor.focal_length_mm
        )
        footprint_height_m = (
            request.mission_altitude_m * sensor.sensor_height_mm / sensor.focal_length_mm
        )
        turn_request = TurnAwareSweepRequest(
            route=route,
            coverage_area=request.area,
            no_fly_zones=request.exclusions,
            minimum_turn_radius_m=self.minimum_turn_radius_m,
            sampling_step_m=self.sampling_step_m,
            strip_width_m=min(footprint_width_m, request.parameters.strip_width_m),
            footprint_height_m=footprint_height_m,
        )
        return build_turn_aware_sweep_route(turn_request, self.transition_planner)


def run_coverage_benchmark(
    cases: list[CoverageBenchmarkCase],
) -> list[CoverageBenchmarkResult]:
    results: list[CoverageBenchmarkResult] = []
    for case in cases:
        measurements = [_measure(case, algorithm) for algorithm in BENCHMARK_ALGORITHMS]
        distances = [
            item.route_distance_km
            for item in measurements
            if item.route_distance_km is not None
            and item.algorithm not in OPTIMISTIC_LOWER_BOUND_ALGORITHMS
        ]
        if not distances:
            raise RuntimeError(f"Сценарий {case.case_id} не решил ни один алгоритм.")
        reference_distance = min(distances)
        results.extend(
            CoverageBenchmarkResult(
                case_id=case.case_id,
                algorithm=item.algorithm,
                selected_angle_degrees=(
                    round(item.selected_angle_degrees, 3)
                    if item.selected_angle_degrees is not None
                    else None
                ),
                route_distance_km=(
                    round(item.route_distance_km, 3) if item.route_distance_km is not None else None
                ),
                turn_count=item.turn_count,
                segment_count=item.segment_count,
                runtime_ms=round(item.runtime_ms, 3),
                gap_to_reference_percent=(
                    round(
                        max(0, (item.route_distance_km / reference_distance - 1) * 100),
                        3,
                    )
                    if item.route_distance_km is not None
                    and item.algorithm not in OPTIMISTIC_LOWER_BOUND_ALGORITHMS
                    else None
                ),
                geometry_valid=item.geometry_valid,
                error_message=item.error_message,
            )
            for item in measurements
        )
    return results


def _measure(
    case: CoverageBenchmarkCase,
    algorithm: SweepAngleAlgorithm,
) -> MeasuredCoveragePlan:
    if algorithm is SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE:
        return _measure_partitioned(case)
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION:
        return _measure_adaptive(case)
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_SECTION_DP:
        return _measure_adaptive_sections(
            case,
            algorithm,
            ExactSectionRouteOptimizer(),
            PolylineSectionVariantFactory(),
        )
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_SECTION_DUBINS:
        return _measure_adaptive_sections(
            case,
            algorithm,
            ExactSectionRouteOptimizer(
                transition_planner=DubinsSectionTransitionPlanner(
                    minimum_turn_radius_m=30,
                    sampling_step_m=3,
                )
            ),
            PolylineSectionVariantFactory(),
        )
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_FULL_DUBINS:
        return _measure_adaptive_sections(
            case,
            algorithm,
            ExactSectionRouteOptimizer(
                transition_planner=DubinsSectionTransitionPlanner(
                    minimum_turn_radius_m=30,
                    sampling_step_m=3,
                )
            ),
            CurvatureSectionVariantFactory(
                minimum_turn_radius_m=30,
                sampling_step_m=3,
                transition_planner=DubinsSweepTransitionPlanner(planner=ExactDubinsPlanner()),
            ),
        )
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_HYBRID_ASTAR:
        hybrid_planner = HybridAStarPlanner(dubins_planner=ExactDubinsPlanner())
        return _measure_adaptive_sections(
            case,
            algorithm,
            ExactSectionRouteOptimizer(
                transition_planner=HybridAStarSectionTransitionPlanner(
                    planner=hybrid_planner,
                    minimum_turn_radius_m=30,
                    primitive_length_m=15,
                    sampling_step_m=3,
                    grid_resolution_m=8,
                    heading_bin_degrees=15,
                    analytic_expansion_distance_m=180,
                    maximum_expansions=20_000,
                )
            ),
            CurvatureSectionVariantFactory(
                minimum_turn_radius_m=30,
                sampling_step_m=3,
                transition_planner=HybridAStarSweepTransitionPlanner(
                    planner=hybrid_planner,
                    primitive_length_m=15,
                    grid_resolution_m=8,
                    heading_bin_degrees=15,
                    analytic_expansion_distance_m=180,
                    maximum_expansions=20_000,
                ),
            ),
        )
    if algorithm is SweepAngleAlgorithm.ADAPTIVE_TURN_AWARE:
        hybrid_planner = HybridAStarPlanner(dubins_planner=ExactDubinsPlanner())
        return _measure_adaptive_sections(
            case,
            algorithm,
            ExactSectionRouteOptimizer(
                transition_planner=HybridAStarSectionTransitionPlanner(
                    planner=hybrid_planner,
                    minimum_turn_radius_m=30,
                    primitive_length_m=15,
                    sampling_step_m=3,
                    grid_resolution_m=8,
                    heading_bin_degrees=15,
                    analytic_expansion_distance_m=180,
                    maximum_expansions=20_000,
                )
            ),
            TurnAwareSectionVariantFactory(
                minimum_turn_radius_m=30,
                sampling_step_m=3,
                transition_planner=HybridAStarSweepTransitionPlanner(
                    planner=hybrid_planner,
                    primitive_length_m=15,
                    grid_resolution_m=8,
                    heading_bin_degrees=15,
                    analytic_expansion_distance_m=180,
                    maximum_expansions=20_000,
                ),
            ),
        )
    if algorithm is SweepAngleAlgorithm.PRODUCTION_ADAPTIVE:
        return _measure_production_adaptive(case)
    return _measure_request(case.request, algorithm)


def _measure_production_adaptive(
    case: CoverageBenchmarkCase,
) -> MeasuredCoveragePlan:
    started = perf_counter()
    try:
        result = AdaptiveCoveragePlanner().plan(case.request)
    except CoveragePlanningError as error:
        return _failed_section_measurement(
            started,
            SweepAngleAlgorithm.PRODUCTION_ADAPTIVE,
            error.message,
        )
    return _measure_planned_result(
        case.request,
        SweepAngleAlgorithm.PRODUCTION_ADAPTIVE,
        result,
        started,
    )


def _measure_partitioned(case: CoverageBenchmarkCase) -> MeasuredCoveragePlan:
    requests = case.partitioned_requests or [case.request]
    started = perf_counter()
    parts = [_measure_request(request, SweepAngleAlgorithm.GEOMETRY_AWARE) for request in requests]
    failed = next((part for part in parts if part.route_distance_km is None), None)
    if failed is not None:
        return MeasuredCoveragePlan(
            algorithm=SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message=failed.error_message,
        )
    distances = [part.route_distance_km for part in parts if part.route_distance_km is not None]
    turns = [part.turn_count for part in parts if part.turn_count is not None]
    angles = {
        part.selected_angle_degrees for part in parts if part.selected_angle_degrees is not None
    }
    return MeasuredCoveragePlan(
        algorithm=SweepAngleAlgorithm.PARTITIONED_MULTI_ANGLE,
        selected_angle_degrees=next(iter(angles)) if len(angles) == 1 else None,
        route_distance_km=sum(distances),
        turn_count=sum(turns),
        segment_count=sum(part.segment_count for part in parts),
        runtime_ms=(perf_counter() - started) * 1_000,
        geometry_valid=all(part.geometry_valid for part in parts),
        error_message=None,
    )


def _measure_adaptive(case: CoverageBenchmarkCase) -> MeasuredCoveragePlan:
    started = perf_counter()
    global_plan = _measure_request(case.request, SweepAngleAlgorithm.GEOMETRY_AWARE)
    partitions = automatic_partition_requests(case.request)
    if len(partitions) == 1:
        return MeasuredCoveragePlan(
            algorithm=SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
            selected_angle_degrees=global_plan.selected_angle_degrees,
            route_distance_km=global_plan.route_distance_km,
            turn_count=global_plan.turn_count,
            segment_count=global_plan.segment_count,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=global_plan.geometry_valid,
            error_message=global_plan.error_message,
        )
    partitioned_case = case.model_copy(update={"partitioned_requests": partitions})
    partitioned_plan = _measure_partitioned(partitioned_case)
    candidates = [
        plan
        for plan in [global_plan, partitioned_plan]
        if plan.route_distance_km is not None and plan.geometry_valid
    ]
    if not candidates:
        message = partitioned_plan.error_message or global_plan.error_message
        return MeasuredCoveragePlan(
            algorithm=SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message=message or "Автоматическая декомпозиция не нашла допустимый маршрут.",
        )
    selected = min(candidates, key=_measured_plan_key)
    return MeasuredCoveragePlan(
        algorithm=SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
        selected_angle_degrees=selected.selected_angle_degrees,
        route_distance_km=selected.route_distance_km,
        turn_count=selected.turn_count,
        segment_count=selected.segment_count,
        runtime_ms=(perf_counter() - started) * 1_000,
        geometry_valid=selected.geometry_valid,
        error_message=None,
    )


def _measured_plan_key(plan: MeasuredCoveragePlan) -> tuple[float, int]:
    distance = plan.route_distance_km if plan.route_distance_km is not None else float("inf")
    turns = plan.turn_count if plan.turn_count is not None else 1_000_000_000
    return distance, turns


def _measure_adaptive_sections(
    case: CoverageBenchmarkCase,
    algorithm: SweepAngleAlgorithm,
    optimizer: ExactSectionRouteOptimizer,
    variant_factory: SectionVariantFactory,
) -> MeasuredCoveragePlan:
    started = perf_counter()
    global_plan = _measure_exact_section_plan(
        case.request,
        [case.request],
        algorithm,
        optimizer,
        variant_factory,
    )
    partitions = automatic_partition_requests(case.request)
    candidates = [global_plan]
    if len(partitions) > 1:
        candidates.append(
            _measure_exact_section_plan(
                case.request,
                partitions,
                algorithm,
                optimizer,
                variant_factory,
            )
        )
    feasible = [
        candidate
        for candidate in candidates
        if candidate.route_distance_km is not None and candidate.geometry_valid
    ]
    if not feasible:
        message = next(
            (
                candidate.error_message
                for candidate in candidates
                if candidate.error_message is not None
            ),
            "Точный секционный решатель не нашёл допустимый маршрут.",
        )
        return MeasuredCoveragePlan(
            algorithm=algorithm,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message=message,
        )
    selected = min(feasible, key=_measured_plan_key)
    return MeasuredCoveragePlan(
        algorithm=algorithm,
        selected_angle_degrees=selected.selected_angle_degrees,
        route_distance_km=selected.route_distance_km,
        turn_count=selected.turn_count,
        segment_count=selected.segment_count,
        runtime_ms=(perf_counter() - started) * 1_000,
        geometry_valid=True,
        error_message=None,
    )


def _measure_exact_section_plan(
    mission_request: CoveragePlanRequest,
    section_requests: list[CoveragePlanRequest],
    algorithm: SweepAngleAlgorithm,
    optimizer: ExactSectionRouteOptimizer,
    variant_factory: SectionVariantFactory,
) -> MeasuredCoveragePlan:
    started = perf_counter()
    try:
        planned = _plan_section_groups(section_requests, variant_factory)
    except CoveragePlanningError as error:
        return _failed_section_measurement(started, algorithm, error.message)
    except RouteGeometryError as error:
        return _failed_section_measurement(started, algorithm, str(error))
    if len(planned.variant_groups) > 12:
        return _failed_section_measurement(
            started,
            algorithm,
            "Точный решатель поддерживает не более 12 секций в одном маршруте.",
        )
    optimization_request = SectionRouteOptimizationRequest(
        variant_groups=planned.variant_groups,
        coverage_area=mission_request.area,
        departure_candidates=mission_request.departure_candidates,
        landing_candidates=mission_request.landing_candidates,
        no_fly_zones=mission_request.exclusions,
    )
    try:
        result = optimizer.optimize(optimization_request)
    except RouteGeometryError as error:
        return _failed_section_measurement(started, algorithm, str(error))
    return MeasuredCoveragePlan(
        algorithm=algorithm,
        selected_angle_degrees=(
            next(iter(planned.selected_angles)) if len(planned.selected_angles) == 1 else None
        ),
        route_distance_km=result.distance_km,
        turn_count=result.turn_count,
        segment_count=len(planned.variant_groups),
        runtime_ms=(perf_counter() - started) * 1_000,
        geometry_valid=route_is_clear_of_no_fly_zones(
            result.route,
            mission_request.exclusions,
        ),
        error_message=None,
    )


def _plan_section_groups(
    requests: list[CoveragePlanRequest],
    variant_factory: SectionVariantFactory,
) -> PlannedSectionGroups:
    groups: list[list[SectionRouteVariant]] = []
    angles: set[float] = set()
    for request in requests:
        results = _candidate_section_plans(request)
        baseline_segment_count = len(results[0].segments)
        compatible_results = [
            result for result in results if len(result.segments) == baseline_segment_count
        ]
        angles.update(result.selected_flight_direction_degrees for result in compatible_results)
        for segment_index in range(baseline_segment_count):
            section_id = f"section-{len(groups) + 1}"
            routes = [
                route
                for result in compatible_results
                for route in [
                    result.segments[segment_index].route,
                    *result.segments[segment_index].alternative_routes,
                ]
            ]
            groups.append(variant_factory.build(section_id, routes, request))
    return PlannedSectionGroups(variant_groups=groups, selected_angles=angles)


def _candidate_section_plans(
    request: CoveragePlanRequest,
) -> list[CoveragePlanResult]:
    results: list[CoveragePlanResult] = []
    errors: list[CoveragePlanningError] = []
    for algorithm in SECTION_VARIANT_ALGORITHMS:
        planner = BoustrophedonCoveragePlanner(automatic_angle_algorithm=algorithm)
        try:
            result = planner.plan(request)
        except CoveragePlanningError as error:
            errors.append(error)
            continue
        results.append(result)
    if results:
        return results
    if errors:
        raise errors[0]
    raise RuntimeError("Список алгоритмов вариантов секции пуст.")


def _section_variants(
    section_id: str,
    routes: list[list[Coordinate]],
) -> list[SectionRouteVariant]:
    variants: list[SectionRouteVariant] = []
    route_keys: set[tuple[tuple[float, float, float | None], ...]] = set()
    for route_index, route in enumerate(routes):
        for orientation, candidate in [
            ("forward", route),
            ("reversed", list(reversed(route))),
        ]:
            key = _route_key(candidate)
            if key in route_keys:
                continue
            route_keys.add(key)
            variants.append(
                SectionRouteVariant(
                    section_id=section_id,
                    variant_id=f"route-{route_index + 1}-{orientation}",
                    route=candidate,
                )
            )
    return variants


def _curvature_section_variants(
    section_id: str,
    routes: list[list[Coordinate]],
    request: CoveragePlanRequest,
    factory: CurvedSectionVariantFactory,
) -> list[SectionRouteVariant]:
    variants: list[SectionRouteVariant] = []
    source_keys: set[tuple[tuple[float, float, float | None], ...]] = set()
    for route_index, route in enumerate(routes):
        source_key = _route_key(route)
        if source_key in source_keys:
            continue
        source_keys.add(source_key)
        try:
            result = factory.build_curve(route, request)
        except ValidationError, RouteGeometryError:
            continue
        variants.extend(
            [
                SectionRouteVariant(
                    section_id=section_id,
                    variant_id=f"curve-{route_index + 1}-forward",
                    route=result.route,
                    survey_legs=result.survey_legs,
                ),
                SectionRouteVariant(
                    section_id=section_id,
                    variant_id=f"curve-{route_index + 1}-reversed",
                    route=list(reversed(result.route)),
                    survey_legs=[list(reversed(leg)) for leg in reversed(result.survey_legs)],
                ),
            ]
        )
    if not variants:
        raise RouteGeometryError(
            f"Секция {section_id} не содержит допустимой криволинейной змейки."
        )
    return variants


def _route_key(
    route: list[Coordinate],
) -> tuple[tuple[float, float, float | None], ...]:
    return tuple(
        (coordinate.longitude, coordinate.latitude, coordinate.altitude_m) for coordinate in route
    )


def _failed_section_measurement(
    started: float,
    algorithm: SweepAngleAlgorithm,
    message: str,
) -> MeasuredCoveragePlan:
    return MeasuredCoveragePlan(
        algorithm=algorithm,
        selected_angle_degrees=None,
        route_distance_km=None,
        turn_count=None,
        segment_count=0,
        runtime_ms=(perf_counter() - started) * 1_000,
        geometry_valid=False,
        error_message=message,
    )


def _measure_request(
    request: CoveragePlanRequest,
    algorithm: SweepAngleAlgorithm,
) -> MeasuredCoveragePlan:
    planner = BoustrophedonCoveragePlanner(automatic_angle_algorithm=algorithm)
    started = perf_counter()
    try:
        result = planner.plan(request)
    except CoveragePlanningError as error:
        return MeasuredCoveragePlan(
            algorithm=algorithm,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message=error.message,
        )
    return _measure_planned_result(request, algorithm, result, started)


def _measure_planned_result(
    request: CoveragePlanRequest,
    algorithm: SweepAngleAlgorithm,
    result: CoveragePlanResult,
    started: float,
) -> MeasuredCoveragePlan:
    if len(result.segments) > 12:
        return MeasuredCoveragePlan(
            algorithm=algorithm,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message="Точный решатель поддерживает не более 12 секций в одном маршруте.",
        )
    variant_groups = [
        _section_variants(
            f"section-{index + 1}",
            [segment.route, *segment.alternative_routes],
        )
        for index, segment in enumerate(result.segments)
    ]
    optimization_request = SectionRouteOptimizationRequest(
        variant_groups=variant_groups,
        coverage_area=request.area,
        departure_candidates=request.departure_candidates,
        landing_candidates=request.landing_candidates,
        no_fly_zones=request.exclusions,
    )
    try:
        optimized = ExactSectionRouteOptimizer().optimize(optimization_request)
    except RouteGeometryError as error:
        return MeasuredCoveragePlan(
            algorithm=algorithm,
            selected_angle_degrees=None,
            route_distance_km=None,
            turn_count=None,
            segment_count=0,
            runtime_ms=(perf_counter() - started) * 1_000,
            geometry_valid=False,
            error_message=str(error),
        )
    runtime_ms = (perf_counter() - started) * 1_000
    return MeasuredCoveragePlan(
        algorithm=algorithm,
        selected_angle_degrees=result.selected_flight_direction_degrees,
        route_distance_km=optimized.distance_km,
        turn_count=optimized.turn_count,
        segment_count=len(result.segments),
        runtime_ms=runtime_ms,
        geometry_valid=route_is_clear_of_no_fly_zones(
            optimized.route,
            request.exclusions,
        ),
        error_message=None,
    )
