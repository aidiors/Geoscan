from dataclasses import dataclass, field

from geoplan.coverage.decomposition import automatic_partition_requests
from geoplan.coverage.models import CoveragePlanRequest, CoveragePlanResult, WorkSegment
from geoplan.coverage.scoring import CoverageRouteScore, _route_choice_score, _scoring_exclusions
from geoplan.coverage.sweep import (
    BoustrophedonCoveragePlanner,
    CoveragePlanningError,
)
from geoplan.domain.enums import CoverageAlgorithm, SweepAngleAlgorithm, SweepDirectionMode
from geoplan.domain.geo import Coordinate

SECTION_ANGLE_ALGORITHMS = (
    SweepAngleAlgorithm.GEOMETRY_AWARE,
    SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE,
    SweepAngleAlgorithm.CONVEX_HULL_EDGES,
    SweepAngleAlgorithm.GRID_15_DEGREES,
)


@dataclass(frozen=True)
class AdaptiveCoveragePlanner:
    base_planner: BoustrophedonCoveragePlanner = field(default_factory=BoustrophedonCoveragePlanner)
    section_angle_algorithms: tuple[SweepAngleAlgorithm, ...] = SECTION_ANGLE_ALGORITHMS

    @property
    def coverage_algorithm(self) -> CoverageAlgorithm:
        return CoverageAlgorithm.ADAPTIVE_DECOMPOSED_SWEEP

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult:
        baseline = self.base_planner.plan(request)
        if request.parameters.direction_mode is SweepDirectionMode.MANUAL:
            return baseline
        partitions = automatic_partition_requests(request)
        if len(partitions) == 1:
            return baseline
        partitioned = _partitioned_plan_or_none(
            request,
            partitions,
            baseline,
            self.section_angle_algorithms,
        )
        if partitioned is None:
            return baseline
        if _plan_key(partitioned, request) < _plan_key(baseline, request):
            return partitioned
        return baseline


def _partitioned_plan_or_none(
    mission_request: CoveragePlanRequest,
    partitions: list[CoveragePlanRequest],
    baseline: CoveragePlanResult,
    algorithms: tuple[SweepAngleAlgorithm, ...],
) -> CoveragePlanResult | None:
    selected_plans: list[CoveragePlanResult] = []
    for partition in partitions:
        selected = _best_section_plan_or_none(partition, mission_request, algorithms)
        if selected is None:
            return None
        selected_plans.append(selected)
    source_segments = [segment for plan in selected_plans for segment in plan.segments]
    segments: list[WorkSegment] = []
    for index, segment in enumerate(source_segments):
        mission_segment = _mission_segment_or_none(segment, mission_request, index)
        if mission_segment is None:
            return None
        segments.append(mission_segment)
    directions = {
        segment.flight_direction_degrees
        for segment in segments
        if segment.flight_direction_degrees is not None
    }
    selected_direction = (
        next(iter(directions))
        if len(directions) == 1
        else baseline.selected_flight_direction_degrees
    )
    return CoveragePlanResult(
        segments=segments,
        computed_gsd_cm=baseline.computed_gsd_cm,
        footprint_width_m=baseline.footprint_width_m,
        footprint_height_m=baseline.footprint_height_m,
        strip_spacing_m=baseline.strip_spacing_m,
        trigger_spacing_m=baseline.trigger_spacing_m,
        selected_flight_direction_degrees=selected_direction,
        angle_algorithm=SweepAngleAlgorithm.ADAPTIVE_DECOMPOSITION,
    )


def _best_section_plan_or_none(
    partition_request: CoveragePlanRequest,
    mission_request: CoveragePlanRequest,
    algorithms: tuple[SweepAngleAlgorithm, ...],
) -> CoveragePlanResult | None:
    candidates: list[CoveragePlanResult] = []
    for algorithm in algorithms:
        planner = BoustrophedonCoveragePlanner(automatic_angle_algorithm=algorithm)
        try:
            result = planner.plan(partition_request)
        except CoveragePlanningError:
            continue
        candidates.append(result)
    feasible = [
        candidate
        for candidate in candidates
        if _plan_score_or_none(candidate, mission_request) is not None
    ]
    if not feasible:
        return None
    return min(feasible, key=lambda candidate: _plan_key(candidate, mission_request))


def _mission_segment_or_none(
    segment: WorkSegment,
    request: CoveragePlanRequest,
    index: int,
) -> WorkSegment | None:
    if segment.survey_speed_limit_kmh is None:
        return None
    valid_routes = [
        (score, route)
        for route in [segment.route, *segment.alternative_routes]
        if (
            score := _route_score_or_none(
                route,
                request,
                segment.survey_speed_limit_kmh,
            )
        )
        is not None
    ]
    if not valid_routes:
        return None
    ordered_routes = [route for _, route in sorted(valid_routes, key=lambda item: item[0])]
    return segment.model_copy(
        update={
            "segment_id": f"{request.area.area_id}-adaptive-{index + 1}"[:64],
            "coverage_area_id": request.area.area_id,
            "route": ordered_routes[0],
            "alternative_routes": ordered_routes[1:],
        }
    )


def _plan_key(
    plan: CoveragePlanResult,
    request: CoveragePlanRequest,
) -> tuple[float, float, int]:
    score = _plan_score_or_none(plan, request)
    return (
        score.objective_cost if score is not None else float("inf"),
        score.distance_km if score is not None else float("inf"),
        len(plan.segments),
    )


def _plan_score_or_none(
    plan: CoveragePlanResult,
    request: CoveragePlanRequest,
) -> CoverageRouteScore | None:
    scores = [_segment_score_or_none(segment, request) for segment in plan.segments]
    if any(score is None for score in scores):
        return None
    valid_scores = [score for score in scores if score is not None]
    return CoverageRouteScore(
        sum(score.objective_cost for score in valid_scores),
        sum(score.distance_km for score in valid_scores),
        sum(score.turn_count for score in valid_scores),
    )


def _segment_score_or_none(
    segment: WorkSegment,
    request: CoveragePlanRequest,
) -> CoverageRouteScore | None:
    if segment.survey_speed_limit_kmh is None:
        return None
    choices = [
        score
        for route in [segment.route, *segment.alternative_routes]
        for oriented in [route, list(reversed(route))]
        if (
            score := _route_score_or_none(
                oriented,
                request,
                segment.survey_speed_limit_kmh,
            )
        )
        is not None
    ]
    return min(choices) if choices else None


def _route_score_or_none(
    route: list[Coordinate],
    request: CoveragePlanRequest,
    survey_speed_limit_kmh: float,
) -> CoverageRouteScore | None:
    choices = [
        score
        for candidate in [route, list(reversed(route))]
        if (
            score := _route_choice_score(
                candidate,
                request,
                _scoring_exclusions(request),
                survey_speed_limit_kmh,
            )
        )
        is not None
    ]
    return min(choices) if choices else None
