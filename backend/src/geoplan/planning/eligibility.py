from __future__ import annotations

from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    ErrorCode,
    MissionTask,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.errors import PlanningError
from geoplan.flight.sites import (
    _configured_departure_sites,
    _configured_primary_landing_sites,
    _site_supports_drone,
)
from geoplan.planning.models import PlanRequest


def _drones_capable_of_task(
    drones: list[DroneSpec],
    task: MissionTask,
) -> list[DroneSpec]:
    capable_drones = [
        drone
        for drone in drones
        if task in drone.supported_tasks
        and (
            not drone.payloads or any(task in payload.supported_tasks for payload in drone.payloads)
        )
    ]
    if capable_drones:
        return capable_drones
    raise PlanningError(
        code=ErrorCode.NO_CAPABLE_DRONE,
        message="Для выбранного типа работ нет подходящего БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ADD_CAPABLE_DRONE,
                message=f"Добавьте или включите БВС, поддерживающий задачу {task.value}.",
                confidence=RecommendationConfidence.EXACT,
                parameter="supported_tasks",
                affected_ids=[task.value],
            )
        ],
    )


def _drones_with_available_resources(drones: list[DroneSpec]) -> list[DroneSpec]:
    eligible = [drone for drone in drones if drone.resource_limit().allows(1, 1e-6)]
    if eligible:
        return eligible
    raise PlanningError(
        code=ErrorCode.NO_CAPABLE_DRONE,
        message="Нет доступных БВС: проверьте готовые комплекты АКБ и остаток до обслуживания.",
    )


def _drones_capable_of_environment(
    drones: list[DroneSpec],
    request: PlanRequest,
) -> list[DroneSpec]:
    capable = [
        drone
        for drone in drones
        if _drone_supports_altitude(drone, request)
        and (
            request.ambient_temperature_c is None
            or drone.minimum_operating_temperature_c is None
            or request.ambient_temperature_c >= drone.minimum_operating_temperature_c
        )
        and (
            request.ambient_temperature_c is None
            or drone.maximum_operating_temperature_c is None
            or request.ambient_temperature_c <= drone.maximum_operating_temperature_c
        )
    ]
    if capable:
        return capable
    temperature = request.ambient_temperature_c
    if temperature is not None and all(
        (
            drone.minimum_operating_temperature_c is not None
            and temperature < drone.minimum_operating_temperature_c
        )
        or (
            drone.maximum_operating_temperature_c is not None
            and temperature > drone.maximum_operating_temperature_c
        )
        for drone in drones
    ):
        raise PlanningError(
            code=ErrorCode.TEMPERATURE_LIMIT_EXCEEDED,
            message="Температура находится вне подтверждённого диапазона доступных БВС.",
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.RESCHEDULE_OPERATION,
                    message="Перенесите вылет на период допустимой температуры.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="ambient_temperature_c",
                    current_value=temperature,
                    unit="celsius",
                    affected_ids=[drone.drone_id for drone in drones],
                )
            ],
        )
    raise PlanningError(
        code=ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
        message="Высота задания находится вне подтверждённого диапазона доступных БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                message="Измените высоту задания или добавьте БВС подходящего диапазона.",
                confidence=RecommendationConfidence.EXACT,
                parameter="mission_altitude_m",
                current_value=request.mission_altitude_m,
                unit="m",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _drone_supports_altitude(drone: DroneSpec, request: PlanRequest) -> bool:
    minimum = drone.minimum_altitude_for(request.altitude_reference)
    maximum = drone.maximum_altitude_for(request.altitude_reference)
    height = request.survey_height_agl
    return (
        (minimum is None or request.mission_altitude_m >= minimum)
        and (maximum is None or request.mission_altitude_m <= maximum)
        and (
            drone.minimum_mission_altitude_agl_m is None
            or height.minimum_m >= drone.minimum_mission_altitude_agl_m
        )
        and (
            drone.maximum_mission_altitude_agl_m is None
            or height.maximum_m <= drone.maximum_mission_altitude_agl_m
        )
    )


def _drones_capable_of_wind(
    drones: list[DroneSpec],
    wind: WindCondition | None,
) -> list[DroneSpec]:
    if wind is None:
        return drones
    capable_drones = [drone for drone in drones if wind.speed_mps <= drone.maximum_wind_mps]
    if capable_drones:
        return capable_drones
    raise PlanningError(
        code=ErrorCode.WIND_LIMIT_EXCEEDED,
        message="Скорость ветра превышает допустимое значение для доступных БВС.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.WAIT_FOR_LOWER_WIND,
                message=(
                    "Перенесите вылет до снижения ветра не более чем до "
                    f"{max(drone.maximum_wind_mps for drone in drones):g} m/s."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="wind.speed_mps",
                current_value=wind.speed_mps,
                required_value=max(drone.maximum_wind_mps for drone in drones),
                unit="m/s",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _drones_with_eligible_sites(
    drones: list[DroneSpec],
    request: PlanRequest,
) -> list[DroneSpec]:
    departure_sites = _configured_departure_sites(request)
    landing_sites = _configured_primary_landing_sites(request)
    alternate_sites = request.alternate_landing_sites
    eligible = [
        drone
        for drone in drones
        if _has_eligible_site(departure_sites, drone.drone_id)
        and _has_eligible_site(landing_sites, drone.drone_id)
        and _has_eligible_site(alternate_sites, drone.drone_id)
    ]
    if eligible:
        return eligible
    raise PlanningError(
        code=ErrorCode.NO_ELIGIBLE_FLIGHT_SITE,
        message="Для доступных БВС нет назначенной пары площадок взлёта и посадки.",
        recommendations=[
            FeasibilityRecommendation(
                code=RecommendationCode.ASSIGN_FLIGHT_SITE,
                message=(
                    "Назначьте хотя бы одну доступную площадку взлёта и основную "
                    "площадку посадки одному и тому же БВС."
                ),
                confidence=RecommendationConfidence.EXACT,
                parameter="departure_sites/landing_sites",
                affected_ids=[drone.drone_id for drone in drones],
            )
        ],
    )


def _has_eligible_site(sites: list[FlightSite], drone_id: str) -> bool:
    if not sites:
        return True
    return any(_site_supports_drone(site, drone_id) for site in sites)
