from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from xml.sax.saxutils import escape

from geoplan.domain.enums import (
    AirframeType,
    AltitudeReference,
    AutopilotFirmware,
    ErrorCode,
    MissionStageType,
)
from geoplan.domain.fleet import AutopilotProfile
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import DronePlan, Mission, MissionStage
from geoplan.errors import PlanningError
from geoplan.flight.trajectory import mission_route_clock
from geoplan.geodata.models import (
    ExportRequest,
    GeoJsonFeature,
    GeoJsonFeatureCollection,
    GeoJsonGeometry,
    GeoJsonProperties,
    KmlExport,
    MissionPlannerExport,
    QGroundControlEmptyCollection,
    QGroundControlExport,
    QGroundControlGeoFence,
    QGroundControlMission,
    QGroundControlPlan,
    QGroundControlSimpleItem,
)
from geoplan.navigation.routing import coordinate_distance_km
from geoplan.safety.audit import ExportVerificationResult

MavCommand = Literal[16, 21, 22, 178, 206, 2000]
MavFrame = Literal[2, 3, 10]

MAV_CMD_NAV_WAYPOINT: Literal[16] = 16
MAV_CMD_NAV_LAND: Literal[21] = 21
MAV_CMD_NAV_TAKEOFF: Literal[22] = 22
MAV_CMD_DO_CHANGE_SPEED: Literal[178] = 178
MAV_CMD_DO_SET_CAM_TRIGG_DIST: Literal[206] = 206
MAV_CMD_IMAGE_START_CAPTURE: Literal[2000] = 2000
MAV_FRAME_MISSION: Literal[2] = 2
MAV_FRAME_GLOBAL_RELATIVE_ALT: Literal[3] = 3
MAV_FRAME_GLOBAL_TERRAIN_ALT: Literal[10] = 10

FIRMWARE_TYPES: dict[AutopilotFirmware, Literal[0, 3, 12]] = {
    AutopilotFirmware.GENERIC_MAVLINK: 0,
    AutopilotFirmware.ARDUPILOT: 3,
    AutopilotFirmware.PX4: 12,
}
VEHICLE_TYPES: dict[AirframeType, Literal[1, 2, 13, 14, 22, 43]] = {
    AirframeType.FIXED_WING: 1,
    AirframeType.QUADROTOR: 2,
    AirframeType.HEXAROTOR: 13,
    AirframeType.OCTOROTOR: 14,
    AirframeType.VTOL_FIXED_ROTOR: 22,
    AirframeType.GENERIC_MULTIROTOR: 43,
}


@dataclass(frozen=True)
class AutopilotCommand:
    command: MavCommand
    frame: MavFrame
    params: list[float | None]


@dataclass(frozen=True)
class CanonicalMissionExportVerifier:
    """Checks every export representation against the canonical mission route."""

    def verify(self, missions: list[Mission]) -> ExportVerificationResult:
        affected = [
            f"{mission.drone_id}:{mission.segment_id}"
            for mission in missions
            if not _mission_exports_match(mission)
        ]
        return ExportVerificationResult(
            consistent=not affected,
            message=(
                "GeoJSON, KML и форматы автопилотов соответствуют рассчитанным маршрутам."
                if not affected
                else "Хотя бы один экспортированный маршрут отличается от рассчитанного."
            ),
            affected_ids=affected,
        )


def _mission_exports_match(mission: Mission) -> bool:
    expected = _absolute_export_coordinates(mission)
    geojson_matches = _create_geojson_feature(mission).geometry.coordinates == expected
    kml_coordinates = " ".join(
        f"{longitude},{latitude},{altitude}" for longitude, latitude, altitude in expected
    )
    kml_matches = f"<coordinates>{kml_coordinates}</coordinates>" in _create_kml_mission_folder(
        mission
    )
    autopilot_coordinates: list[list[float]] = []
    commanded_altitude_m = _commanded_altitude_m(mission)
    for command in _mission_commands(mission):
        if command.command not in {
            MAV_CMD_NAV_TAKEOFF,
            MAV_CMD_NAV_WAYPOINT,
            MAV_CMD_NAV_LAND,
        }:
            continue
        latitude = command.params[4]
        longitude = command.params[5]
        if latitude is None or longitude is None:
            return False
        expected_altitude_m = 0 if command.command == MAV_CMD_NAV_LAND else commanded_altitude_m
        if command.params[6] != expected_altitude_m:
            return False
        autopilot_coordinates.append([longitude, latitude])
        expected_frame = (
            MAV_FRAME_GLOBAL_TERRAIN_ALT
            if mission.altitude_reference is AltitudeReference.AGL
            else MAV_FRAME_GLOBAL_RELATIVE_ALT
        )
        if command.frame != expected_frame:
            return False
    expected_autopilot = [
        [coordinate.longitude, coordinate.latitude] for coordinate in mission.route
    ]
    captures_match = True
    if mission.photo_capture_route_indices is not None:
        position = None
        actual = []
        for command in _mission_commands(mission):
            if command.command in {16, 21, 22}:
                position = (command.params[5], command.params[4])
            if command.command == MAV_CMD_IMAGE_START_CAPTURE:
                actual.append(position)
                captures_match &= command.params[2:4] == [1, len(actual)]
            if command.command == MAV_CMD_DO_SET_CAM_TRIGG_DIST:
                captures_match = False
        captures_match &= actual == [
            (mission.route[i].longitude, mission.route[i].latitude)
            for i in mission.photo_capture_route_indices
        ]
    return (
        captures_match
        and geojson_matches
        and kml_matches
        and _deduplicate(autopilot_coordinates) == _deduplicate(expected_autopilot)
    )


def _absolute_export_coordinates(mission: Mission) -> list[list[float]]:
    if mission.vertical_profile:
        return [
            [
                point.coordinate.longitude,
                point.coordinate.latitude,
                point.target_altitude_msl,
            ]
            for point in mission.vertical_profile
        ]
    return [
        [coordinate.longitude, coordinate.latitude, mission.altitude_m]
        for coordinate in mission.route
    ]


def _deduplicate(coordinates: list[list[float]]) -> list[list[float]]:
    result: list[list[float]] = []
    for coordinate in coordinates:
        if not result or coordinate != result[-1]:
            result.append(coordinate)
    return result


def build_geojson_export(request: ExportRequest) -> GeoJsonFeatureCollection:
    missions = _missions_for_drone(request)
    return GeoJsonFeatureCollection(
        type="FeatureCollection",
        features=[_create_geojson_feature(mission) for mission in missions],
    )


def build_kml_export(request: ExportRequest) -> KmlExport:
    missions = _missions_for_drone(request)
    placemarks = "".join(_create_kml_mission_folder(mission) for mission in missions)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(request.drone_id)}</name>{placemarks}</Document></kml>"
    )
    return KmlExport(file_name=f"mission-{request.drone_id}.kml", content=content)


def build_qgroundcontrol_export(request: ExportRequest) -> QGroundControlExport:
    drone_plan = _drone_plan_for_request(request)
    plan = _qgroundcontrol_plan(drone_plan.missions, drone_plan.autopilot_profile)
    return QGroundControlExport(
        file_name=f"mission-{request.drone_id}.plan",
        content=plan.model_dump_json(by_alias=True),
    )


def build_mission_planner_export(request: ExportRequest) -> MissionPlannerExport:
    missions = _missions_for_drone(request)
    content = "\n".join(["QGC WPL 110", *_mission_planner_lines(missions)])
    return MissionPlannerExport(
        file_name=f"mission-{request.drone_id}.waypoints",
        content=f"{content}\n",
    )


def _missions_for_drone(request: ExportRequest) -> list[Mission]:
    return _drone_plan_for_request(request).missions


def _drone_plan_for_request(request: ExportRequest) -> DronePlan:
    for drone_plan in request.plan.drone_plans:
        if drone_plan.drone_id == request.drone_id:
            return drone_plan
    raise PlanningError(
        code=ErrorCode.DRONE_NOT_FOUND,
        message=f"БВС {request.drone_id} отсутствует в плане.",
    )


def _qgroundcontrol_plan(
    missions: list[Mission],
    autopilot_profile: AutopilotProfile,
) -> QGroundControlPlan:
    route = [coordinate for mission in missions for coordinate in mission.route]
    if not route:
        raise PlanningError(
            code=ErrorCode.NO_EXPORTABLE_MISSIONS,
            message="Для выбранного БВС нет маршрута для экспорта.",
        )
    initial_mission = missions[0]
    return QGroundControlPlan(
        file_type="Plan",
        geo_fence=QGroundControlGeoFence(circles=[], polygons=[], version=2),
        ground_station="QGroundControl",
        mission=QGroundControlMission(
            cruise_speed=initial_mission.cruise_speed_kmh / 3.6,
            firmware_type=FIRMWARE_TYPES[autopilot_profile.firmware],
            global_plan_altitude_mode=(
                4 if initial_mission.altitude_reference is AltitudeReference.AGL else 1
            ),
            hover_speed=autopilot_profile.hover_speed_mps,
            items=[
                QGroundControlSimpleItem(
                    type="SimpleItem",
                    altitude=_command_altitude(command),
                    altitude_mode=(4 if command.frame == MAV_FRAME_GLOBAL_TERRAIN_ALT else 0),
                    command=command.command,
                    do_jump_id=index + 1,
                    frame=command.frame,
                    params=command.params,
                )
                for index, command in enumerate(_autopilot_commands(missions))
            ],
            planned_home_position=[
                route[0].latitude,
                route[0].longitude,
                _planned_home_altitude_m(initial_mission),
            ],
            vehicle_type=VEHICLE_TYPES[autopilot_profile.airframe],
            version=2,
        ),
        rally_points=QGroundControlEmptyCollection(points=[], version=2),
        version=1,
    )


def _planned_home_altitude_m(mission: Mission) -> float:
    if mission.vertical_profile:
        return mission.vertical_profile[0].terrain_elevation_m
    return mission.route[0].altitude_m or 0


def _mission_planner_lines(missions: list[Mission]) -> list[str]:
    lines: list[str] = []
    for index, command in enumerate(_autopilot_commands(missions)):
        current = 1 if index == 0 else 0
        serialized_params = [0 if value is None else value for value in command.params]
        lines.append(
            "\t".join(
                [
                    str(index),
                    str(current),
                    str(command.frame),
                    str(command.command),
                    *(f"{value:.7f}" for value in serialized_params),
                    "1",
                ]
            )
        )
    return lines


def _autopilot_commands(missions: list[Mission]) -> list[AutopilotCommand]:
    commands: list[AutopilotCommand] = []
    capture_sequence = 0
    for mission in missions:
        for command in _mission_commands(mission):
            if command.command == MAV_CMD_IMAGE_START_CAPTURE:
                capture_sequence += 1
                command = replace(
                    command,
                    params=[*command.params[:3], capture_sequence, *command.params[4:]],
                )
            commands.append(command)
    return commands


def _mission_commands(mission: Mission) -> list[AutopilotCommand]:
    position_frame = _position_frame(mission)
    commanded_altitude_m = _commanded_altitude_m(mission)
    commands = [
        _position_command(
            MAV_CMD_NAV_TAKEOFF,
            mission.route[0],
            commanded_altitude_m,
            position_frame,
        ),
        AutopilotCommand(
            command=MAV_CMD_DO_CHANGE_SPEED,
            frame=MAV_FRAME_MISSION,
            params=[0, mission.cruise_speed_kmh / 3.6, -1, 0, 0, 0, 0],
        ),
    ]
    clock = mission_route_clock(mission)
    route_index = 0
    capture_indices = set(mission.photo_capture_route_indices or [])
    captured = set()

    def capture_at(index: int) -> None:
        if index in capture_indices and index not in captured:
            captured.add(index)
            commands.append(
                AutopilotCommand(
                    command=MAV_CMD_IMAGE_START_CAPTURE,
                    frame=MAV_FRAME_MISSION,
                    params=[0, 0, 1, len(captured), 0, 0, 0],
                )
            )

    last_speed = None
    last_coordinate = mission.route[0]
    route_stages = [
        stage for stage in mission.stages if stage.stage_type is not MissionStageType.TAKEOFF
    ]
    if not route_stages:
        last_coordinate = _append_waypoint_commands(
            commands,
            mission.route,
            commanded_altitude_m,
            last_coordinate,
            position_frame,
        )
    else:
        trigger_spacing_m = (
            None
            if mission.photo_capture_route_indices is not None
            else _camera_trigger_spacing_m(mission)
        )
        for stage in route_stages:
            if stage.stage_type is MissionStageType.SURVEY:
                capture_at(route_index)
            if stage.stage_type is MissionStageType.SURVEY and trigger_spacing_m is not None:
                commands.append(_camera_trigger_command(trigger_spacing_m))
            coordinates = stage.coordinates
            if stage.stage_type is MissionStageType.LANDING:
                coordinates = coordinates[:-1]
            if clock.phase_timed:
                for coordinate in coordinates:
                    if coordinate == last_coordinate:
                        continue
                    duration = (
                        clock.offsets_minutes[route_index + 1] - clock.offsets_minutes[route_index]
                    )
                    speed = (
                        coordinate_distance_km(last_coordinate, coordinate) * 1000 / (duration * 60)
                        if duration > 0
                        else 0.0
                    )
                    if speed > 0 and (last_speed is None or abs(speed - last_speed) > 1e-7):
                        commands.append(
                            AutopilotCommand(
                                command=MAV_CMD_DO_CHANGE_SPEED,
                                frame=MAV_FRAME_MISSION,
                                params=[1, speed, -1, 0, 0, 0, 0],
                            )
                        )
                        last_speed = speed
                    commands.append(
                        _position_command(
                            MAV_CMD_NAV_WAYPOINT, coordinate, commanded_altitude_m, position_frame
                        )
                    )
                    last_coordinate = coordinate
                    route_index += 1
                    capture_at(route_index)
            else:
                last_coordinate = _append_waypoint_commands(
                    commands, coordinates, commanded_altitude_m, last_coordinate, position_frame
                )
            if stage.stage_type is MissionStageType.SURVEY and trigger_spacing_m is not None:
                commands.append(_camera_trigger_command(0))
    if clock.phase_timed and last_coordinate != mission.route[-1]:
        duration = clock.offsets_minutes[route_index + 1] - clock.offsets_minutes[route_index]
        speed = (
            coordinate_distance_km(last_coordinate, mission.route[-1]) * 1000 / (duration * 60)
            if duration > 0
            else 0.0
        )
        if speed > 0 and (last_speed is None or abs(speed - last_speed) > 1e-7):
            commands.append(
                AutopilotCommand(
                    command=MAV_CMD_DO_CHANGE_SPEED,
                    frame=MAV_FRAME_MISSION,
                    params=[1, speed, -1, 0, 0, 0, 0],
                )
            )
    commands.append(_position_command(MAV_CMD_NAV_LAND, mission.route[-1], 0, position_frame))
    return commands


def _commanded_altitude_m(mission: Mission) -> float:
    if mission.altitude_reference is AltitudeReference.AGL:
        return mission.altitude_m + mission.terrain_vertical_uncertainty_m
    return mission.altitude_m


def _append_waypoint_commands(
    commands: list[AutopilotCommand],
    coordinates: list[Coordinate],
    altitude_m: float,
    last_coordinate: Coordinate,
    frame: Literal[3, 10],
) -> Coordinate:
    current = last_coordinate
    for coordinate in coordinates:
        if coordinate == current:
            continue
        commands.append(_position_command(MAV_CMD_NAV_WAYPOINT, coordinate, altitude_m, frame))
        current = coordinate
    return current


def _position_command(
    command: Literal[16, 21, 22],
    coordinate: Coordinate,
    altitude_m: float,
    frame: Literal[3, 10],
) -> AutopilotCommand:
    return AutopilotCommand(
        command=command,
        frame=frame,
        params=[0, 0, 0, None, coordinate.latitude, coordinate.longitude, altitude_m],
    )


def _position_frame(mission: Mission) -> Literal[3, 10]:
    if mission.altitude_reference is AltitudeReference.AGL:
        return MAV_FRAME_GLOBAL_TERRAIN_ALT
    return MAV_FRAME_GLOBAL_RELATIVE_ALT


def _camera_trigger_command(distance_m: float) -> AutopilotCommand:
    return AutopilotCommand(
        command=MAV_CMD_DO_SET_CAM_TRIGG_DIST,
        frame=MAV_FRAME_MISSION,
        params=[distance_m, 0, 1 if distance_m > 0 else 0, 0, 0, 0, 0],
    )


def _camera_trigger_spacing_m(mission: Mission) -> float | None:
    from geoplan.flight.camera import DistanceCameraPolicy

    return DistanceCameraPolicy().spacing_m(mission)


def _command_altitude(command: AutopilotCommand) -> float:
    if command.command in {MAV_CMD_NAV_WAYPOINT, MAV_CMD_NAV_LAND, MAV_CMD_NAV_TAKEOFF}:
        altitude = command.params[6]
        return altitude if altitude is not None else 0
    return 0


def _create_geojson_feature(mission: Mission) -> GeoJsonFeature:
    return GeoJsonFeature(
        type="Feature",
        properties=GeoJsonProperties(
            coverage_area_ids=mission.coverage_area_ids,
            drone_id=mission.drone_id,
            segment_id=mission.segment_id,
            duration_minutes=mission.duration_minutes,
            distance_km=mission.distance_km,
            altitude_m=mission.altitude_m,
            altitude_reference=mission.altitude_reference,
            minimum_agl_m=mission.minimum_agl_m,
            minimum_terrain_elevation_m=mission.minimum_terrain_elevation_m,
            maximum_terrain_elevation_m=mission.maximum_terrain_elevation_m,
            vertical_distance_m=mission.vertical_distance_m,
            vertical_energy_equivalent_minutes=mission.vertical_energy_equivalent_minutes,
            vertical_climb_duration_minutes=mission.vertical_climb_duration_minutes,
            vertical_descent_duration_minutes=mission.vertical_descent_duration_minutes,
            terrain_vertical_uncertainty_m=mission.terrain_vertical_uncertainty_m,
            vertical_deconfliction_offset_m=mission.vertical_deconfliction_offset_m,
            cruise_speed_kmh=mission.cruise_speed_kmh,
            effective_speed_kmh=mission.effective_speed_kmh,
            estimated_battery_percent=mission.estimated_battery_percent,
            starting_battery_percent=mission.starting_battery_percent,
            remaining_battery_percent=mission.remaining_battery_percent,
            battery_reserve_margin_percent=mission.battery_reserve_margin_percent,
            minimum_alternate_margin_percent=mission.minimum_alternate_margin_percent,
            critical_alternate_site_id=mission.critical_alternate_site_id,
            alternate_check_spacing_m=mission.alternate_check_spacing_m,
            alternate_evaluated_checkpoint_count=(mission.alternate_evaluated_checkpoint_count),
            alternate_certification_error_minutes=(mission.alternate_certification_error_minutes),
            survey_duration_minutes=mission.survey_duration_minutes,
            transit_duration_minutes=mission.transit_duration_minutes,
            start_offset_minutes=mission.start_offset_minutes,
            end_offset_minutes=mission.end_offset_minutes,
            start_time=mission.start_time,
            end_time=mission.end_time,
            image_count=mission.image_count,
            photo_capture_route_indices=mission.photo_capture_route_indices,
            computed_gsd_cm=mission.computed_gsd_cm,
            strip_spacing_m=mission.strip_spacing_m,
            stages=mission.stages,
        ),
        geometry=GeoJsonGeometry(
            type="LineString",
            coordinates=_absolute_export_coordinates(mission),
        ),
    )


def _create_kml_mission_folder(mission: Mission) -> str:
    stage_placemarks = "".join(
        _create_kml_stage_placemark(stage, mission.altitude_m) for stage in mission.stages
    )
    return (
        "<Folder>"
        f"<name>{escape(mission.segment_id)}</name>"
        f"{_create_kml_route_placemark(mission)}{stage_placemarks}"
        "</Folder>"
    )


def _create_kml_route_placemark(mission: Mission) -> str:
    coordinates = " ".join(
        f"{longitude},{latitude},{altitude}"
        for longitude, latitude, altitude in _absolute_export_coordinates(mission)
    )
    return (
        "<Placemark>"
        "<name>Маршрут полёта</name>"
        "<ExtendedData>"
        f'<Data name="distance_km"><value>{mission.distance_km}</value></Data>'
        f'<Data name="duration_minutes"><value>{mission.duration_minutes}</value></Data>'
        f'<Data name="effective_speed_kmh"><value>{mission.effective_speed_kmh}</value></Data>'
        '<Data name="estimated_battery_percent">'
        f"<value>{mission.estimated_battery_percent}</value></Data>"
        '<Data name="starting_battery_percent">'
        f"<value>{mission.starting_battery_percent}</value></Data>"
        '<Data name="remaining_battery_percent">'
        f"<value>{mission.remaining_battery_percent or 0}</value></Data>"
        '<Data name="battery_reserve_margin_percent">'
        f"<value>{mission.battery_reserve_margin_percent or 0}</value></Data>"
        '<Data name="minimum_alternate_margin_percent">'
        f"<value>{mission.minimum_alternate_margin_percent or 0}</value></Data>"
        '<Data name="critical_alternate_site_id">'
        f"<value>{escape(mission.critical_alternate_site_id or '')}</value></Data>"
        '<Data name="alternate_check_spacing_m">'
        f"<value>{mission.alternate_check_spacing_m or 0}</value></Data>"
        '<Data name="alternate_evaluated_checkpoint_count">'
        f"<value>{mission.alternate_evaluated_checkpoint_count or 0}</value></Data>"
        '<Data name="alternate_certification_error_minutes">'
        f"<value>{mission.alternate_certification_error_minutes or 0}</value></Data>"
        '<Data name="survey_duration_minutes">'
        f"<value>{mission.survey_duration_minutes or 0}</value></Data>"
        '<Data name="transit_duration_minutes">'
        f"<value>{mission.transit_duration_minutes or 0}</value></Data>"
        '<Data name="start_offset_minutes">'
        f"<value>{mission.start_offset_minutes or 0}</value></Data>"
        f'<Data name="end_offset_minutes"><value>{mission.end_offset_minutes or 0}</value></Data>'
        f'<Data name="start_time"><value>{_optional_isoformat(mission.start_time)}</value></Data>'
        f'<Data name="end_time"><value>{_optional_isoformat(mission.end_time)}</value></Data>'
        f'<Data name="image_count"><value>{mission.image_count or 0}</value></Data>'
        '<Data name="photo_capture_route_indices"><value>'
        f"{mission.photo_capture_route_indices or []}</value></Data>"
        f'<Data name="computed_gsd_cm"><value>{mission.computed_gsd_cm or 0}</value></Data>'
        f'<Data name="strip_spacing_m"><value>{mission.strip_spacing_m or 0}</value></Data>'
        f'<Data name="altitude_reference"><value>{mission.altitude_reference.value}</value></Data>'
        f'<Data name="minimum_agl_m"><value>{mission.minimum_agl_m or 0}</value></Data>'
        '<Data name="vertical_energy_equivalent_minutes">'
        f"<value>{mission.vertical_energy_equivalent_minutes}</value></Data>"
        '<Data name="vertical_climb_duration_minutes">'
        f"<value>{mission.vertical_climb_duration_minutes}</value></Data>"
        '<Data name="vertical_descent_duration_minutes">'
        f"<value>{mission.vertical_descent_duration_minutes}</value></Data>"
        '<Data name="terrain_vertical_uncertainty_m">'
        f"<value>{mission.terrain_vertical_uncertainty_m}</value></Data>"
        '<Data name="vertical_deconfliction_offset_m">'
        f"<value>{mission.vertical_deconfliction_offset_m}</value></Data>"
        "</ExtendedData>"
        "<LineString><altitudeMode>absolute</altitudeMode>"
        f"<coordinates>{coordinates}</coordinates>"
        "</LineString></Placemark>"
    )


def _optional_isoformat(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _create_kml_stage_placemark(stage: MissionStage, altitude_m: float) -> str:
    coordinate = stage.coordinates[-1]
    stage_altitude_m = (
        0
        if stage.stage_type in {MissionStageType.TAKEOFF, MissionStageType.LANDING}
        else altitude_m
    )
    return (
        "<Placemark>"
        f"<name>{escape(stage.stage_type.value)}</name>"
        "<Point>"
        f"<coordinates>{coordinate.longitude},{coordinate.latitude},{stage_altitude_m}</coordinates>"
        "</Point></Placemark>"
    )
