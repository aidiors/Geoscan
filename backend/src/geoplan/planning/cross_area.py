"""Bounded neighboring-area alternatives with a complete scheduled-plan comparison."""

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.budget import PlanningTimeExceeded, improvement_allowed
from geoplan.coverage.models import WorkSegment
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.navigation.routing import coordinate_distance_km
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.planning.productive import (
    CandidateBuilder,
    PlanEvaluator,
    _mission_candidates,
    _routing_model,
    _survey_tracks,
)
from geoplan.routing.pool import ProductiveRoutePool, RoutePoolGenerator


@dataclass(frozen=True)
class NeighborAreaSelector:
    maximum_areas: int = 12
    maximum_cluster_areas: int = 4
    maximum_tracks: int = 400
    maximum_clusters: int = 6

    def select(self, groups: dict[str, list[WorkSegment]]) -> list[tuple[str, ...]]:
        pending = list(groups)[: self.maximum_areas]
        centers = {}
        sizes = {}
        for area in pending:
            points = [
                p for segment in groups[area] for track in _survey_tracks(segment) for p in track
            ]
            centers[area] = Coordinate(
                longitude=sum(p.longitude for p in points) / len(points),
                latitude=sum(p.latitude for p in points) / len(points),
            )
            sizes[area] = len(points) // 2
        full, fallbacks = [], []
        while pending:
            anchor = pending.pop(0)
            selected = [anchor]
            count = sizes[anchor]
            for area in sorted(
                pending, key=lambda a: (coordinate_distance_km(centers[anchor], centers[a]), a)
            ):
                if len(selected) >= self.maximum_cluster_areas:
                    break
                if count + sizes[area] <= self.maximum_tracks:
                    selected.append(area)
                    count += sizes[area]
            if len(selected) < 2:
                continue
            pending = [area for area in pending if area not in selected]
            full.append(tuple(selected))
            fallbacks.extend(tuple(selected[:n]) for n in range(len(selected) - 1, 1, -1))
        return [*full, *fallbacks][: self.maximum_clusters]


@dataclass(frozen=True)
class CrossAreaResult:
    plan: PlanResponse | None
    segments: list[WorkSegment]
    evaluated: int = 0


class CrossAreaOptimizer(Protocol):
    def improve(
        self,
        request: PlanRequest,
        segments: list[WorkSegment],
        drones: list[DroneSpec],
        baseline: PlanResponse | None,
        build: CandidateBuilder,
        evaluate: PlanEvaluator,
    ) -> CrossAreaResult: ...


@dataclass(frozen=True)
class AuditedCrossAreaPlanner:
    selector: NeighborAreaSelector = field(default_factory=NeighborAreaSelector)
    route_pool: RoutePoolGenerator = field(
        default_factory=lambda: ProductiveRoutePool(
            maximum_orders=2,
            maximum_candidates=3,
            maximum_primary_candidates=3,
            maximum_catalog_candidates=3,
            seconds_per_catalog=0.5,
            seconds_per_decode=0.2,
            total_seconds=1.5,
            cross_objective_seconds=0,
            neighborhood_seconds=0,
            adaptive_neighborhood_seconds=0,
            completion_seed_seconds=0,
        )
    )

    def improve(
        self,
        request: PlanRequest,
        segments: list[WorkSegment],
        drones: list[DroneSpec],
        baseline: PlanResponse | None,
        build: CandidateBuilder,
        evaluate: PlanEvaluator,
    ) -> CrossAreaResult:
        groups: dict[str, list[WorkSegment]] = defaultdict(list)
        manual_ids = {s.segment_id for s in request.segments}
        for segment in segments:
            if (
                len(segment.area_ids) == 1
                and segment.segment_id not in manual_ids
                and segment.coverage_pattern is None
                and segment.survey_speed_limit_kmh is not None
                and (segment.survey_tracks is not None or len(segment.route) % 2 == 0)
            ):
                groups[segment.area_ids[0]].append(segment)
        best, current, checked = baseline, segments, 0
        consumed: set[str] = set()
        for cluster in self.selector.select(groups):
            if consumed.intersection(cluster) or not improvement_allowed():
                continue
            replaced = [s for area in cluster for s in groups[area]]
            removed = {s.segment_id for s in replaced}
            outside = [s for s in current if s.segment_id not in removed]
            missions = (
                {m.segment_id: m for p in best.drone_plans for m in p.missions}
                if best is not None
                else {}
            )
            if any(s.segment_id not in missions for s in outside):
                continue
            untouched = [
                MissionAssignmentCandidate(
                    segment_id=s.segment_id,
                    drone_id=missions[s.segment_id].drone_id,
                    mission=missions[s.segment_id],
                )
                for s in outside
            ]
            # Other areas already consume batteries and service life in this job.
            reserved = []
            active = {m.drone_id for m in missions.values()}
            for drone in drones:
                if (
                    request.maximum_active_drones is not None
                    and len(active) >= request.maximum_active_drones
                    and drone.drone_id not in active
                ):
                    continue
                used = [a.mission for a in untouched if a.drone_id == drone.drone_id]
                reserved.append(
                    drone.model_copy(
                        update={
                            "available_battery_sets": max(
                                0, drone.available_battery_sets - len(used)
                            )
                            if drone.available_battery_sets is not None
                            else None,
                            "completed_flights_since_maintenance": (
                                drone.completed_flights_since_maintenance + len(used)
                            ),
                            "flight_hours_since_maintenance": drone.flight_hours_since_maintenance
                            + sum(m.duration_minutes for m in used) / 60,
                        }
                    )
                )
            owners = tuple(s.area_ids[0] for s in replaced for _ in _survey_tracks(s))
            group_id = "joint-" + sha256("\0".join(sorted(cluster)).encode()).hexdigest()[:12]
            try:
                model = _routing_model(request, replaced, reserved)
                if model is None:
                    continue
                alternatives = self.route_pool.generate(model, request.maximum_active_drones).all
                for _, solution in sorted(alternatives, key=lambda c: model.score(c[1]))[:3]:
                    if not improvement_allowed() or not model.validate_geometry(solution):
                        continue
                    built = _mission_candidates(
                        request,
                        replaced[0],
                        drones,
                        model,
                        solution,
                        build,
                        track_area_ids=owners,
                        group_id=group_id,
                    )
                    if built is None:
                        continue
                    new_segments, assignments = built
                    combined = [*untouched, *assignments]
                    if (
                        request.maximum_active_drones is not None
                        and len({a.drone_id for a in combined}) > request.maximum_active_drones
                    ):
                        continue
                    try:
                        candidate = evaluate([*outside, *new_segments], combined)
                    except PlanningTimeExceeded:
                        raise
                    except PlanningError:
                        continue
                    checked += 1
                    if plan_is_audited(candidate) and (
                        best is None
                        or not plan_is_audited(best)
                        or scheduled_plan_objective(request, candidate)
                        < scheduled_plan_objective(request, best)
                    ):
                        best, current = candidate, [*outside, *new_segments]
                        consumed.update(cluster)
            except PlanningTimeExceeded:
                break
            except PlanningError:
                continue
        return CrossAreaResult(best, current, checked)
