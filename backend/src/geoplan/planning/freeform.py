"""Bounded freeform candidates, selected only after actual mission and schedule audit."""

from dataclasses import replace
from itertools import product
from math import hypot
from time import perf_counter

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.enums import (
    AltitudeReference,
    CoverageAlgorithm,
    MissionStageType,
    MissionTask,
    OptimizationMode,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.sites import _eligible_departure_sites, _eligible_landing_sites
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.productive import CandidateBuilder, PlanEvaluator, _audited, _objective
from geoplan.routing.coverage_patterns import shell_routes
from geoplan.routing.freeform import (
    GeometryCase,
    GeometryModel,
    Route,
    anchor_contours,
    contour_strokes,
    oriented_order,
    route_legs,
)
from geoplan.routing.photography import screen_mission, with_capture_waypoints


def improve_freeform_routes(
    request: PlanRequest,
    source: list[WorkSegment],
    drones: list[DroneSpec],
    baseline: PlanResponse,
    build: CandidateBuilder,
    evaluate: PlanEvaluator,
    reference: PlanResponse | None = None,
) -> PlanResponse:
    """Try at most six paths per context and schedule at most two valid alternatives.

    This first integration covers one flat RGB area in one sortie. Existing
    resource-split/fleet routes remain candidates. Endpoint protection compares
    total departure, landing and camera-off minutes of the actual complete plan.
    """
    if (
        not _audited(baseline)
        or len(request.coverage_areas) != 1
        or request.segments
        or request.survey_parameters is None
        or not source
        or request.minimum_turn_radius_m > 0
        or request.task is not MissionTask.RGB
        or request.altitude_reference is not AltitudeReference.MSL
        or request.terrain_elevation_samples
        or request.terrain_source is not None
        or request.optimization_mode is OptimizationMode.FAST_HEURISTIC
    ):
        return baseline
    guards = [baseline] + ([reference] if reference is not None and _audited(reference) else [])
    started = perf_counter()
    best, selected = baseline, None
    scheduled = checked = contexts = 0
    used = {p.drone_id for p in baseline.drone_plans if p.missions}
    fleet = sorted(drones, key=lambda d: (d.drone_id not in used, -d.cruise_speed_kmh, d.drone_id))
    for drone in fleet[:3]:
        sites = product(
            _eligible_departure_sites(request, drone.drone_id)[:2],
            _eligible_landing_sites(request, drone.drone_id)[:2],
        )
        for home, landing in sites:
            if home is None or landing is None:
                continue
            if contexts >= 3 or scheduled >= 2 or perf_counter() - started > 12:
                break
            contexts += 1
            coverage = CoveragePlanRequest(
                area=request.coverage_areas[0],
                mission_altitude_m=request.mission_altitude_m,
                parameters=request.survey_parameters,
                wind=request.wind,
                exclusions=_constraints_for_segment(source[0], request),
            )
            probe = GeometryModel(GeometryCase("freeform", coverage, (0, 0), (0, 0)))
            if (
                probe.target.area > 5_000_000
                or probe.spacing < 10
                or len(coverage.area.coordinates) > 128
            ):
                continue
            model = GeometryModel(
                replace(
                    probe.case,
                    home=probe.projection.project(home.coordinate),
                    landing=probe.projection.project(landing.coordinate),
                    cruise_speed_kmh=drone.cruise_speed_kmh,
                    endurance_minutes=drone.maximum_flight_minutes,
                    reserve_percent=drone.battery_reserve_percent,
                )
            )
            ranked = []
            for route in _patterns(model):
                legs = route_legs(model, route)[0]
                cost = sum(leg.minutes for leg in legs)
                ranked.append((cost, route.family, route, legs))
            for _, _, route, legs in sorted(ranked, key=lambda r: r[:2])[:6]:
                if scheduled >= 2:
                    break
                checked += 1
                service = [leg for leg in legs if leg.phase == "survey"]
                internal = [leg for leg in legs if leg.phase in ("survey", "connection")]
                if not internal:
                    continue
                path = [
                    model.coordinate(internal[0].start),
                    *(model.coordinate(leg.end) for leg in internal),
                ]
                tracks = [
                    (model.coordinate(leg.start), model.coordinate(leg.end)) for leg in service
                ]
                ratio = min(
                    route_distance_km(list(track))
                    * 1000
                    / hypot(
                        leg.end[0] - leg.start[0],
                        leg.end[1] - leg.start[1],
                    )
                    for track, leg in zip(tracks, service, strict=True)
                )
                segment = source[0].model_copy(
                    update={
                        "segment_id": f"freeform-{contexts}-{checked}",
                        "route": path,
                        "alternative_routes": [],
                        "survey_tracks": tracks,
                        "distance_km": route_distance_km(path),
                        "image_count": None,
                        "survey_speed_limit_kmh": model.survey_cap * min(1, ratio) * (1 - 1e-9),
                        "strip_spacing_m": model.spacing,
                        "computed_gsd_cm": model.width
                        / coverage.parameters.sensor.image_width_px
                        * 100,
                        "coverage_pattern": "split_shell"
                        if route.family.startswith("split_")
                        else ("shell" if route.family.startswith("shell_") else "contours"),
                        "flight_direction_degrees": None,
                    }
                )
                mission = build(segment, drone)
                if mission is None:
                    continue
                try:
                    mission = with_capture_waypoints(
                        mission,
                        coverage.parameters.sensor.minimum_trigger_interval_s,
                    )
                    if not screen_mission(coverage, mission)["screen_passed"]:
                        continue
                    if any(not _protected_missions([mission], guard) for guard in guards):
                        continue
                    segment = segment.model_copy(update={"image_count": mission.image_count})
                    candidate = evaluate(
                        [segment],
                        [
                            MissionAssignmentCandidate(
                                segment_id=segment.segment_id,
                                drone_id=drone.drone_id,
                                mission=mission,
                            )
                        ],
                    )
                except PlanningError, ValueError:
                    continue
                scheduled += 1
                if (
                    _audited(candidate)
                    and all(_protected(candidate, guard) for guard in guards)
                    and _objective(request, candidate) < _objective(request, best)
                ):
                    best, selected = candidate, route.family
    if baseline.optimization is None or baseline.optimization.productive_routing is None:
        return best
    summary = baseline.optimization.productive_routing.model_copy(
        update={
            "selected_variant": f"freeform:{selected}"
            if selected
            else baseline.optimization.productive_routing.selected_variant,
            "evaluated_variants": baseline.optimization.productive_routing.evaluated_variants
            + scheduled,
            "freeform_candidates_checked": checked,
            "message": (
                "Выбрано свободное покрытие: учтены старт и посадка; налёт, подлёт, возврат "
                "и холостое время не хуже исходного плана."
                if selected
                else baseline.optimization.productive_routing.message
            ),
        }
    )
    assert best.optimization is not None
    from geoplan.domain.enums import OptimizationScope

    return best.model_copy(
        update={
            "optimization": best.optimization.model_copy(
                update={
                    "productive_routing": summary,
                    "coverage_algorithm": CoverageAlgorithm.FREEFORM_PATTERN_POOL
                    if selected
                    else best.optimization.coverage_algorithm,
                    "scope": OptimizationScope.FREEFORM_COVERAGE_ROUTE_POOL
                    if selected
                    else best.optimization.scope,
                }
            )
        }
    )


def _patterns(model: GeometryModel) -> tuple[Route, ...]:
    rings = contour_strokes(model)
    routes = []
    if len(rings) <= 40:
        for name, order in (
            ("inward", rings),
            ("outward", tuple(reversed(rings))),
            ("loop", (*rings[::2], *reversed(rings[1::2]))),
        ):
            if order:
                anchored = anchor_contours(model, tuple(order))
                routes.append(Route(f"contours_{name}", (oriented_order(model, anchored),)))
    for layers, angle, split in product((1, 3, 5, 8, 9), (0, 90), (False, True)):
        routes.extend(shell_routes(model, layers, angle, split=split))
    return tuple(routes)


def _costs(missions: list[Mission]) -> tuple[float, float, float, float]:
    total = dead = departure = returning = 0.0
    for mission in missions:
        total += mission.duration_minutes
        dead += mission.transit_duration_minutes or 0
        scan = [
            i
            for i, stage in enumerate(mission.stages)
            if stage.stage_type is MissionStageType.SURVEY
        ]
        if not scan:
            return float("inf"), float("inf"), float("inf"), float("inf")
        departure += sum(s.duration_minutes for s in mission.stages[: scan[0]])
        returning += sum(s.duration_minutes for s in mission.stages[scan[-1] + 1 :])
    return total, dead, departure, returning


def _protected_missions(missions: list[Mission], baseline: PlanResponse) -> bool:
    original = [m for drone in baseline.drone_plans for m in drone.missions]
    return all(a <= b + 1e-7 for a, b in zip(_costs(missions), _costs(original), strict=True))


def _protected(candidate: PlanResponse, baseline: PlanResponse) -> bool:
    return _protected_missions(
        [m for drone in candidate.drone_plans for m in drone.missions], baseline
    )
