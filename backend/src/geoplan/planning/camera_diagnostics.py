# ruff: noqa: RUF001
from dataclasses import dataclass
from itertools import pairwise

from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    ErrorCode,
    MissionStageType,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.errors import PlanningError
from geoplan.flight.performance import WindPerformanceError, ground_speed_kmh
from geoplan.planning.models import PlanRequest


@dataclass(frozen=True)
class CameraMissionDiagnostics:
    def airspeed_error(
        self,
        request: PlanRequest,
        drone: DroneSpec,
        mission: Mission,
        envelope: tuple[float, float] | None,
    ) -> PlanningError:
        if envelope is None:
            return PlanningError(
                ErrorCode.FLIGHT_ENVELOPE_EXCEEDED,
                f"Не удалось восстановить воздушную скорость БВС {drone.drone_id}.",
            )
        minimum = (
            f"{drone.minimum_cruise_speed_kmh:g}"
            if drone.minimum_cruise_speed_kmh is not None
            else "не задан"
        )
        maximum = (
            f"{drone.maximum_cruise_speed_kmh:g}"
            if drone.maximum_cruise_speed_kmh is not None
            else "не задан"
        )
        message = (
            f"БВС {drone.drone_id}: расчётная воздушная скорость "
            f"{envelope[0]:.2f}–{envelope[1]:.2f} км/ч вне диапазона "
            f"{minimum}–{maximum} км/ч."
        )
        recommendations = []
        survey = request.survey_parameters
        if (
            survey is not None
            and drone.minimum_cruise_speed_kmh is not None
            and envelope[0] < drone.minimum_cruise_speed_kmh - 0.05
        ):
            camera = drone.installed_sensor or survey.sensor
            spacing = (
                mission.height_agl.minimum_m
                * camera.sensor_height_mm
                / camera.focal_length_mm
                * (1 - survey.forward_overlap_percent / 100)
            )
            try:
                speeds = [
                    ground_speed_kmh(a, b, drone.minimum_cruise_speed_kmh, request.wind)
                    for stage in mission.stages
                    if stage.stage_type is MissionStageType.SURVEY
                    for a, b in pairwise(stage.coordinates)
                    if a != b
                ]
                interval = spacing / max(speeds) * 3.6 if speeds else None
            except WindPerformanceError:
                interval = None
            message += f" Камера требует интервал не менее {camera.minimum_trigger_interval_s:g} с."
            if interval is not None:
                message += (
                    f" Для минимальной скорости оценочный предел интервала — {interval:.3f} с."
                )
                recommendations.append(
                    FeasibilityRecommendation(
                        code=RecommendationCode.ADD_CAPABLE_DRONE,
                        message="Используйте более быструю камеру или БВС с меньшей минимальной "
                        "скоростью; после изменения высоты повторно проверьте GSD и перекрытия.",
                        confidence=RecommendationConfidence.ESTIMATE,
                        parameter="installed_sensor.minimum_trigger_interval_s",
                        current_value=camera.minimum_trigger_interval_s,
                        required_value=round(interval, 6),
                        unit="seconds",
                        affected_ids=[drone.drone_id, camera.sensor_id],
                    )
                )
        return PlanningError(ErrorCode.FLIGHT_ENVELOPE_EXCEEDED, message, recommendations)
