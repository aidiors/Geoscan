"""Repair strip-boundary camera timing without changing flight geometry or clocks."""

from geoplan.domain.enums import ErrorCode
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
                        "Камера не успевает между галсами профиля рельефа; "
                        "измените разбиение вылетов."
                    ),
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
