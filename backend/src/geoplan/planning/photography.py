# ruff: noqa: RUF001
"""Repair strip-boundary camera timing without changing flight geometry or clocks."""

from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import ErrorCode, RecommendationCode, RecommendationConfidence
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.camera import DistanceCameraPolicy
from geoplan.planning.models import PlanRequest
from geoplan.routing.photography import with_capture_waypoints


def prepare_photography(request: PlanRequest, missions: list[Mission]) -> list[Mission]:
    if request.survey_parameters is None:
        return missions
    cameras = {
        d.drone_id: d.installed_sensor or request.survey_parameters.sensor for d in request.drones
    }
    prepared = []
    for mission in missions:
        minimum = cameras[mission.drone_id].minimum_trigger_interval_s
        try:
            interval = DistanceCameraPolicy().minimum_interval_s(mission)
        except ValueError as error:
            raise PlanningError(
                code=ErrorCode.UNPLANNABLE_SEGMENT,
                message="План камеры превышает лимит проверяемых кадров.",
            ) from error
        if (
            mission.photo_capture_route_indices is None
            and interval is not None
            and interval < minimum - 1e-6
        ):
            if mission.vertical_profile:
                raise PlanningError(
                    code=ErrorCode.UNPLANNABLE_SEGMENT,
                    message=(
                        f"БВС {mission.drone_id}: камера не успевает между галсами профиля "
                        f"рельефа — интервал {interval:.6f} с, предел камеры {minimum:g} с. "
                        "Коррекция явных точек съёмки над DEM пока не поддерживается."
                    ),
                    recommendations=[
                        FeasibilityRecommendation(
                            code=RecommendationCode.REDUCE_MISSION_SCOPE,
                            message="Измените разбиение вылетов, чтобы исключить короткие стыки "
                            "съёмки; повторите расчёт с сохранением DEM и требований перекрытия.",
                            confidence=RecommendationConfidence.ESTIMATE,
                            parameter="survey_parameters.maximum_segment_distance_km",
                            current_value=request.survey_parameters.maximum_segment_distance_km,
                            unit="km",
                            affected_ids=[mission.drone_id, mission.segment_id],
                        )
                    ],
                )
            try:
                mission = with_capture_waypoints(mission, minimum, maximum_captures=100_000)
            except ValueError as error:
                raise PlanningError(
                    code=ErrorCode.UNPLANNABLE_SEGMENT,
                    message="Построение непрерывных часов камеры для вылета не удалось.",
                ) from error
        prepared.append(mission)
    return prepared
