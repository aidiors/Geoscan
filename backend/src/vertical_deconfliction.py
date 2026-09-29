from __future__ import annotations

from dataclasses import dataclass

from src.schemas import Coordinate, Mission, MissionStage, MissionStageType, VerticalLayerOption


@dataclass(frozen=True)
class AltitudeInterval:
    minimum_msl_m: float
    maximum_msl_m: float


def altitude_interval_msl(mission: Mission) -> AltitudeInterval:
    altitudes = (
        [point.target_altitude_msl for point in mission.vertical_profile]
        if mission.vertical_profile
        else [mission.altitude_m]
    )
    return AltitudeInterval(min(altitudes), max(altitudes))


def vertical_interval_gap_m(first: Mission, second: Mission) -> float:
    first_interval = altitude_interval_msl(first)
    second_interval = altitude_interval_msl(second)
    return max(
        0.0,
        second_interval.minimum_msl_m - first_interval.maximum_msl_m,
        first_interval.minimum_msl_m - second_interval.maximum_msl_m,
    )


def horizontal_duration_minutes(mission: Mission) -> float:
    return max(
        0.0,
        mission.duration_minutes
        - mission.vertical_climb_duration_minutes
        - mission.vertical_descent_duration_minutes,
    )


def apply_vertical_layer(mission: Mission, option: VerticalLayerOption) -> Mission:
    if option.offset_m <= 0:
        return mission
    updated_profile = [
        point.model_copy(
            update={
                "coordinate": _offset_coordinate(point.coordinate, option.offset_m),
                "target_altitude_msl": point.target_altitude_msl + option.offset_m,
                "target_agl_m": point.target_agl_m + option.offset_m,
            }
        )
        for point in mission.vertical_profile
    ]
    extra_duration = option.climb_duration_minutes + option.descent_duration_minutes
    updated_duration = mission.duration_minutes + extra_duration
    return mission.model_copy(
        update={
            "duration_minutes": updated_duration,
            "altitude_m": mission.altitude_m + option.offset_m,
            "vertical_profile": updated_profile,
            "minimum_agl_m": (
                mission.minimum_agl_m + option.offset_m
                if mission.minimum_agl_m is not None
                else None
            ),
            "vertical_distance_m": mission.vertical_distance_m + 2 * option.offset_m,
            "vertical_energy_equivalent_minutes": (
                mission.vertical_energy_equivalent_minutes + option.extra_energy_equivalent_minutes
            ),
            "vertical_climb_duration_minutes": (
                mission.vertical_climb_duration_minutes + option.climb_duration_minutes
            ),
            "vertical_descent_duration_minutes": (
                mission.vertical_descent_duration_minutes + option.descent_duration_minutes
            ),
            "effective_speed_kmh": mission.distance_km / updated_duration * 60,
            "estimated_battery_percent": (
                mission.estimated_battery_percent + option.extra_battery_percent
            ),
            "remaining_battery_percent": _subtract_optional(
                mission.remaining_battery_percent,
                option.extra_battery_percent,
            ),
            "battery_reserve_margin_percent": _subtract_optional(
                mission.battery_reserve_margin_percent,
                option.extra_battery_percent,
            ),
            "minimum_alternate_margin_percent": _subtract_optional(
                mission.minimum_alternate_margin_percent,
                option.extra_battery_percent,
            ),
            "critical_alternate_start_offset_minutes": _add_optional(
                mission.critical_alternate_start_offset_minutes,
                option.climb_duration_minutes,
            ),
            "stages": _apply_stage_durations(mission, option),
            "vertical_deconfliction_offset_m": (
                mission.vertical_deconfliction_offset_m + option.offset_m
            ),
        }
    )


def _offset_coordinate(coordinate: Coordinate, offset_m: float) -> Coordinate:
    if coordinate.altitude_m is None:
        return coordinate
    return coordinate.model_copy(update={"altitude_m": coordinate.altitude_m + offset_m})


def _subtract_optional(value: float | None, decrement: float) -> float | None:
    return value - decrement if value is not None else None


def _add_optional(value: float | None, increment: float) -> float | None:
    return value + increment if value is not None else None


def _apply_stage_durations(
    mission: Mission,
    option: VerticalLayerOption,
) -> list[MissionStage]:
    increments = {
        MissionStageType.TAKEOFF: option.climb_duration_minutes,
        MissionStageType.LANDING: option.descent_duration_minutes,
    }
    return [
        stage.model_copy(
            update={
                "duration_minutes": stage.duration_minutes + increments.get(stage.stage_type, 0)
            }
        )
        for stage in mission.stages
    ]
