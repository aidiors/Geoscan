from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    AltitudeReference,
    ErrorCode,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.flight.duration import _route_duration_minutes
from geoplan.flight.energy import _effective_endurance_minutes
from geoplan.flight.sites import _eligible_alternate_sites
from geoplan.navigation.connections import _safe_route
from geoplan.navigation.routing import (
    coordinate_distance_km,
    route_distance_km,
    route_is_inside_allowed_airspaces,
)
from geoplan.planning.models import PlanRequest

ALTERNATE_CERTIFICATION_TOLERANCE_MINUTES = 0.1


ALTERNATE_MINIMUM_INTERVAL_M = 1.0


ALTERNATE_MAXIMUM_REFINEMENT_DEPTH = 20


@dataclass(frozen=True)
class EmergencyReachability:
    minimum_margin_percent: float
    critical_site_id: str
    critical_route: list[Coordinate]
    critical_checkpoint_elapsed_minutes: float
    critical_diversion_minutes: float
    certification_error_minutes: float
    maximum_interval_m: float
    evaluated_checkpoint_count: int


@dataclass(frozen=True)
class FlightPhase:
    route: list[Coordinate]
    distance_km: float
    ground_speed_limit_kmh: float | None


@dataclass(frozen=True)
class EmergencyCheckpoint:
    coordinate: Coordinate
    elapsed_minutes: float


@dataclass(frozen=True)
class EvaluatedEmergencyCheckpoint:
    checkpoint: EmergencyCheckpoint
    site: FlightSite
    diversion_minutes: float
    diversion_route: list[Coordinate]

    @property
    def required_minutes(self) -> float:
        return self.checkpoint.elapsed_minutes + self.diversion_minutes


@dataclass(frozen=True)
class EmergencyIntervalCertificate:
    maximum_required_minutes: float
    maximum_interval_m: float


def _evaluate_emergency_reachability(
    phases: list[FlightPhase],
    drone: DroneSpec,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    segment_id: str,
) -> EmergencyReachability | None:
    alternate_sites = _eligible_alternate_sites(request, drone.drone_id)
    if not request.alternate_landing_sites:
        return None
    if not alternate_sites:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=f"Для БВС {drone.drone_id} нет назначенной резервной площадки.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_ALTERNATE_SITE,
                    message=(
                        f"Назначьте БВС {drone.drone_id} резервную площадку, "
                        "доступную из любой части маршрута."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="alternate_landing_sites",
                    affected_ids=[drone.drone_id, segment_id],
                )
            ],
        )

    checkpoints = _emergency_route_nodes(phases, drone, request.wind)
    available_minutes = (
        _effective_endurance_minutes(drone)
        * (drone.initial_charge_percent - drone.battery_reserve_percent)
        / 100
    )
    minimum_ground_speed_kmh = _minimum_possible_ground_speed_kmh(drone, request.wind)
    evaluated: list[EvaluatedEmergencyCheckpoint] = []

    def evaluate(checkpoint: EmergencyCheckpoint) -> EvaluatedEmergencyCheckpoint:
        diversion = _fastest_emergency_diversion(
            checkpoint.coordinate,
            alternate_sites,
            drone,
            request,
            constraints,
            segment_id,
        )
        if diversion is None:
            raise PlanningError(
                code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
                message=(
                    f"Из контрольной точки маршрута участка {segment_id} "
                    "нет безопасного пути к резервной площадке."
                ),
            )
        site, diversion_minutes, diversion_route = diversion
        result = EvaluatedEmergencyCheckpoint(
            checkpoint=checkpoint,
            site=site,
            diversion_minutes=diversion_minutes,
            diversion_route=diversion_route,
        )
        evaluated.append(result)
        return result

    endpoint_evaluations = [evaluate(checkpoint) for checkpoint in checkpoints]
    certificates = [
        _certify_emergency_interval(
            start,
            end,
            evaluate,
            minimum_ground_speed_kmh,
        )
        for start, end in pairwise(endpoint_evaluations)
    ]
    if not evaluated:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=f"Для участка {segment_id} не удалось проверить резервную посадку.",
        )
    critical = max(
        evaluated,
        key=lambda item: (item.required_minutes, item.site.site_id),
    )
    certified_required_minutes = max(
        critical.required_minutes,
        *(certificate.maximum_required_minutes for certificate in certificates),
    )
    minimum_margin_minutes = available_minutes - certified_required_minutes
    certification_error_minutes = certified_required_minutes - critical.required_minutes
    maximum_interval_m = max(
        (certificate.maximum_interval_m for certificate in certificates),
        default=0,
    )
    margin_percent = minimum_margin_minutes / _effective_endurance_minutes(drone) * 100
    if margin_percent < 0:
        usable_fraction = (drone.initial_charge_percent - drone.battery_reserve_percent) / 100
        required_effective_endurance = certified_required_minutes / usable_fraction
        required_endurance = (
            required_effective_endurance
            * drone.energy_consumption_multiplier
            / (drone.battery_health_percent / 100)
        )
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=(
                f"Участок {segment_id}: уход на резервную площадку "
                f"{critical.site.name} нарушает резерв АКБ на "
                f"{abs(margin_percent):.1f} п.п."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.EXTEND_ENDURANCE,
                    message=(
                        f"Для гарантированного ухода требуется продолжительность полёта "
                        f"не менее {required_endurance:.1f} мин при текущем резерве."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_flight_minutes",
                    current_value=drone.maximum_flight_minutes,
                    required_value=round(required_endurance, 2),
                    unit="minutes",
                    affected_ids=[drone.drone_id, segment_id, critical.site.site_id],
                ),
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_ALTERNATE_SITE,
                    message="Добавьте более близкую резервную площадку без уменьшения резерва АКБ.",
                    confidence=RecommendationConfidence.ESTIMATE,
                    parameter="alternate_landing_sites",
                    affected_ids=[segment_id],
                ),
            ],
        )
    return EmergencyReachability(
        minimum_margin_percent=margin_percent,
        critical_site_id=critical.site.site_id,
        critical_route=critical.diversion_route,
        critical_checkpoint_elapsed_minutes=critical.checkpoint.elapsed_minutes,
        critical_diversion_minutes=critical.diversion_minutes,
        certification_error_minutes=certification_error_minutes,
        maximum_interval_m=maximum_interval_m,
        evaluated_checkpoint_count=len(evaluated),
    )


def _emergency_route_nodes(
    phases: list[FlightPhase],
    drone: DroneSpec,
    wind: WindCondition | None,
) -> list[EmergencyCheckpoint]:
    checkpoints: list[EmergencyCheckpoint] = []
    elapsed_minutes = 0.0
    for phase in phases:
        if phase.distance_km == 0:
            continue
        if not checkpoints or checkpoints[-1].coordinate != phase.route[0]:
            checkpoints.append(
                EmergencyCheckpoint(
                    coordinate=phase.route[0],
                    elapsed_minutes=elapsed_minutes,
                )
            )
        geometric_distance = route_distance_km(phase.route)
        if geometric_distance == 0:
            continue
        for start, end in pairwise(phase.route):
            leg_geometric_distance = coordinate_distance_km(start, end)
            if leg_geometric_distance == 0:
                continue
            leg_distance = phase.distance_km * leg_geometric_distance / geometric_distance
            elapsed_minutes += _route_duration_minutes(
                [start, end],
                leg_distance,
                drone.cruise_speed_kmh,
                wind,
                phase.ground_speed_limit_kmh,
            )
            checkpoints.append(
                EmergencyCheckpoint(
                    coordinate=end,
                    elapsed_minutes=elapsed_minutes,
                )
            )
    return checkpoints


def _certify_emergency_interval(
    start: EvaluatedEmergencyCheckpoint,
    end: EvaluatedEmergencyCheckpoint,
    evaluate: Callable[[EmergencyCheckpoint], EvaluatedEmergencyCheckpoint],
    minimum_ground_speed_kmh: float,
    depth: int = 0,
) -> EmergencyIntervalCertificate:
    distance_km = coordinate_distance_km(
        start.checkpoint.coordinate,
        end.checkpoint.coordinate,
    )
    upper_bound = _emergency_interval_upper_bound(
        start,
        end,
        distance_km,
        minimum_ground_speed_kmh,
    )
    observed = max(start.required_minutes, end.required_minutes)
    if (
        upper_bound - observed <= ALTERNATE_CERTIFICATION_TOLERANCE_MINUTES
        or distance_km * 1_000 <= ALTERNATE_MINIMUM_INTERVAL_M
        or depth >= ALTERNATE_MAXIMUM_REFINEMENT_DEPTH
    ):
        return EmergencyIntervalCertificate(
            maximum_required_minutes=upper_bound,
            maximum_interval_m=distance_km * 1_000,
        )
    midpoint = evaluate(
        EmergencyCheckpoint(
            coordinate=_interpolate_coordinate(
                start.checkpoint.coordinate,
                end.checkpoint.coordinate,
                0.5,
            ),
            elapsed_minutes=(start.checkpoint.elapsed_minutes + end.checkpoint.elapsed_minutes) / 2,
        )
    )
    left = _certify_emergency_interval(
        start,
        midpoint,
        evaluate,
        minimum_ground_speed_kmh,
        depth + 1,
    )
    right = _certify_emergency_interval(
        midpoint,
        end,
        evaluate,
        minimum_ground_speed_kmh,
        depth + 1,
    )
    return EmergencyIntervalCertificate(
        maximum_required_minutes=max(
            left.maximum_required_minutes,
            right.maximum_required_minutes,
        ),
        maximum_interval_m=max(left.maximum_interval_m, right.maximum_interval_m),
    )


def _emergency_interval_upper_bound(
    start: EvaluatedEmergencyCheckpoint,
    end: EvaluatedEmergencyCheckpoint,
    distance_km: float,
    minimum_ground_speed_kmh: float,
) -> float:
    elapsed_delta = end.checkpoint.elapsed_minutes - start.checkpoint.elapsed_minutes
    diversion_variation = distance_km / minimum_ground_speed_kmh * 60
    increasing_slope = elapsed_delta + diversion_variation
    decreasing_origin = end.required_minutes + diversion_variation
    denominator = increasing_slope + diversion_variation
    intersection = (
        min(1.0, max(0.0, (decreasing_origin - start.required_minutes) / denominator))
        if denominator > 0
        else 0
    )
    intersection_bound = min(
        start.required_minutes + increasing_slope * intersection,
        decreasing_origin - diversion_variation * intersection,
    )
    return max(start.required_minutes, end.required_minutes, intersection_bound)


def _minimum_possible_ground_speed_kmh(
    drone: DroneSpec,
    wind: WindCondition | None,
) -> float:
    wind_speed_kmh = wind.speed_mps * 3.6 if wind is not None else 0
    minimum_speed = drone.cruise_speed_kmh - wind_speed_kmh
    if minimum_speed <= 0:
        raise PlanningError(
            code=ErrorCode.ALTERNATE_LANDING_UNREACHABLE,
            message=(
                f"Для БВС {drone.drone_id} нельзя сертифицировать аварийный уход: "
                "скорость ветра не оставляет положительной путевой скорости во всех направлениях."
            ),
        )
    return minimum_speed


def _interpolate_coordinate(start: Coordinate, end: Coordinate, fraction: float) -> Coordinate:
    return Coordinate(
        longitude=start.longitude + (end.longitude - start.longitude) * fraction,
        latitude=start.latitude + (end.latitude - start.latitude) * fraction,
    )


def _fastest_emergency_diversion(
    coordinate: Coordinate,
    alternate_sites: list[FlightSite],
    drone: DroneSpec,
    request: PlanRequest,
    constraints: list[NoFlyZone],
    segment_id: str,
) -> tuple[FlightSite, float, list[Coordinate]] | None:
    choices: list[tuple[FlightSite, float, list[Coordinate]]] = []
    for site in alternate_sites:
        try:
            route = _safe_route(
                [coordinate, site.coordinate],
                constraints,
                segment_id,
            )
        except PlanningError:
            continue
        if not _route_is_inside_configured_airspace(route, request):
            continue
        distance_km = route_distance_km(route)
        if distance_km == 0:
            choices.append((site, 0, route))
            continue
        try:
            duration_minutes = _route_duration_minutes(
                route,
                distance_km,
                drone.cruise_speed_kmh,
                request.wind,
            )
        except PlanningError:
            continue
        choices.append((site, duration_minutes, route))
    if not choices:
        return None
    return min(choices, key=lambda choice: (choice[1], choice[0].site_id))


def _route_is_inside_configured_airspace(
    route: list[Coordinate],
    request: PlanRequest,
) -> bool:
    if not request.allowed_airspaces:
        return True
    if request.altitude_reference is AltitudeReference.AGL:
        return route_is_inside_allowed_airspaces(route, request.allowed_airspaces)
    compatible = [
        airspace
        for airspace in request.allowed_airspaces
        if airspace.altitude_reference is request.altitude_reference
        and airspace.minimum_altitude_m <= request.mission_altitude_m <= airspace.maximum_altitude_m
    ]
    return bool(compatible) and route_is_inside_allowed_airspaces(route, compatible)
