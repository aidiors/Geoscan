from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from geoplan.domain.enums import OptimalityStatus
from geoplan.domain.geo import Coordinate
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.hybrid_astar import HybridAStarPlanner
from geoplan.navigation.models import (
    DubinsPathRequest,
    DubinsPose,
    HybridAStarPathRequest,
    SectionRouteOptimizationRequest,
    SectionRouteOptimizationResult,
    SectionRouteSelection,
    SectionRouteVariant,
)
from geoplan.navigation.routing import (
    RouteGeometryError,
    build_safe_route,
    route_bearing_degrees,
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_coverage_area,
)


@dataclass(frozen=True)
class PreparedVariant:
    group_index: int
    variant_index: int
    contract: SectionRouteVariant
    route: list[Coordinate]
    distance_km: float
    turn_count: int


@dataclass(frozen=True)
class SelectionIndex:
    group_index: int
    variant_index: int


@dataclass(frozen=True)
class StateKey:
    visited_mask: int
    group_index: int
    variant_index: int


@dataclass(frozen=True)
class DynamicRecord:
    distance_km: float
    turn_count: int
    selections: tuple[SelectionIndex, ...]
    departure_index: int | None


@dataclass(frozen=True)
class Transition:
    connector: list[Coordinate]
    appended_distance_km: float
    appended_turn_count: int


@dataclass(frozen=True)
class FinalChoice:
    record: DynamicRecord
    landing_index: int | None
    distance_km: float
    turn_count: int


class SectionTransitionPlanner(Protocol):
    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SectionRouteOptimizationRequest,
    ) -> list[Coordinate] | None: ...


@dataclass(frozen=True)
class VisibilityGraphSectionTransitionPlanner:
    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SectionRouteOptimizationRequest,
    ) -> list[Coordinate] | None:
        connector = _safe_connector_or_none(source[-1], target[0], request)
        if connector is None:
            return None
        if not route_is_inside_coverage_area(connector, request.coverage_area):
            return None
        return connector


@dataclass(frozen=True)
class DubinsSectionTransitionPlanner:
    minimum_turn_radius_m: float
    sampling_step_m: float = 5

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SectionRouteOptimizationRequest,
    ) -> list[Coordinate] | None:
        source_heading = _last_route_heading(source)
        target_heading = _first_route_heading(target)
        if source[-1] == target[0] and _heading_difference(source_heading, target_heading) <= 1e-9:
            return [source[-1]]
        path_request = DubinsPathRequest(
            start=DubinsPose(
                coordinate=source[-1],
                heading_degrees=source_heading,
            ),
            end=DubinsPose(
                coordinate=target[0],
                heading_degrees=target_heading,
            ),
            minimum_turn_radius_m=self.minimum_turn_radius_m,
            sampling_step_m=self.sampling_step_m,
        )
        if not request.no_fly_zones:
            try:
                return ExactDubinsPlanner().plan(path_request).route
            except RuntimeError:
                return None
        try:
            candidates = ExactDubinsPlanner().plan_candidates(path_request)
        except RuntimeError:
            return None
        return next(
            (
                candidate.route
                for candidate in candidates
                if route_is_clear_of_no_fly_zones(candidate.route, request.no_fly_zones)
            ),
            None,
        )


@dataclass(frozen=True)
class HybridAStarSectionTransitionPlanner:
    planner: HybridAStarPlanner
    minimum_turn_radius_m: float
    primitive_length_m: float
    sampling_step_m: float
    grid_resolution_m: float
    heading_bin_degrees: float
    analytic_expansion_distance_m: float
    maximum_expansions: int

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SectionRouteOptimizationRequest,
    ) -> list[Coordinate] | None:
        source_heading = _last_route_heading(source)
        target_heading = _first_route_heading(target)
        if source[-1] == target[0] and _heading_difference(source_heading, target_heading) <= 1e-9:
            return [source[-1]]
        path_request = HybridAStarPathRequest(
            start=DubinsPose(
                coordinate=source[-1],
                heading_degrees=source_heading,
            ),
            end=DubinsPose(
                coordinate=target[0],
                heading_degrees=target_heading,
            ),
            no_fly_zones=request.no_fly_zones,
            minimum_turn_radius_m=self.minimum_turn_radius_m,
            primitive_length_m=self.primitive_length_m,
            sampling_step_m=self.sampling_step_m,
            grid_resolution_m=self.grid_resolution_m,
            heading_bin_degrees=self.heading_bin_degrees,
            analytic_expansion_distance_m=self.analytic_expansion_distance_m,
            maximum_expansions=self.maximum_expansions,
        )
        try:
            result = self.planner.plan(path_request)
        except RuntimeError:
            return None
        return result.route


@dataclass(frozen=True)
class ExactSectionRouteOptimizer:
    transition_planner: SectionTransitionPlanner = VisibilityGraphSectionTransitionPlanner()

    def optimize(
        self,
        request: SectionRouteOptimizationRequest,
    ) -> SectionRouteOptimizationResult:
        groups = _prepare_groups(request)
        transitions = _build_transitions(groups, request, self.transition_planner)
        records = _initial_records(groups, request)
        if not records:
            raise RouteGeometryError("Ни одна секция не достижима из доступных точек взлёта.")
        full_mask = (1 << len(groups)) - 1
        for visited_mask in range(1, full_mask + 1):
            active = [
                (key, record) for key, record in records.items() if key.visited_mask == visited_mask
            ]
            for key, record in active:
                _extend_record(records, key, record, groups, transitions)
        final = _select_final(records, full_mask, groups, request)
        if final is None:
            raise RouteGeometryError(
                "Безопасный маршрут через все секции до точки посадки отсутствует."
            )
        route = _restore_route(final, groups, transitions, request)
        selections = [
            SectionRouteSelection(
                section_id=groups[index.group_index][index.variant_index].contract.section_id,
                variant_id=groups[index.group_index][index.variant_index].contract.variant_id,
            )
            for index in final.record.selections
        ]
        departure = (
            request.departure_candidates[final.record.departure_index]
            if final.record.departure_index is not None
            else None
        )
        landing = (
            request.landing_candidates[final.landing_index]
            if final.landing_index is not None
            else None
        )
        return SectionRouteOptimizationResult(
            selections=selections,
            route=route,
            distance_km=route_distance_km(route),
            turn_count=_turn_count(route),
            departure=departure,
            landing=landing,
            optimality=OptimalityStatus.PROVEN_OPTIMAL,
        )


def _prepare_groups(
    request: SectionRouteOptimizationRequest,
) -> list[list[PreparedVariant]]:
    groups: list[list[PreparedVariant]] = []
    for group_index, source_group in enumerate(request.variant_groups):
        prepared_group: list[PreparedVariant] = []
        for variant in source_group:
            prepared = _prepare_variant(
                variant,
                group_index,
                len(prepared_group),
                request,
            )
            if prepared is not None:
                prepared_group.append(prepared)
        if not prepared_group:
            raise RouteGeometryError(
                f"Секция {source_group[0].section_id} не содержит допустимого маршрута."
            )
        groups.append(prepared_group)
    return groups


def _prepare_variant(
    variant: SectionRouteVariant,
    group_index: int,
    variant_index: int,
    request: SectionRouteOptimizationRequest,
) -> PreparedVariant | None:
    route = _safe_route_or_none(variant.route, request)
    if route is None or not _variant_survey_is_inside_area(variant, route, request):
        return None
    distance_km = route_distance_km(route)
    if distance_km <= 0:
        return None
    return PreparedVariant(
        group_index=group_index,
        variant_index=variant_index,
        contract=variant,
        route=route,
        distance_km=distance_km,
        turn_count=_turn_count(route),
    )


def _variant_survey_is_inside_area(
    variant: SectionRouteVariant,
    route: list[Coordinate],
    request: SectionRouteOptimizationRequest,
) -> bool:
    routes = variant.survey_legs or [route]
    return all(
        route_is_inside_coverage_area(candidate, request.coverage_area) for candidate in routes
    )


def _build_transitions(
    groups: list[list[PreparedVariant]],
    request: SectionRouteOptimizationRequest,
    transition_planner: SectionTransitionPlanner,
) -> dict[tuple[int, int, int, int], Transition]:
    transitions: dict[tuple[int, int, int, int], Transition] = {}
    for source_group in groups:
        for source in source_group:
            for target_group in groups:
                if source.group_index == target_group[0].group_index:
                    continue
                for target in target_group:
                    connector = transition_planner.connect(source.route, target.route, request)
                    if connector is None:
                        continue
                    appended_route = _join_routes([source.route[-2:], connector, target.route])
                    transitions[
                        (
                            source.group_index,
                            source.variant_index,
                            target.group_index,
                            target.variant_index,
                        )
                    ] = Transition(
                        connector=connector,
                        appended_distance_km=(route_distance_km(connector) + target.distance_km),
                        appended_turn_count=_turn_count(appended_route),
                    )
    return transitions


def _initial_records(
    groups: list[list[PreparedVariant]],
    request: SectionRouteOptimizationRequest,
) -> dict[StateKey, DynamicRecord]:
    records: dict[StateKey, DynamicRecord] = {}
    for group in groups:
        for variant in group:
            choice = _best_initial_choice(variant, request)
            if choice is None:
                continue
            initial_route, departure_index = choice
            key = StateKey(
                visited_mask=1 << variant.group_index,
                group_index=variant.group_index,
                variant_index=variant.variant_index,
            )
            records[key] = DynamicRecord(
                distance_km=route_distance_km(initial_route),
                turn_count=_turn_count(initial_route),
                selections=(SelectionIndex(variant.group_index, variant.variant_index),),
                departure_index=departure_index,
            )
    return records


def _best_initial_choice(
    variant: PreparedVariant,
    request: SectionRouteOptimizationRequest,
) -> tuple[list[Coordinate], int | None] | None:
    if not request.departure_candidates:
        return variant.route, None
    choices: list[tuple[list[Coordinate], int]] = []
    for departure_index, departure in enumerate(request.departure_candidates):
        connector = _safe_connector_or_none(departure, variant.route[0], request)
        if connector is None:
            continue
        choices.append((_join_routes([connector, variant.route]), departure_index))
    if not choices:
        return None
    return min(
        choices,
        key=lambda choice: (
            route_distance_km(choice[0]),
            _turn_count(choice[0]),
            choice[1],
        ),
    )


def _extend_record(
    records: dict[StateKey, DynamicRecord],
    key: StateKey,
    record: DynamicRecord,
    groups: list[list[PreparedVariant]],
    transitions: dict[tuple[int, int, int, int], Transition],
) -> None:
    for target_group_index, target_group in enumerate(groups):
        if key.visited_mask & (1 << target_group_index):
            continue
        for target in target_group:
            transition = transitions.get(
                (
                    key.group_index,
                    key.variant_index,
                    target.group_index,
                    target.variant_index,
                )
            )
            if transition is None:
                continue
            candidate_key = StateKey(
                visited_mask=key.visited_mask | (1 << target_group_index),
                group_index=target.group_index,
                variant_index=target.variant_index,
            )
            candidate = DynamicRecord(
                distance_km=record.distance_km + transition.appended_distance_km,
                turn_count=record.turn_count + transition.appended_turn_count,
                selections=(
                    *record.selections,
                    SelectionIndex(target.group_index, target.variant_index),
                ),
                departure_index=record.departure_index,
            )
            current = records.get(candidate_key)
            if current is None or _record_key(candidate) < _record_key(current):
                records[candidate_key] = candidate


def _select_final(
    records: dict[StateKey, DynamicRecord],
    full_mask: int,
    groups: list[list[PreparedVariant]],
    request: SectionRouteOptimizationRequest,
) -> FinalChoice | None:
    choices: list[FinalChoice] = []
    for key, record in records.items():
        if key.visited_mask != full_mask:
            continue
        variant = groups[key.group_index][key.variant_index]
        choices.extend(_final_choices(record, variant, request))
    if not choices:
        return None
    return min(
        choices,
        key=lambda choice: (
            choice.distance_km,
            choice.turn_count,
            tuple(
                (selection.group_index, selection.variant_index)
                for selection in choice.record.selections
            ),
            choice.landing_index if choice.landing_index is not None else -1,
        ),
    )


def _final_choices(
    record: DynamicRecord,
    variant: PreparedVariant,
    request: SectionRouteOptimizationRequest,
) -> list[FinalChoice]:
    if not request.landing_candidates:
        return [
            FinalChoice(
                record=record,
                landing_index=None,
                distance_km=record.distance_km,
                turn_count=record.turn_count,
            )
        ]
    choices: list[FinalChoice] = []
    for landing_index, landing in enumerate(request.landing_candidates):
        connector = _safe_connector_or_none(variant.route[-1], landing, request)
        if connector is None:
            continue
        tail = _join_routes([variant.route[-2:], connector])
        choices.append(
            FinalChoice(
                record=record,
                landing_index=landing_index,
                distance_km=record.distance_km + route_distance_km(connector),
                turn_count=record.turn_count + _turn_count(tail),
            )
        )
    return choices


def _restore_route(
    final: FinalChoice,
    groups: list[list[PreparedVariant]],
    transitions: dict[tuple[int, int, int, int], Transition],
    request: SectionRouteOptimizationRequest,
) -> list[Coordinate]:
    first_index = final.record.selections[0]
    first = groups[first_index.group_index][first_index.variant_index]
    if final.record.departure_index is None:
        route = list(first.route)
    else:
        departure = request.departure_candidates[final.record.departure_index]
        connector = _safe_connector(departure, first.route[0], request)
        route = _join_routes([connector, first.route])
    for source_index, target_index in pairwise(final.record.selections):
        transition = transitions[
            (
                source_index.group_index,
                source_index.variant_index,
                target_index.group_index,
                target_index.variant_index,
            )
        ]
        target = groups[target_index.group_index][target_index.variant_index]
        route = _join_routes([route, transition.connector, target.route])
    if final.landing_index is not None:
        landing = request.landing_candidates[final.landing_index]
        connector = _safe_connector(route[-1], landing, request)
        route = _join_routes([route, connector])
    return route


def _safe_route_or_none(
    route: list[Coordinate],
    request: SectionRouteOptimizationRequest,
) -> list[Coordinate] | None:
    try:
        return build_safe_route(route, request.no_fly_zones)
    except RouteGeometryError:
        return None


def _safe_connector_or_none(
    start: Coordinate,
    end: Coordinate,
    request: SectionRouteOptimizationRequest,
) -> list[Coordinate] | None:
    return _safe_route_or_none([start, end], request)


def _safe_connector(
    start: Coordinate,
    end: Coordinate,
    request: SectionRouteOptimizationRequest,
) -> list[Coordinate]:
    connector = _safe_connector_or_none(start, end, request)
    if connector is None:
        raise RouteGeometryError("Безопасный переход между секциями восстановить не удалось.")
    return connector


def _join_routes(routes: list[list[Coordinate]]) -> list[Coordinate]:
    combined: list[Coordinate] = []
    for route in routes:
        if not route:
            continue
        if combined and combined[-1] == route[0]:
            combined.extend(route[1:])
        else:
            combined.extend(route)
    return combined


def _record_key(record: DynamicRecord) -> tuple[float, int]:
    return record.distance_km, record.turn_count


def _turn_count(route: list[Coordinate]) -> int:
    bearings = [route_bearing_degrees(start, end) for start, end in pairwise(route) if start != end]
    count = 0
    active_direction = 0
    for start, end in pairwise(bearings):
        change = (end - start + 180) % 360 - 180
        if abs(change) <= 0.5:
            active_direction = 0
            continue
        direction = 1 if change > 0 else -1
        if active_direction == 0 or direction != active_direction:
            count += 1
        active_direction = direction
    return count


def _last_route_heading(route: list[Coordinate]) -> float:
    for start, end in reversed(list(pairwise(route))):
        if start != end:
            return route_bearing_degrees(start, end)
    raise RouteGeometryError("Направление в конце секции не определено.")


def _first_route_heading(route: list[Coordinate]) -> float:
    for start, end in pairwise(route):
        if start != end:
            return route_bearing_degrees(start, end)
    raise RouteGeometryError("Направление в начале секции не определено.")


def _heading_difference(first: float, second: float) -> float:
    difference = abs(first - second)
    return min(difference, 360 - difference)
