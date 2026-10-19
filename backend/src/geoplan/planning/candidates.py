from __future__ import annotations

from math import floor

from geoplan.assignment.models import MissionAssignmentCandidate
from geoplan.budget import PlanningCancelled, PlanningTimeExceeded, check_planning_budget
from geoplan.coverage.models import WorkSegment
from geoplan.coverage.resolution import SurveyResolution
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    ErrorCode,
    MissionStageType,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission, MissionStage
from geoplan.errors import PlanningError
from geoplan.flight.altitude import FlightAltitudeEnvelope
from geoplan.flight.duration import MissionDurationCalculator
from geoplan.flight.emergency import FlightPhase, _evaluate_emergency_reachability
from geoplan.flight.energy import _battery_use_percent, _effective_endurance_minutes
from geoplan.flight.envelope import HorizontalAirspeedEnvelope
from geoplan.flight.fixed_wing import FixedWingPhasePlanner, forward_route_inside_airspaces
from geoplan.flight.models import MissionDurationRequest
from geoplan.flight.site_clearance import VerticalSiteClearance
from geoplan.flight.sites import _eligible_departure_sites, _eligible_landing_sites
from geoplan.flight.takeoff_landing import TakeoffLandingEstimator
from geoplan.flight.terrain import VerticalProfilePlanner
from geoplan.flight.vertical import _validate_vertical_airspace, _vertical_profile
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.models import RouteOptimizationRequest, RouteOptimizationResult
from geoplan.navigation.optimizer import RouteOptimizer
from geoplan.navigation.routing import (
    calculate_route_multiplier,
    coordinate_distance_km,
    route_distance_km,
)
from geoplan.planning.camera_diagnostics import CameraMissionDiagnostics
from geoplan.planning.candidate_models import MissionCandidate, OptimizedMissionRoute
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.models import PlanRequest
from geoplan.planning.routes import (
    _combine_routes,
    _departure_route,
    _landing_route,
    _optimize_route,
    _validate_geometry,
)
from geoplan.planning.survey_adapter import MissionSurveyAdapter
from geoplan.planning.survey_phases import survey_edge_mask, survey_flight_phases, survey_stages


def _create_candidate_groups(
    segments: list[WorkSegment],
    drones: list[DroneSpec],
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
) -> list[list[MissionAssignmentCandidate]]:
    groups: list[list[MissionAssignmentCandidate]] = []
    for segment in segments:
        constraints = _constraints_for_segment(segment, request)
        candidates: list[MissionCandidate] = []
        evaluated_candidates: list[MissionCandidate] = []
        errors: list[PlanningError] = []
        for drone in drones:
            candidate, error = _candidate_evaluation(
                segment,
                drone,
                request,
                duration_calculator,
                route_optimizer,
                transition_planner,
                vertical_profile_planner,
                constraints,
            )
            if candidate is not None:
                evaluated_candidates.append(candidate)
                if _candidate_is_feasible(candidate):
                    candidates.append(candidate)
            if error is not None:
                errors.append(error)
        if not candidates:
            if evaluated_candidates:
                raise _battery_endurance_error(segment, evaluated_candidates)
            if errors:
                raise errors[0]
            raise PlanningError(
                code=ErrorCode.UNPLANNABLE_SEGMENT,
                message=(
                    f"Участок {segment.segment_id} не помещается "
                    "в безопасную продолжительность полёта."
                ),
            )
        groups.append(
            [
                MissionAssignmentCandidate(
                    segment_id=segment.segment_id,
                    drone_id=candidate.drone.drone_id,
                    mission=candidate.mission,
                )
                for candidate in candidates
            ]
        )
    return groups


def _candidate_evaluation(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
    constraints: list[NoFlyZone],
) -> tuple[MissionCandidate | None, PlanningError | None]:
    check_planning_budget()
    try:
        segment, request = MissionSurveyAdapter(transition_planner).prepare(segment, drone, request)
        candidate = _create_candidate(
            segment,
            drone,
            request,
            duration_calculator,
            route_optimizer,
            transition_planner,
            vertical_profile_planner,
            constraints,
        )
        check_planning_budget()
        if not FlightAltitudeEnvelope().allows(drone, candidate.mission):
            raise PlanningError(
                code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
                message=f"Маршрут БВС {drone.drone_id} нарушает пределы AGL/MSL.",
            )
        if request.survey_parameters is not None and not SurveyResolution().allows(
            candidate.mission,
            request.survey_parameters.sensor,
            request.survey_parameters.maximum_gsd_cm,
        ):
            raise PlanningError(
                code=ErrorCode.GSD_REQUIREMENT_UNSATISFIED,
                message=f"Камера БВС {drone.drone_id} не обеспечивает требуемый GSD.",
            )
    except PlanningTimeExceeded, PlanningCancelled:
        raise
    except PlanningError as error:
        return None, error
    return candidate, None


def _create_candidate(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    duration_calculator: MissionDurationCalculator,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    vertical_profile_planner: VerticalProfilePlanner,
    constraints: list[NoFlyZone],
) -> MissionCandidate:
    optimized = _best_route_and_sites(
        segment,
        drone,
        request,
        route_optimizer,
        transition_planner,
        constraints,
        duration_calculator,
        vertical_profile_planner,
    )
    full_route = _combine_routes(
        optimized.transit_route,
        optimized.survey_route,
        optimized.landing_route,
    )
    _validate_geometry(full_route, optimized.survey_route, constraints, segment, request)
    estimate = optimized.duration_estimate
    internal_stages = survey_stages(
        segment,
        optimized.survey_route,
        estimate.survey_leg_durations_minutes,
        sum(estimate.survey_leg_durations_minutes)
        if estimate.survey_leg_durations_minutes is not None
        else estimate.survey_duration_minutes,
    )
    service_distance = (
        sum(
            route_distance_km(stage.coordinates)
            for stage in internal_stages
            if stage.stage_type is MissionStageType.SURVEY
        )
        if segment.survey_tracks is not None
        else optimized.survey_distance_km
    )
    service_minutes = sum(
        stage.duration_minutes
        for stage in internal_stages
        if stage.stage_type is MissionStageType.SURVEY
    )
    _validate_route_envelope(drone, optimized, full_route, segment.segment_id)
    vertical_profile = optimized.vertical_profile
    phases = optimized.takeoff_landing
    total_duration = estimate.precise_duration_minutes + phases.duration_minutes
    energy_equivalent_minutes = (
        (vertical_profile.vertical_energy_equivalent_minutes if vertical_profile is not None else 0)
        + phases.energy_surcharge_minutes
        + (optimized.forward_flight.energy_surcharge_minutes if optimized.forward_flight else 0)
    )
    battery_percent = round(
        _battery_use_percent(total_duration + energy_equivalent_minutes, drone),
        2,
    )
    remaining_battery_percent = round(max(0, drone.initial_charge_percent - battery_percent), 2)
    reserve_margin_percent = round(
        remaining_battery_percent - drone.battery_reserve_percent,
        2,
    )
    emergency = optimized.emergency_reachability
    mission = Mission(
        coverage_area_ids=list(segment.area_ids),
        drone_id=drone.drone_id,
        segment_id=segment.segment_id,
        duration_minutes=total_duration,
        distance_km=round(optimized.total_distance_km, 3),
        route=full_route,
        altitude_m=request.mission_altitude_m,
        altitude_reference=request.altitude_reference,
        flat_ground_elevation_m=request.flat_ground_elevation_m,
        vertical_profile=(vertical_profile.points if vertical_profile is not None else []),
        route_altitudes_msl_m=optimized.route_altitudes_msl_m,
        forward_flight_phases=optimized.forward_flight,
        minimum_agl_m=(
            vertical_profile.minimum_agl_m
            if vertical_profile is not None
            else request.survey_height_agl.minimum_m
        ),
        minimum_terrain_elevation_m=(
            vertical_profile.minimum_terrain_elevation_m if vertical_profile is not None else None
        ),
        maximum_terrain_elevation_m=(
            vertical_profile.maximum_terrain_elevation_m if vertical_profile is not None else None
        ),
        maximum_required_climb_rate_mps=(
            vertical_profile.maximum_required_climb_rate_mps
            if vertical_profile is not None
            else None
        ),
        maximum_required_descent_rate_mps=(
            vertical_profile.maximum_required_descent_rate_mps
            if vertical_profile is not None
            else None
        ),
        vertical_distance_m=(
            round(
                (vertical_profile.vertical_distance_m if vertical_profile is not None else 0)
                + phases.vertical_distance_m
                + (2 * request.survey_height_agl.maximum_m if optimized.forward_flight else 0),
                2,
            )
        ),
        vertical_climb_duration_minutes=phases.climb_minutes,
        vertical_descent_duration_minutes=phases.descent_minutes,
        vertical_energy_equivalent_minutes=energy_equivalent_minutes,
        terrain_vertical_uncertainty_m=(request.terrain_vertical_uncertainty_m),
        cruise_speed_kmh=drone.cruise_speed_kmh,
        effective_speed_kmh=optimized.total_distance_km / total_duration * 60,
        estimated_battery_percent=battery_percent,
        starting_battery_percent=drone.initial_charge_percent,
        remaining_battery_percent=remaining_battery_percent,
        battery_reserve_margin_percent=reserve_margin_percent,
        minimum_alternate_margin_percent=(
            round(emergency.minimum_margin_percent, 2) if emergency is not None else None
        ),
        critical_alternate_site_id=(emergency.critical_site_id if emergency is not None else None),
        critical_alternate_route=(emergency.critical_route if emergency is not None else []),
        critical_alternate_start_offset_minutes=(
            emergency.critical_checkpoint_elapsed_minutes if emergency is not None else None
        ),
        critical_alternate_duration_minutes=(
            emergency.critical_diversion_minutes if emergency is not None else None
        ),
        alternate_check_spacing_m=(
            round(emergency.maximum_interval_m, 3) if emergency is not None else None
        ),
        alternate_evaluated_checkpoint_count=(
            emergency.evaluated_checkpoint_count if emergency is not None else None
        ),
        alternate_certification_error_minutes=(
            round(emergency.certification_error_minutes, 6) if emergency is not None else None
        ),
        survey_distance_km=round(service_distance, 3),
        transit_distance_km=round(optimized.total_distance_km - service_distance, 3),
        survey_duration_minutes=service_minutes,
        transit_duration_minutes=total_duration - service_minutes,
        useful_flight_percent=round(service_distance / optimized.total_distance_km * 100, 2),
        image_count=(
            sum(
                floor((route_distance_km(s.coordinates) * 1000 + 1e-7) / segment.trigger_spacing_m)
                + 1
                for s in internal_stages
                if s.stage_type is MissionStageType.SURVEY
            )
            if segment.trigger_spacing_m is not None
            else segment.image_count
        ),
        sensor=request.survey_parameters.sensor if request.survey_parameters else None,
        camera_trigger_spacing_m=segment.trigger_spacing_m,
        minimum_turn_radius_m=request.minimum_turn_radius_m,
        computed_gsd_cm=segment.computed_gsd_cm,
        strip_spacing_m=segment.strip_spacing_m,
        flight_direction_degrees=segment.flight_direction_degrees,
        departure_site=optimized.departure_site,
        landing_site=optimized.landing_site,
        stages=[
            MissionStage(
                stage_type=MissionStageType.TAKEOFF,
                coordinates=[optimized.transit_route[0]],
                duration_minutes=phases.climb_minutes,
            ),
            MissionStage(
                stage_type=MissionStageType.TRANSIT,
                coordinates=optimized.transit_route,
                duration_minutes=(
                    sum(estimate.transit_leg_durations_minutes)
                    if estimate.transit_leg_durations_minutes is not None
                    else max(
                        0, estimate.transit_duration_minutes - estimate.landing_duration_minutes
                    )
                ),
                leg_durations_minutes=estimate.transit_leg_durations_minutes,
            ),
            *internal_stages,
            MissionStage(
                stage_type=MissionStageType.LANDING,
                coordinates=optimized.landing_route,
                duration_minutes=phases.descent_minutes
                + (
                    sum(estimate.landing_leg_durations_minutes)
                    if estimate.landing_leg_durations_minutes is not None
                    else estimate.landing_duration_minutes
                ),
                leg_durations_minutes=estimate.landing_leg_durations_minutes,
            ),
        ],
    )
    if drone.minimum_cruise_speed_kmh is not None or drone.maximum_cruise_speed_kmh is not None:
        envelope = HorizontalAirspeedEnvelope().range_kmh(
            mission, request.wind, include_vertical=True
        )
        if (
            envelope is None
            or (
                drone.minimum_cruise_speed_kmh is not None
                and envelope[0] < drone.minimum_cruise_speed_kmh - 0.05
            )
            or (
                drone.maximum_cruise_speed_kmh is not None
                and envelope[1] > drone.maximum_cruise_speed_kmh + 0.05
            )
        ):
            raise CameraMissionDiagnostics().airspeed_error(request, drone, mission, envelope)
    return MissionCandidate(drone=drone, mission=mission)


def _best_route_and_sites(
    segment: WorkSegment,
    drone: DroneSpec,
    request: PlanRequest,
    route_optimizer: RouteOptimizer,
    transition_planner: SweepTransitionPlanner,
    constraints: list[NoFlyZone],
    duration_calculator: MissionDurationCalculator,
    vertical_profile_planner: VerticalProfilePlanner,
) -> OptimizedMissionRoute:
    departure_sites = _eligible_departure_sites(request, drone.drone_id)
    landing_sites = _eligible_landing_sites(request, drone.drone_id)
    choices: list[OptimizedMissionRoute] = []
    errors: list[PlanningError] = []
    for departure_site in departure_sites:
        departure = departure_site.coordinate if departure_site is not None else segment.route[0]
        optimization_request = RouteOptimizationRequest(
            route=segment.route,
            alternative_routes=segment.alternative_routes,
            departure=departure,
            landings=[site.coordinate for site in landing_sites if site is not None],
            no_fly_zones=constraints,
        )
        try:
            optimized = _optimize_route(
                route_optimizer,
                optimization_request,
                segment.segment_id,
            )
        except PlanningError as error:
            errors.append(error)
            continue
        if request.minimum_turn_radius_m > 0 and not _preserves_curved_route(
            optimized.route,
            segment,
        ):
            errors.append(
                PlanningError(
                    code=ErrorCode.TURN_RADIUS_UNSATISFIED,
                    message=f"Маршрут участка {segment.segment_id} изменён после проверки радиуса.",
                )
            )
            continue
        try:
            transit_route = _departure_route(
                departure,
                optimized.route,
                segment,
                request,
                constraints,
                transition_planner,
            )
        except PlanningError as error:
            errors.append(error)
            continue
        for landing_site in landing_sites:
            landing = landing_site.coordinate if landing_site is not None else optimized.route[-1]
            try:
                landing_route = _landing_route(
                    optimized.route,
                    landing,
                    segment,
                    request,
                    constraints,
                    transition_planner,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            survey_distance = _survey_distance(segment, optimized)
            departure_distance = route_distance_km(transit_route)
            landing_distance = route_distance_km(landing_route)
            transit_distance = departure_distance + landing_distance
            total_distance = transit_distance + survey_distance
            full_route = _combine_routes(transit_route, optimized.route, landing_route)
            try:
                duration_estimate = duration_calculator.calculate(
                    MissionDurationRequest(
                        distance_km=total_distance,
                        cruise_speed_kmh=drone.cruise_speed_kmh,
                        route=full_route,
                        wind=request.wind,
                        speed_limit_kmh=segment.survey_speed_limit_kmh,
                        transit_route=transit_route,
                        transit_distance_km=departure_distance,
                        survey_route=optimized.route,
                        survey_distance_km=survey_distance,
                        landing_route=landing_route,
                        landing_distance_km=landing_distance,
                        survey_leg_speed_limits_kmh=(
                            [
                                segment.survey_speed_limit_kmh if scan else None
                                for scan in survey_edge_mask(segment, optimized.route)
                            ]
                            if segment.survey_tracks is not None
                            else None
                        ),
                    )
                )
            except PlanningError as error:
                errors.append(error)
                continue
            forward = None
            route_altitudes = None
            if drone.fixed_wing_phase_profile is not None:
                departure_times = duration_estimate.transit_leg_durations_minutes
                landing_times = duration_estimate.landing_leg_durations_minutes
                if departure_times is None or landing_times is None:
                    errors.append(
                        PlanningError(
                            code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
                            message="Самолётный профиль требует точные времена плеч перелёта.",
                        )
                    )
                    continue
                try:
                    forward = FixedWingPhasePlanner().plan(
                        drone,
                        request.survey_height_agl.maximum_m,
                        sum(departure_times),
                        sum(landing_times),
                    )
                    route_altitudes = FixedWingPhasePlanner().altitudes(
                        transit_route,
                        optimized.route,
                        landing_route,
                        departure_times,
                        landing_times,
                        request.flat_ground_elevation_m,
                        request.mission_altitude_m,
                    )
                    total_distance = FixedWingPhasePlanner().distance_km(
                        full_route,
                        route_altitudes,
                        request.flat_ground_elevation_m,
                        request.terrain_vertical_uncertainty_m,
                    )
                    transit_distance = total_distance - survey_distance
                    if not forward_route_inside_airspaces(
                        full_route,
                        route_altitudes,
                        request.flat_ground_elevation_m,
                        request.terrain_vertical_uncertainty_m,
                        request.allowed_airspaces,
                    ):
                        raise PlanningError(
                            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
                            message="Самолётный набор или снижение выходят "
                            "за разрешённый диапазон высот.",
                        )
                except PlanningError as error:
                    errors.append(error)
                    continue
            try:
                vertical_profile = _vertical_profile(
                    full_route,
                    drone,
                    request,
                    vertical_profile_planner,
                    segment.segment_id,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            try:
                _validate_vertical_airspace(vertical_profile, request, segment.segment_id)
            except PlanningError as error:
                errors.append(error)
                continue
            heights = request.survey_height_agl
            takeoff_landing = TakeoffLandingEstimator().estimate(
                drone,
                (vertical_profile.points[0].target_agl_m + request.terrain_vertical_uncertainty_m)
                if vertical_profile is not None
                else heights.maximum_m,
                (vertical_profile.points[-1].target_agl_m + request.terrain_vertical_uncertainty_m)
                if vertical_profile is not None
                else heights.maximum_m,
            )
            if drone.takeoff_landing_profile is not None:
                clear = True
                for index in (0, -1):
                    point = full_route[index]
                    ground = (
                        vertical_profile.points[index].terrain_elevation_m
                        if vertical_profile
                        else request.flat_ground_elevation_m
                    )
                    altitude = (
                        vertical_profile.points[index].target_altitude_msl
                        if vertical_profile
                        else request.mission_altitude_m
                    )
                    clear &= VerticalSiteClearance().allows(
                        point,
                        ground,
                        altitude,
                        heights.maximum_m,
                        request.terrain_vertical_uncertainty_m,
                        request.allowed_airspaces,
                        request.no_fly_zones,
                        request.obstacles,
                    )
                if not clear:
                    errors.append(
                        PlanningError(
                            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
                            message="Вертикальная колонна взлёта или посадки не подтверждена: "
                            "проверьте площадки, препятствия и постоянные разрешённые диапазоны.",
                        )
                    )
                    continue
            try:
                emergency_reachability = _evaluate_emergency_reachability(
                    phases=[
                        FlightPhase(
                            route=transit_route,
                            distance_km=departure_distance,
                            ground_speed_limit_kmh=None,
                        ),
                        *survey_flight_phases(segment, optimized.route, survey_distance),
                        FlightPhase(
                            route=landing_route,
                            distance_km=landing_distance,
                            ground_speed_limit_kmh=None,
                        ),
                    ],
                    drone=drone,
                    request=request,
                    constraints=constraints,
                    segment_id=segment.segment_id,
                    initial_elapsed_minutes=takeoff_landing.climb_minutes,
                    additional_energy_minutes=(
                        takeoff_landing.energy_surcharge_minutes
                        + (
                            vertical_profile.vertical_energy_equivalent_minutes
                            if vertical_profile
                            else 0
                        )
                    ),
                    alternate_descent_minutes=takeoff_landing.descent_minutes,
                )
            except PlanningError as error:
                errors.append(error)
                continue
            choices.append(
                OptimizedMissionRoute(
                    survey_route=optimized.route,
                    transit_route=transit_route,
                    landing_route=landing_route,
                    departure_site=departure_site,
                    landing_site=landing_site,
                    survey_distance_km=survey_distance,
                    transit_distance_km=transit_distance,
                    total_distance_km=total_distance,
                    duration_estimate=duration_estimate,
                    emergency_reachability=emergency_reachability,
                    vertical_profile=vertical_profile,
                    takeoff_landing=takeoff_landing,
                    forward_flight=forward,
                    route_altitudes_msl_m=route_altitudes,
                )
            )
    if choices:
        return min(choices, key=_route_choice_key)
    if errors:
        raise errors[0]
    raise PlanningError(
        code=ErrorCode.NO_ELIGIBLE_FLIGHT_SITE,
        message=f"Для участка {segment.segment_id} нет допустимой пары площадок.",
    )


def _preserves_curved_route(route: list[Coordinate], segment: WorkSegment) -> bool:
    return any(
        route == candidate or route == list(reversed(candidate))
        for candidate in [segment.route, *segment.alternative_routes]
    )


def _route_choice_key(choice: OptimizedMissionRoute) -> tuple[float, str, str]:
    departure_id = choice.departure_site.site_id if choice.departure_site is not None else ""
    landing_id = choice.landing_site.site_id if choice.landing_site is not None else ""
    vertical_minutes = (
        choice.vertical_profile.vertical_energy_equivalent_minutes
        if choice.vertical_profile is not None
        else 0
    )
    return (
        choice.duration_estimate.precise_duration_minutes
        + vertical_minutes
        + choice.takeoff_landing.energy_equivalent_minutes
        + (choice.forward_flight.energy_surcharge_minutes if choice.forward_flight else 0),
        departure_id,
        landing_id,
    )


def _survey_distance(
    segment: WorkSegment,
    optimized: RouteOptimizationResult,
) -> float:
    if optimized.selected_route_index > 0:
        return route_distance_km(optimized.route)
    return segment.distance_km * calculate_route_multiplier(
        segment.route,
        optimized.route,
    )


def _validate_route_envelope(
    drone: DroneSpec,
    optimized: OptimizedMissionRoute,
    route: list[Coordinate],
    segment_id: str,
) -> None:
    if (
        drone.maximum_route_distance_km is not None
        and optimized.total_distance_km > drone.maximum_route_distance_km
    ):
        raise PlanningError(
            code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
            message=(
                f"Маршрут участка {segment_id} длиннее паспортного предела БВС {drone.drone_id}."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REDUCE_MISSION_SCOPE,
                    message="Разделите участок на более короткие вылеты.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_route_distance_km",
                    current_value=round(optimized.total_distance_km, 3),
                    required_value=drone.maximum_route_distance_km,
                    unit="km",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        )
    if drone.maximum_control_range_km is None:
        return
    departure = route[0]
    maximum_radius_km = max(coordinate_distance_km(departure, point) for point in route)
    if maximum_radius_km <= drone.maximum_control_range_km:
        return
    raise PlanningError(
        code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
        message=(
            f"Маршрут участка {segment_id} выходит за подтверждённую дальность "
            f"управления БВС {drone.drone_id}."
        ),
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.REDUCE_MISSION_SCOPE,
                message="Перенесите площадку ближе к зоне или разделите операцию.",
                confidence=RecommendationConfidence.EXACT,
                parameter="maximum_control_range_km",
                current_value=round(maximum_radius_km, 3),
                required_value=drone.maximum_control_range_km,
                unit="km",
                affected_ids=[segment_id, drone.drone_id],
            )
        ],
    )


def _candidate_is_feasible(candidate: MissionCandidate) -> bool:
    available_percent = (
        candidate.drone.initial_charge_percent - candidate.drone.battery_reserve_percent
    )
    return candidate.mission.estimated_battery_percent <= available_percent


def _battery_endurance_error(
    segment: WorkSegment,
    candidates: list[MissionCandidate],
) -> PlanningError:
    best = min(
        candidates,
        key=lambda candidate: (
            (
                candidate.mission.duration_minutes
                + candidate.mission.vertical_energy_equivalent_minutes
            )
            / (
                (candidate.drone.initial_charge_percent - candidate.drone.battery_reserve_percent)
                / 100
            )
            - _effective_endurance_minutes(candidate.drone),
            candidate.drone.drone_id,
        ),
    )
    usable_fraction = (best.drone.initial_charge_percent - best.drone.battery_reserve_percent) / 100
    required_effective_endurance = (
        best.mission.duration_minutes + best.mission.vertical_energy_equivalent_minutes
    ) / usable_fraction
    required_endurance = (
        required_effective_endurance
        * best.drone.energy_consumption_multiplier
        / (best.drone.battery_health_percent / 100)
    )
    recommendations: list[FeasibilityRecommendation] = []
    if best.drone.initial_charge_percent < 100:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.RECHARGE_BATTERY,
                message=(
                    f"Зарядите БВС {best.drone.drone_id} выше текущих "
                    f"{best.drone.initial_charge_percent:g}% либо назначьте другой аппарат."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="initial_charge_percent",
                current_value=best.drone.initial_charge_percent,
                required_value=min(
                    100,
                    round(
                        best.mission.estimated_battery_percent + best.drone.battery_reserve_percent,
                        2,
                    ),
                ),
                unit="percent",
                affected_ids=[segment.segment_id, best.drone.drone_id],
            )
        )
    recommendations.extend(
        [
            FeasibilityRecommendation(
                code=RecommendationCode.EXTEND_ENDURANCE,
                message=(
                    f"Для БВС {best.drone.drone_id} требуется паспортная продолжительность "
                    f"не менее {required_endurance:.1f} мин при сохранении резерва "
                    f"{best.drone.battery_reserve_percent:g}%."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="maximum_flight_minutes",
                current_value=best.drone.maximum_flight_minutes,
                required_value=round(required_endurance, 2),
                unit="minutes",
                affected_ids=[segment.segment_id, best.drone.drone_id],
            ),
            FeasibilityRecommendation(
                code=RecommendationCode.REDUCE_MISSION_SCOPE,
                message=("Разделите участок на более короткие вылеты без снижения резерва АКБ."),
                confidence=RecommendationConfidence.ESTIMATE,
                affected_ids=[segment.segment_id],
            ),
        ]
    )
    return PlanningError(
        code=ErrorCode.UNPLANNABLE_SEGMENT,
        message=(
            f"Участок {segment.segment_id} не помещается в безопасную продолжительность полёта."
        ),
        recommendations=recommendations,
    )
