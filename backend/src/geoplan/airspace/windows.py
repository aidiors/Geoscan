from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

from geoplan.coverage.resolution import SurveyResolution
from geoplan.domain.airspace import AllowedAirspace, NoFlyZone, RestrictionValidity
from geoplan.domain.enums import AirframeType, AltitudeReference, ValidityMode
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.domain.terrain import VerticalProfilePoint
from geoplan.flight.altitude import FlightAltitudeEnvelope
from geoplan.flight.energy import _battery_use_percent
from geoplan.flight.takeoff_landing import TakeoffLandingEstimator
from geoplan.navigation.routing import (
    route_is_clear_of_no_fly_zones,
    route_is_inside_allowed_airspaces,
)
from geoplan.planning.models import PlanRequest
from geoplan.scheduling.models import ScheduleTimeWindow, VerticalLayerOption
from geoplan.scheduling.vertical import apply_vertical_layer


def _vertical_layers_by_segment_id(
    missions: list[Mission],
    request: PlanRequest,
) -> dict[str, list[VerticalLayerOption]]:
    drones = {drone.drone_id: drone for drone in request.drones}
    return {
        mission.segment_id: _vertical_layer_options(mission, drones[mission.drone_id], request)
        for mission in missions
    }


def _vertical_layer_options(
    mission: Mission,
    drone: DroneSpec,
    request: PlanRequest,
) -> list[VerticalLayerOption]:
    baseline = VerticalLayerOption(
        offset_m=0,
        extra_energy_equivalent_minutes=0,
        extra_battery_percent=0,
    )
    profile = drone.takeoff_landing_profile
    climb_rate = profile.climb_rate_mps if profile else drone.maximum_climb_rate_mps
    descent_rate = profile.descent_rate_mps if profile else drone.maximum_descent_rate_mps
    if (
        request.maximum_vertical_deconfliction_m <= 0
        or climb_rate is None
        or descent_rate is None
        or _has_altitude_dependent_temporal_restrictions(request)
        or drone.autopilot_profile.airframe
        in {AirframeType.FIXED_WING, AirframeType.VTOL_FIXED_ROTOR}
    ):
        return [baseline]
    options = [baseline]
    offset = request.vertical_deconfliction_step_m
    while offset <= request.maximum_vertical_deconfliction_m + 1e-9:
        climb_minutes = offset / climb_rate / 60
        descent_minutes = offset / descent_rate / 60
        extra_minutes = climb_minutes + descent_minutes
        surcharge = (
            TakeoffLandingEstimator().estimate(drone, offset, offset).energy_surcharge_minutes
        )
        extra_battery = _battery_use_percent(extra_minutes + surcharge, drone)
        option = VerticalLayerOption(
            offset_m=round(offset, 3),
            climb_duration_minutes=round(climb_minutes, 6),
            descent_duration_minutes=round(descent_minutes, 6),
            extra_energy_equivalent_minutes=round(surcharge, 6),
            extra_battery_percent=round(extra_battery, 6),
        )
        shifted = apply_vertical_layer(mission, option)
        if _vertical_layer_is_feasible(shifted, option, request):
            options.append(option)
        offset += request.vertical_deconfliction_step_m
    return options


def _has_altitude_dependent_temporal_restrictions(request: PlanRequest) -> bool:
    timed_airspace = any(
        airspace.validity is not None and airspace.validity.mode is ValidityMode.TIME_WINDOW
        for airspace in request.allowed_airspaces
    )
    selective_no_fly = any(
        zone.minimum_altitude_m is not None
        or zone.maximum_altitude_m is not None
        or (zone.validity is not None and zone.validity.mode is ValidityMode.TIME_WINDOW)
        for zone in request.no_fly_zones
    )
    return timed_airspace or selective_no_fly


def _vertical_layer_is_feasible(
    mission: Mission,
    option: VerticalLayerOption,
    request: PlanRequest,
) -> bool:
    survey = request.survey_parameters
    if survey is not None and not SurveyResolution().allows(
        mission, mission.sensor or survey.sensor, survey.maximum_gsd_cm
    ):
        return False
    drone = next(drone for drone in request.drones if drone.drone_id == mission.drone_id)
    if not FlightAltitudeEnvelope().allows(drone, mission):
        return False
    if mission.battery_reserve_margin_percent is None or mission.battery_reserve_margin_percent < 0:
        return False
    if (
        mission.minimum_alternate_margin_percent is not None
        and mission.minimum_alternate_margin_percent < 0
    ):
        return False
    return not request.allowed_airspaces or _mission_route_inside_airspaces(
        mission,
        request.allowed_airspaces,
    )


def _schedule_time_windows(
    request: PlanRequest,
    missions: list[Mission],
) -> dict[str, list[ScheduleTimeWindow]]:
    if request.planning_start_time is None:
        return {}
    has_temporal_constraint = request.planning_end_time is not None or any(
        item.validity is not None and item.validity.mode is ValidityMode.TIME_WINDOW
        for item in [*request.allowed_airspaces, *request.no_fly_zones]
    )
    if not has_temporal_constraint:
        return {}
    horizon_minutes = _planning_horizon_minutes(request, missions)
    windows: dict[str, list[ScheduleTimeWindow]] = {}
    for mission in missions:
        mission_windows = _mission_time_windows(mission, request, horizon_minutes)
        if mission_windows is not None:
            windows[mission.segment_id] = mission_windows
    return windows


def _mission_time_windows(
    mission: Mission,
    request: PlanRequest,
    horizon_minutes: float,
) -> list[ScheduleTimeWindow] | None:
    compatible_airspaces = [
        airspace
        for airspace in request.allowed_airspaces
        if any(
            airspace.minimum_altitude_m <= altitude <= airspace.maximum_altitude_m
            for altitude in _mission_altitudes(mission, airspace.altitude_reference)
        )
    ]
    relevant_no_fly_zones = [
        zone
        for zone in request.no_fly_zones
        if _zone_applies_to_mission(zone, mission)
        and not route_is_clear_of_no_fly_zones(mission.route, [zone])
    ]
    timed_items = [
        item
        for item in [*compatible_airspaces, *relevant_no_fly_zones]
        if item.validity is not None and item.validity.mode is ValidityMode.TIME_WINDOW
    ]
    if not timed_items and request.planning_end_time is None:
        return None
    boundaries = {0.0, horizon_minutes}
    for item in timed_items:
        validity = item.validity
        if validity is None:
            continue
        interval = _validity_interval(validity, request)
        if interval is not None:
            boundaries.update(interval)
    legal: list[ScheduleTimeWindow] = []
    for start, end in pairwise(sorted(boundaries)):
        if end <= start or start >= horizon_minutes:
            continue
        clipped_end = min(end, horizon_minutes)
        midpoint = (start + clipped_end) / 2
        if _mission_is_legal_at_offset(
            mission,
            compatible_airspaces,
            relevant_no_fly_zones,
            midpoint,
            request,
        ):
            _append_or_merge_window(legal, start, clipped_end)
    return legal


def _mission_altitudes_msl(mission: Mission) -> list[float]:
    if mission.vertical_profile:
        return [point.target_altitude_msl for point in mission.vertical_profile]
    return [mission.altitude_m]


def _mission_altitudes(mission: Mission, reference: AltitudeReference) -> list[float]:
    if reference is AltitudeReference.MSL:
        return _mission_altitudes_msl(mission)
    if mission.vertical_profile:
        return [point.target_agl_m for point in mission.vertical_profile]
    if mission.altitude_reference is AltitudeReference.AGL:
        return [mission.altitude_m]
    return []


def _zone_applies_to_mission(zone: NoFlyZone, mission: Mission) -> bool:
    if zone.minimum_altitude_m is None or zone.maximum_altitude_m is None:
        return True
    altitudes = _mission_altitudes(mission, zone.altitude_reference)
    return any(
        zone.minimum_altitude_m <= altitude <= zone.maximum_altitude_m for altitude in altitudes
    )


def _planning_horizon_minutes(request: PlanRequest, missions: list[Mission]) -> float:
    planning_start = request.planning_start_time
    if planning_start is None:
        return 0
    if request.planning_end_time is not None:
        return (request.planning_end_time - planning_start).total_seconds() / 60
    latest_boundary = max(
        (
            (item.validity.valid_to - planning_start).total_seconds() / 60
            for item in [*request.allowed_airspaces, *request.no_fly_zones]
            if item.validity is not None
            and item.validity.mode is ValidityMode.TIME_WINDOW
            and item.validity.valid_to is not None
        ),
        default=0.0,
    )
    serial_workload = sum(mission.duration_minutes for mission in missions)
    turnarounds = request.turnaround_minutes * max(0, len(missions) - 1)
    return max(1.0, latest_boundary + serial_workload + turnarounds + 60)


def _validity_interval(
    validity: RestrictionValidity,
    request: PlanRequest,
) -> tuple[float, float] | None:
    planning_start = request.planning_start_time
    if validity.valid_from is None or validity.valid_to is None or planning_start is None:
        return None
    start = max(0.0, (validity.valid_from - planning_start).total_seconds() / 60)
    end = (validity.valid_to - planning_start).total_seconds() / 60
    return (start, end) if end > start else None


def _mission_is_legal_at_offset(
    mission: Mission,
    airspaces: list[AllowedAirspace],
    no_fly_zones: list[NoFlyZone],
    offset_minutes: float,
    request: PlanRequest,
) -> bool:
    planning_start = request.planning_start_time
    if planning_start is None:
        return True
    at_time = planning_start + timedelta(minutes=offset_minutes)
    active_airspaces = [
        airspace for airspace in airspaces if _validity_is_active(airspace.validity, at_time)
    ]
    if request.allowed_airspaces and (
        not active_airspaces or not _mission_route_inside_airspaces(mission, active_airspaces)
    ):
        return False
    return not any(_validity_is_active(zone.validity, at_time) for zone in no_fly_zones)


def _mission_route_inside_airspaces(
    mission: Mission,
    airspaces: list[AllowedAirspace],
) -> bool:
    if not mission.vertical_profile:
        compatible = [
            airspace
            for airspace in airspaces
            if any(
                airspace.minimum_altitude_m <= altitude <= airspace.maximum_altitude_m
                for altitude in _mission_altitudes(mission, airspace.altitude_reference)
            )
        ]
        return bool(compatible) and route_is_inside_allowed_airspaces(mission.route, compatible)
    for start, end in pairwise(mission.vertical_profile):
        compatible = [
            airspace
            for airspace in airspaces
            if airspace.minimum_altitude_m <= _profile_altitude(start, airspace.altitude_reference)
            and _profile_altitude(start, airspace.altitude_reference) <= airspace.maximum_altitude_m
            and airspace.minimum_altitude_m
            <= _profile_altitude(end, airspace.altitude_reference)
            <= airspace.maximum_altitude_m
        ]
        if not compatible or not route_is_inside_allowed_airspaces(
            [start.coordinate, end.coordinate],
            compatible,
        ):
            return False
    return True


def _profile_altitude(
    point: VerticalProfilePoint,
    reference: AltitudeReference,
) -> float:
    return point.target_agl_m if reference is AltitudeReference.AGL else point.target_altitude_msl


def _validity_is_active(
    validity: RestrictionValidity | None,
    at_time: datetime,
) -> bool:
    if validity is None or validity.mode is ValidityMode.PERMANENT:
        return True
    if validity.valid_from is None or validity.valid_to is None:
        return False
    return validity.valid_from <= at_time <= validity.valid_to


def _append_or_merge_window(
    windows: list[ScheduleTimeWindow],
    start: float,
    end: float,
) -> None:
    if windows and abs(windows[-1].end_offset_minutes - start) <= 1e-9:
        windows[-1] = windows[-1].model_copy(update={"end_offset_minutes": end})
        return
    windows.append(
        ScheduleTimeWindow(
            start_offset_minutes=start,
            end_offset_minutes=end,
        )
    )
