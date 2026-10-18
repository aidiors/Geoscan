"""Canonical physical costs shared by the planner and reproducible experiments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from math import inf, isclose
from typing import Protocol

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.flight.performance import ground_speed_kmh, route_duration_minutes
from geoplan.navigation.routing import (
    PreparedSafeRoutes,
    RouteGeometryError,
    build_safe_route,
    route_distance_km,
    route_is_clear_of_no_fly_zones,
)


@dataclass(frozen=True)
class Track:
    track_id: str
    start: Coordinate
    end: Coordinate
    survey_speed_kmh: float


@dataclass(frozen=True)
class Aircraft:
    drone_id: str
    speed_kmh: float
    available_minutes: float
    home: Coordinate
    turnaround_minutes: float = 2
    eligible_tracks: frozenset[int] | None = None
    landing: Coordinate | None = None
    survey_speed_kmh: float | None = None
    minimum_turn_radius_m: float = 0

    @property
    def landing_coordinate(self) -> Coordinate:
        return self.landing if self.landing is not None else self.home


@dataclass(frozen=True)
class Trip:
    aircraft: int
    visits: tuple[int, ...]  # 2 * track index + reverse flag


Solution = tuple[Trip, ...]


class RoutingData(Protocol):
    @property
    def tracks(self) -> tuple[Track, ...]: ...

    @property
    def aircraft(self) -> tuple[Aircraft, ...]: ...

    @property
    def maximum_internal_distance_km(self) -> float | None: ...

    @property
    def wind(self) -> WindCondition | None: ...

    @property
    def exclusions(self) -> tuple[NoFlyZone, ...]: ...

    @property
    def criterion(self) -> OptimizationCriterion: ...

    @property
    def deadline_minutes(self) -> float | None: ...


@dataclass(frozen=True)
class RoutingProblem:
    tracks: tuple[Track, ...]
    aircraft: tuple[Aircraft, ...]
    maximum_internal_distance_km: float | None
    wind: WindCondition | None
    exclusions: tuple[NoFlyZone, ...]
    criterion: OptimizationCriterion
    deadline_minutes: float | None


@dataclass(frozen=True)
class RoutingSearchObjective:
    """Search guidance only; the application always evaluates the user's objective."""

    criterion: OptimizationCriterion
    deadline_minutes: float | None = None

    def score(self, total: float, deadhead: float, completion: float, active: int):
        return routing_objective(
            self.criterion, self.deadline_minutes, total, deadhead, completion, active
        )


@dataclass(frozen=True)
class Cost:
    survey: float = 0
    departure: float = 0
    connections: float = 0
    returning: float = 0
    internal_distance: float = 0

    @property
    def deadhead(self) -> float:
        return self.departure + self.connections + self.returning

    @property
    def total(self) -> float:
        return self.survey + self.deadhead


class ConnectionCost(Protocol):
    def cost(
        self,
        aircraft: Aircraft,
        source: tuple[Coordinate, Coordinate],
        target: tuple[Coordinate, Coordinate],
    ) -> tuple[float, float]: ...


class CostModel[P: RoutingData]:
    def __init__(
        self,
        problem: P,
        *,
        shared_geometry: bool = True,
        connection_cost: ConnectionCost | None = None,
    ) -> None:
        self.problem = problem
        self.shared_geometry = shared_geometry
        self.connection_cost = connection_cost
        self._connections: dict[tuple[int, int, int], tuple[float, float]] = {}
        self.geometry_builds = 0
        self.service_evaluations = 0
        self.insertion_evaluations = 0
        self.repair_attempts = 0
        self.sortie_destroy_attempts = 0
        self.sortie_repair_completed = 0
        self.neighborhood_operator_attempts: dict[str, int] = {}
        self.neighborhood_operator_improvements: dict[str, int] = {}
        self.group_exchange_attempts = 0
        self.catalog_arcs = 0
        self.catalog_complete = False
        self.decoded_candidates = 0
        self._paths: dict[tuple[Coordinate, Coordinate], tuple[list[Coordinate], float] | None] = {}
        self._legs: dict[tuple[int, Coordinate, Coordinate], tuple[float, float]] = {}
        self._trips: dict[Trip, Cost | None] = {}
        self._services: dict[tuple[int, int], tuple[float, float]] = {}
        self._safe_routes = (
            PreparedSafeRoutes.create(
                [
                    *(p for t in problem.tracks for p in (t.start, t.end)),
                    *(p for a in problem.aircraft for p in (a.home, a.landing_coordinate)),
                ],
                list(problem.exclusions),
            )
            if shared_geometry
            else None
        )
        self.track_distances = tuple(route_distance_km([t.start, t.end]) for t in problem.tracks)
        self._blocked_tracks = {
            i
            for i, track in enumerate(problem.tracks)
            if not route_is_clear_of_no_fly_zones(
                [track.start, track.end], list(problem.exclusions)
            )
        }

    def path(self, start: Coordinate, end: Coordinate) -> tuple[list[Coordinate], float] | None:
        key = start, end
        if self.shared_geometry and key in self._paths:
            return self._paths[key]
        self.geometry_builds += 1
        try:
            route = (
                self._safe_routes.build([start, end])
                if self._safe_routes is not None
                else build_safe_route([start, end], list(self.problem.exclusions))
            )
            result = route, route_distance_km(route)
        except RouteGeometryError:
            result = None
        if self.shared_geometry:
            self._paths[key] = result
        return result

    def endpoints(self, visit: int) -> tuple[Coordinate, Coordinate]:
        track = self.problem.tracks[visit // 2]
        return (track.end, track.start) if visit % 2 else (track.start, track.end)

    def leg(self, aircraft: int, start: Coordinate, end: Coordinate) -> tuple[float, float]:
        key = aircraft, start, end
        if key not in self._legs:
            try:
                path = self.path(start, end)
                if path is None:
                    self._legs[key] = inf, inf
                    return self._legs[key]
                route, distance = path
                duration = route_duration_minutes(
                    route, distance, self.problem.aircraft[aircraft].speed_kmh, self.problem.wind
                )
                self._legs[key] = duration, distance
            except RouteGeometryError, ValueError:
                self._legs[key] = inf, inf
        return self._legs[key]

    def service(self, aircraft: int, token: int) -> tuple[float, float]:
        key = aircraft, token
        if key not in self._services:
            self.service_evaluations += 1
            distance = self.track_distances[token // 2]
            start, end = self.endpoints(token)
            try:
                duration = route_duration_minutes(
                    [start, end],
                    distance,
                    self.problem.aircraft[aircraft].speed_kmh,
                    self.problem.wind,
                    self.problem.aircraft[aircraft].survey_speed_kmh
                    or self.problem.tracks[token // 2].survey_speed_kmh,
                )
            except ValueError:
                duration = inf
            self._services[key] = duration, distance
        return self._services[key]

    def connection(self, aircraft: int, source: int, target: int) -> tuple[float, float]:
        plane = self.problem.aircraft[aircraft]
        if self.connection_cost is None or plane.minimum_turn_radius_m == 0:
            return self.leg(aircraft, self.endpoints(source)[1], self.endpoints(target)[0])
        key = aircraft, source, target
        if key not in self._connections:
            self._connections[key] = self.connection_cost.cost(
                plane, self.endpoints(source), self.endpoints(target)
            )
        return self._connections[key]

    def service_allowed(self, aircraft: int, token: int) -> bool:
        if not 0 <= aircraft < len(self.problem.aircraft) or not (
            0 <= token < 2 * len(self.problem.tracks)
        ):
            return False
        eligible = self.problem.aircraft[aircraft].eligible_tracks
        return token // 2 not in self._blocked_tracks and (
            eligible is None or token // 2 in eligible
        )

    def feasible_cost(self, aircraft: int, cost: Cost) -> bool:
        limit = self.problem.maximum_internal_distance_km
        return cost.total <= self.problem.aircraft[aircraft].available_minutes + 1e-8 and (
            limit is None or cost.internal_distance <= limit + 1e-8
        )

    def trip(self, trip: Trip) -> Cost | None:
        if trip in self._trips:
            return self._trips[trip]
        if not 0 <= trip.aircraft < len(self.problem.aircraft) or any(
            token < 0 or token >= 2 * len(self.problem.tracks) for token in trip.visits
        ):
            return None
        plane = self.problem.aircraft[trip.aircraft]
        if not trip.visits or any(
            token // 2 in self._blocked_tracks
            or (plane.eligible_tracks is not None and token // 2 not in plane.eligible_tracks)
            for token in trip.visits
        ):
            return None
        limit = self.problem.maximum_internal_distance_km
        if (
            limit is not None
            and sum(self.track_distances[t // 2] for t in trip.visits) > limit + 1e-8
        ):
            return None
        survey = internal_distance = connections = 0.0
        departure = self.leg(trip.aircraft, plane.home, self.endpoints(trip.visits[0])[0])[0]
        returning = self.leg(
            trip.aircraft, self.endpoints(trip.visits[-1])[1], plane.landing_coordinate
        )[0]
        previous: int | None = None
        for token in trip.visits:
            if previous is not None:
                duration, distance = self.connection(trip.aircraft, previous, token)
                connections += duration
                internal_distance += distance
            duration, distance = self.service(trip.aircraft, token)
            survey += duration
            internal_distance += distance
            previous = token
        cost = Cost(survey, departure, connections, returning, internal_distance)
        valid = self.feasible_cost(trip.aircraft, cost)
        result = cost if valid else None
        if len(self._trips) >= 100_000:
            self._trips.clear()
        self._trips[trip] = result
        return result

    def metrics(self, solution: Solution) -> dict[str, float] | None:
        required = Counter(range(len(self.problem.tracks)))
        actual = Counter(token // 2 for trip in solution for token in trip.visits)
        if actual != required:
            return None
        loads = [0.0] * len(self.problem.aircraft)
        counts = [0] * len(loads)
        costs: list[Cost] = []
        for trip in solution:
            cost = self.trip(trip)
            if cost is None:
                return None
            costs.append(cost)
            loads[trip.aircraft] += cost.total + (
                self.problem.aircraft[trip.aircraft].turnaround_minutes
                if counts[trip.aircraft]
                else 0
            )
            counts[trip.aircraft] += 1
        total = sum(cost.total for cost in costs)
        deadhead = sum(cost.deadhead for cost in costs)
        return {
            "total_minutes": total,
            "survey_minutes": sum(cost.survey for cost in costs),
            "deadhead_minutes": deadhead,
            "departure_minutes": sum(cost.departure for cost in costs),
            "connection_minutes": sum(cost.connections for cost in costs),
            "return_minutes": sum(cost.returning for cost in costs),
            "load_makespan_minutes": max(loads, default=0),
            "sorties": float(len(solution)),
            "active_aircraft": float(sum(count > 0 for count in counts)),
            "useful_time_percent": (total - deadhead) / total * 100 if total else 0,
        }

    def score(
        self,
        solution: Solution,
        *,
        complete: bool = True,
        criterion: OptimizationCriterion | None = None,
        search_objective: RoutingSearchObjective | None = None,
    ) -> tuple[float, ...]:
        if not complete:
            if not solution or any(self.trip(trip) is None for trip in solution):
                return (inf,)
            loads = [0.0] * len(self.problem.aircraft)
            counts = [0] * len(loads)
            total = deadhead = 0.0
            for trip in solution:
                cost = self.trip(trip)
                assert cost is not None
                total += cost.total
                deadhead += cost.deadhead
                loads[trip.aircraft] += cost.total + (
                    self.problem.aircraft[trip.aircraft].turnaround_minutes
                    if counts[trip.aircraft]
                    else 0
                )
                counts[trip.aircraft] += 1
            return self.objective(
                total, deadhead, max(loads), sum(c > 0 for c in counts), criterion, search_objective
            )
        metrics = self.metrics(solution)
        if metrics is None:
            return (inf,)
        return self.objective(
            metrics["total_minutes"],
            metrics["deadhead_minutes"],
            metrics["load_makespan_minutes"],
            int(metrics["active_aircraft"]),
            criterion,
            search_objective,
        )

    def objective(
        self,
        total: float,
        deadhead: float,
        completion: float,
        active: int,
        criterion: OptimizationCriterion | None = None,
        search_objective: RoutingSearchObjective | None = None,
    ) -> tuple[float, ...]:
        if search_objective is not None:
            return search_objective.score(total, deadhead, completion, active)
        return routing_objective(
            criterion if criterion is not None else self.problem.criterion,
            self.problem.deadline_minutes,
            total,
            deadhead,
            completion,
            active,
        )

    def validate_geometry(self, solution: Solution) -> bool:
        """Reconstruct paths independently of cached trip costs; never bridge survey phases."""
        if self.metrics(solution) is None:
            return False
        # Rebuild the query context without the model's geometry or cost caches.
        # Use its fixed projection, so tiny projection changes cannot masquerade
        # as a stale accumulated flight cost.
        safe_routes = (
            PreparedSafeRoutes.create(
                [
                    *(p for t in self.problem.tracks for p in (t.start, t.end)),
                    *(p for a in self.problem.aircraft for p in (a.home, a.landing_coordinate)),
                ],
                list(self.problem.exclusions),
            )
            if self.shared_geometry
            else None
        )
        for trip in solution:
            plane = self.problem.aircraft[trip.aircraft]
            previous = plane.home
            elapsed = internal_distance = 0.0
            for index, token in enumerate(trip.visits):
                start, end = self.endpoints(token)
                connector = (
                    safe_routes.build([previous, start])
                    if safe_routes is not None
                    else build_safe_route([previous, start], list(self.problem.exclusions))
                )
                if not route_is_clear_of_no_fly_zones(
                    [start, end], list(self.problem.exclusions)
                ) or not route_is_clear_of_no_fly_zones(connector, list(self.problem.exclusions)):
                    return False
                if index and self.connection_cost is not None and plane.minimum_turn_radius_m > 0:
                    duration, distance = self.connection_cost.cost(
                        plane, self.endpoints(trip.visits[index - 1]), (start, end)
                    )
                    elapsed += duration
                    internal_distance += distance
                else:
                    elapsed += self._recomputed_duration(connector, plane.speed_kmh)
                    if index:
                        internal_distance += route_distance_km(connector)
                elapsed += self._recomputed_duration(
                    [start, end],
                    plane.speed_kmh,
                    plane.survey_speed_kmh or self.problem.tracks[token // 2].survey_speed_kmh,
                )
                internal_distance += route_distance_km([start, end])
                previous = end
            tail = (
                safe_routes.build([previous, plane.landing_coordinate])
                if safe_routes is not None
                else build_safe_route(
                    [previous, plane.landing_coordinate], list(self.problem.exclusions)
                )
            )
            if not route_is_clear_of_no_fly_zones(tail, list(self.problem.exclusions)):
                return False
            elapsed += self._recomputed_duration(tail, plane.speed_kmh)
            cost = self.trip(trip)
            assert cost is not None
            if not isclose(elapsed, cost.total, rel_tol=1e-9, abs_tol=1e-8):
                return False
            if not isclose(internal_distance, cost.internal_distance, abs_tol=1e-8):
                return False
        return True

    def _recomputed_duration(
        self, route: list[Coordinate], speed: float, cap: float = inf
    ) -> float:
        # Separate accumulation from route_duration_minutes and the cost caches.
        return sum(
            route_distance_km([a, b])
            * 60
            / min(
                ground_speed_kmh(a, b, speed, self.problem.wind),
                cap,
            )
            for a, b in pairwise(route)
            if a != b
        )


def reverse(visits: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(token ^ 1 for token in reversed(visits))


def variants(visits: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(dict.fromkeys((visits, reverse(visits), tuple(t ^ 1 for t in visits))))


def routing_objective(
    criterion: OptimizationCriterion,
    deadline: float | None,
    total: float,
    deadhead: float,
    completion: float,
    active: int,
) -> tuple[float, ...]:
    if criterion is OptimizationCriterion.COMPLETION_TIME:
        return completion, total, float(active), 0.0, deadhead
    if criterion is OptimizationCriterion.BALANCED:
        assert deadline is not None
        if completion <= deadline:
            return 0.0, total, completion, float(active), deadhead
        return 1.0, completion - deadline, total, float(active), deadhead
    return total, completion, float(active), 0.0, deadhead
