"""Regroup selected expensive areas, then compare complete jointly scheduled plans."""

from collections import defaultdict

from geoplan.assignment.alternatives import AssignmentAlternativeProvider
from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.assignment.solver import AssignmentOptimizer
from geoplan.budget import PlanningTimeExceeded, improvement_allowed
from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import OptimizationMode
from geoplan.domain.fleet import DroneSpec
from geoplan.errors import PlanningError
from geoplan.planning.models import PlanRequest, PlanResponse, ProductiveRoutingSummary
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.planning.productive import (
    CandidateBuilder,
    PlanEvaluator,
    _mission_candidates,
    _routing_model,
    _survey_tracks,
)
from geoplan.planning.reassignment import improve_regrouped_assignment
from geoplan.routing.pool import ProductiveRoutePool, RoutePoolGenerator


def improve_multi_area_routes(
    request: PlanRequest,
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    baseline: PlanResponse | None,
    build: CandidateBuilder,
    evaluate: PlanEvaluator,
    route_pool: RoutePoolGenerator | None = None,
    assignment_optimizer: AssignmentOptimizer | None = None,
    assignment_alternative: AssignmentAlternativeProvider | None = None,
) -> PlanResponse | None:
    if baseline is None:
        return baseline
    if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
        return baseline
    by_area: dict[str, list[WorkSegment]] = defaultdict(list)
    missions = {m.segment_id: m for p in baseline.drone_plans for m in p.missions}
    areas = {a.area_id: a for a in request.coverage_areas}
    manual_ids = {s.segment_id for s in request.segments}
    for segment in segments:
        if (
            segment.coverage_area_id in areas
            and segment.segment_id in missions
            and segment.segment_id not in manual_ids
            and segment.coverage_pattern is None
        ):
            assert segment.coverage_area_id is not None
            by_area[segment.coverage_area_id].append(segment)
    ranked = sorted(
        by_area.items(),
        key=lambda item: (
            -sum(missions[s.segment_id].transit_duration_minutes or 0 for s in item[1]),
            item[0],
        ),
    )
    pool = route_pool or ProductiveRoutePool(
        maximum_orders=2,
        maximum_candidates=3,
        maximum_primary_candidates=3,
        maximum_catalog_candidates=3,
        seconds_per_catalog=0.25,
        seconds_per_decode=0.1,
        total_seconds=0.75,
        cross_objective_seconds=0,
        neighborhood_seconds=0,
        adaptive_neighborhood_seconds=0,
        completion_seed_seconds=0,
    )
    variants: list[dict[str, tuple[list[WorkSegment], list[MissionAssignmentCandidate]]]] = [
        {},
        {},
        {},
    ]
    active = {m.drone_id for m in missions.values()}
    fleet = drones
    if request.maximum_active_drones is not None and len(active) >= request.maximum_active_drones:
        fleet = [d for d in drones if d.drone_id in active]
    searched = 0
    for area_id, group in ranked:
        if searched >= 12 or not improvement_allowed(90):
            break
        if len(group) < 2 or any(
            s.survey_speed_limit_kmh is None or (s.survey_tracks is None and len(s.route) % 2)
            for s in group
        ):
            continue
        if not 2 <= sum(len(_survey_tracks(s)) for s in group) <= 400:
            continue
        area_request = request.model_copy(update={"coverage_areas": [areas[area_id]]})
        model = _routing_model(area_request, group, fleet)
        if model is None:
            continue
        searched += 1
        try:
            choices = pool.generate(model, request.maximum_active_drones).all
            for rank, (_, solution) in enumerate(
                sorted(choices, key=lambda c: model.score(c[1]))[:3]
            ):
                if not model.validate_geometry(solution):
                    continue
                candidate = _mission_candidates(
                    area_request, group[0], fleet, model, solution, build
                )
                if candidate is not None:
                    variants[rank][area_id] = candidate
        except PlanningTimeExceeded:
            break
    best = baseline
    checked = 0
    selected = "baseline"
    best_segments = segments
    seen: set[tuple] = set()
    for rank, changes in enumerate(variants):
        if not changes or not improvement_allowed():
            continue
        grouped = [s for s in segments if s.coverage_area_id not in changes]
        assignments = [
            MissionAssignmentCandidate(
                segment_id=s.segment_id,
                drone_id=missions[s.segment_id].drone_id,
                mission=missions[s.segment_id],
            )
            for s in grouped
        ]
        for new_segments, new_assignments in changes.values():
            grouped.extend(new_segments)
            assignments.extend(new_assignments)
        fingerprint = tuple(
            (a.segment_id, a.drone_id, a.mission.duration_minutes) for a in assignments
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if (
            request.maximum_active_drones is not None
            and len({a.drone_id for a in assignments}) > request.maximum_active_drones
        ):
            continue
        try:
            candidate = evaluate(grouped, assignments)
        except PlanningTimeExceeded:
            break
        except PlanningError:
            continue
        checked += 1
        if plan_is_audited(candidate) and (
            not plan_is_audited(best)
            or scheduled_plan_objective(request, candidate)
            < scheduled_plan_objective(request, best)
        ):
            best, selected = candidate, f"multi-area-{rank + 1}"
            best_segments = grouped
    if selected != "baseline" and assignment_optimizer is not None:
        reassigned, evaluated = improve_regrouped_assignment(
            request,
            best_segments,
            drones,
            best,
            build,
            evaluate,
            assignment_optimizer,
            assignment_alternative,
        )
        checked += evaluated
        if scheduled_plan_objective(request, reassigned) < scheduled_plan_objective(request, best):
            selected += "+global-assignment"
        best = reassigned
    summary = ProductiveRoutingSummary(
        selected_variant=selected,
        evaluated_variants=checked,
        baseline_makespan_minutes=baseline.metrics.completion_minutes,
        baseline_total_flight_minutes=baseline.metrics.total_flight_minutes,
        baseline_transit_minutes=sum(m.transit_duration_minutes or 0 for m in missions.values()),
        message=(
            f"Проверены совместные расписания для {len(request.coverage_areas)} областей. "
            f"Областей для перегруппировки по холостому налёту: {searched}. "
            + (
                "Выбран улучшенный план."
                if selected != "baseline"
                else "Сохранён лучший исходный план."
            )
        ),
    )
    assert best.optimization is not None
    return best.model_copy(
        update={
            "optimization": best.optimization.model_copy(update={"productive_routing": summary})
        }
    )
