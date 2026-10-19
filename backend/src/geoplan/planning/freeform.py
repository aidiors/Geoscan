"""Audited freeform improvement, assembled from replaceable search policies."""

from dataclasses import dataclass, field, replace
from itertools import product
from time import perf_counter

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.budget import improvement_allowed
from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.enums import (
    AltitudeReference,
    CoverageAlgorithm,
    MissionTask,
    OptimizationMode,
    OptimizationScope,
)
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.errors import PlanningError
from geoplan.flight.sites import _eligible_departure_sites, _eligible_landing_sites
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.freeform_costs import EndpointCostProtection
from geoplan.planning.freeform_missions import AuditedMissionPhotography, FreeformMissionFactory
from geoplan.planning.freeform_ports import (
    MissionCandidateBuilder,
    MissionCostProtection,
    MissionPhotography,
    ScheduledPlanEvaluator,
)
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.routing.freeform import GeometryCase, GeometryModel
from geoplan.routing.pattern_pool import CoverageCandidatePool, RankedCoverageCandidate

ContextKey = tuple[str, float, float, float, float, bool]


@dataclass(frozen=True)
class FreeformSearchContext:
    coverage: CoveragePlanRequest
    model: GeometryModel
    candidates: tuple[RankedCoverageCandidate, ...]


@dataclass(frozen=True)
class AuditedFreeformRouteOptimizer:
    """Compare audited incumbents across four bounded, request-local passes.

    Original and geometry-aware prefixes run first. Only then do extra diverse
    alternatives consume their own screening/scheduling budgets. Geometry and
    physical ranking are cached within one improvement call, never globally.
    """

    pool: CoverageCandidatePool = field(default_factory=CoverageCandidatePool)
    mission_factory: FreeformMissionFactory = field(default_factory=FreeformMissionFactory)
    photography: MissionPhotography = field(default_factory=AuditedMissionPhotography)
    cost_protection: MissionCostProtection = field(default_factory=EndpointCostProtection)
    maximum_contexts: int = 3
    maximum_scheduled: int = 2
    seconds_per_pass: float = 12
    preserved_candidates: int = 6

    def improve(
        self,
        request: PlanRequest,
        source: list[WorkSegment],
        drones: list[DroneSpec],
        baseline: PlanResponse,
        build: MissionCandidateBuilder,
        evaluate: ScheduledPlanEvaluator,
        reference: PlanResponse | None = None,
    ) -> PlanResponse:
        # Finish both old shortlists before spending budget on extra candidates.
        # A later-site legacy solution must not be displaced by extra work at
        # the first site. Cache only within this call; application instances are
        # immutable and safe to reuse for concurrent requests.
        contexts: dict[ContextKey, FreeformSearchContext] = {}
        best = baseline
        for expansion in (False, True):
            for additional_only in (False, True):
                if not improvement_allowed():
                    return best
                best = self._improve_pass(
                    request,
                    source,
                    drones,
                    best,
                    build,
                    evaluate,
                    reference,
                    additional_only=additional_only,
                    expansion=expansion,
                    cache=contexts,
                )
        return best

    def _improve_pass(
        self,
        request: PlanRequest,
        source: list[WorkSegment],
        drones: list[DroneSpec],
        baseline: PlanResponse,
        build: MissionCandidateBuilder,
        evaluate: ScheduledPlanEvaluator,
        reference: PlanResponse | None,
        *,
        additional_only: bool,
        expansion: bool,
        cache: dict[ContextKey, FreeformSearchContext],
    ) -> PlanResponse:
        """Screen a bounded diverse pool and schedule at most two valid alternatives.

        This first integration covers one flat RGB area in one sortie. Existing
        resource-split/fleet routes remain candidates. Endpoint protection compares
        total departure, landing and camera-off minutes of the actual complete plan.
        """
        if (
            not plan_is_audited(baseline)
            or len(request.coverage_areas) != 1
            or request.segments
            or request.survey_parameters is None
            or not source
            or request.minimum_turn_radius_m > 0
            or any(d.minimum_turn_radius_m > 0 or d.installed_sensor is not None for d in drones)
            or request.task is not MissionTask.RGB
            or request.altitude_reference is not AltitudeReference.MSL
            or request.terrain_elevation_samples
            or request.terrain_source is not None
            or request.optimization_mode is OptimizationMode.FAST_HEURISTIC
        ):
            return baseline
        guards = [baseline] + (
            [reference] if reference is not None and plan_is_audited(reference) else []
        )
        started = perf_counter()
        best, selected = baseline, None
        scheduled = checked = contexts = 0
        used = {p.drone_id for p in baseline.drone_plans if p.missions}
        fleet = sorted(
            drones, key=lambda d: (d.drone_id not in used, -d.cruise_speed_kmh, d.drone_id)
        )
        for drone in fleet[:3]:
            sites = product(
                _eligible_departure_sites(request, drone.drone_id)[:2],
                _eligible_landing_sites(request, drone.drone_id)[:2],
            )
            for home, landing in sites:
                if home is None or landing is None:
                    continue
                if (
                    contexts >= self.maximum_contexts
                    or scheduled >= self.maximum_scheduled
                    or perf_counter() - started > self.seconds_per_pass
                ):
                    break
                contexts += 1
                key = (
                    drone.drone_id,
                    home.coordinate.longitude,
                    home.coordinate.latitude,
                    landing.coordinate.longitude,
                    landing.coordinate.latitude,
                    additional_only,
                )
                context = cache.get(key)
                if context is None:
                    context = self._context(request, source, drone, home, landing, additional_only)
                    if context is None:
                        continue
                    cache[key] = context
                coverage, model = context.coverage, context.model
                candidates = (
                    context.candidates[self.preserved_candidates :]
                    if expansion
                    else context.candidates[: self.preserved_candidates]
                )
                for ranked in candidates:
                    route = ranked.pattern.route
                    if scheduled >= self.maximum_scheduled:
                        break
                    checked += 1
                    prepared = self.mission_factory.build(
                        model, source[0], ranked, drone, f"freeform-{contexts}-{checked}", build
                    )
                    if prepared is None:
                        continue
                    segment, mission = prepared.segment, prepared.mission
                    # Capture insertion preserves phase times. Reject candidates
                    # that cannot beat protected costs before building thousands
                    # of capture waypoints and performing polygon unions.
                    if any(not self.cost_protection.allows([mission], guard) for guard in guards):
                        continue
                    try:
                        mission = self.photography.prepare(coverage, mission)
                        if mission is None:
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
                        plan_is_audited(candidate)
                        and all(
                            self.cost_protection.allows_plan(candidate, guard) for guard in guards
                        )
                        and scheduled_plan_objective(request, candidate)
                        < scheduled_plan_objective(request, best)
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
                "freeform_candidates_checked": (
                    baseline.optimization.productive_routing.freeform_candidates_checked + checked
                ),
                "message": (
                    "Выбрано свободное покрытие: учтены старт и посадка; налёт, подлёт, возврат "
                    "и холостое время не хуже исходного плана."
                    if selected
                    else baseline.optimization.productive_routing.message
                ),
            }
        )
        assert best.optimization is not None

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

    def _context(
        self,
        request: PlanRequest,
        source: list[WorkSegment],
        drone: DroneSpec,
        home: FlightSite,
        landing: FlightSite,
        additional_only: bool,
    ) -> FreeformSearchContext | None:
        assert request.survey_parameters is not None
        coverage = CoveragePlanRequest(
            area=request.coverage_areas[0],
            mission_altitude_m=request.survey_height_agl.minimum_m,
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
            return None
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
        return FreeformSearchContext(
            coverage, model, self.pool.candidates(model, additional_only=additional_only)
        )
