from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.domain.enums import AirframeType, AltitudeReference, AutopilotFirmware, MissionTask
from geoplan.domain.flight_phases import VerticalTakeoffLandingProfile
from geoplan.domain.geo import Coordinate
from geoplan.domain.survey import SensorProfile


class AutopilotProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    firmware: AutopilotFirmware = AutopilotFirmware.GENERIC_MAVLINK
    airframe: AirframeType = AirframeType.GENERIC_MULTIROTOR
    hover_speed_mps: float = Field(default=5, gt=0, le=100)


class PayloadCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    supported_tasks: list[MissionTask] = Field(min_length=1, max_length=16)


class DroneResourceLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    maximum_sorties: int | None = Field(default=None, ge=0)
    maximum_flight_minutes: float | None = Field(default=None, ge=0)

    @property
    def is_bounded(self) -> bool:
        return self.maximum_sorties is not None or self.maximum_flight_minutes is not None

    def allows(self, sorties: int, flight_minutes: float) -> bool:
        return (self.maximum_sorties is None or sorties <= self.maximum_sorties) and (
            self.maximum_flight_minutes is None
            or flight_minutes <= self.maximum_flight_minutes + 1e-9
        )


class DroneSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    takeoff_landing_profile: VerticalTakeoffLandingProfile | None = None
    installed_sensor: SensorProfile | None = None
    available_battery_sets: int | None = Field(default=None, ge=0, le=10_000)
    minimum_turn_radius_m: float = Field(default=0, ge=0, le=10_000)

    drone_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    cruise_speed_kmh: float = Field(gt=0, le=300)
    maximum_flight_minutes: float = Field(gt=0, le=1_440)
    initial_charge_percent: float = Field(default=100, gt=0, le=100)
    battery_health_percent: float = Field(default=100, gt=0, le=100)
    energy_consumption_multiplier: float = Field(default=1, ge=0.1, le=10)
    battery_reserve_percent: float = Field(ge=0, lt=100)
    maximum_wind_mps: float = Field(default=10, gt=0, le=100)
    supported_tasks: list[MissionTask] = Field(min_length=1, max_length=16)
    autopilot_profile: AutopilotProfile = Field(default_factory=AutopilotProfile)
    maintenance_interval_flights: int | None = Field(default=None, gt=0, le=100_000)
    maintenance_interval_hours: float | None = Field(default=None, gt=0, le=100_000)
    completed_flights_since_maintenance: int = Field(default=0, ge=0, le=100_000)
    flight_hours_since_maintenance: float = Field(default=0, ge=0, le=100_000)
    maximum_climb_rate_mps: float | None = Field(default=None, gt=0, le=100)
    maximum_descent_rate_mps: float | None = Field(default=None, gt=0, le=100)
    minimum_mission_altitude_m: float | None = Field(default=None, ge=0, le=10_000)
    maximum_mission_altitude_m: float | None = Field(default=None, gt=0, le=100_000)
    minimum_mission_altitude_agl_m: float | None = Field(default=None, ge=0, le=10_000)
    maximum_mission_altitude_agl_m: float | None = Field(default=None, gt=0, le=10_000)
    minimum_mission_altitude_msl_m: float | None = Field(default=None, ge=-500, le=100_000)
    maximum_mission_altitude_msl_m: float | None = Field(default=None, gt=-500, le=100_000)
    minimum_cruise_speed_kmh: float | None = Field(default=None, gt=0, le=300)
    maximum_cruise_speed_kmh: float | None = Field(default=None, gt=0, le=300)
    maximum_route_distance_km: float | None = Field(default=None, gt=0, le=10_000)
    minimum_operating_temperature_c: float | None = Field(default=None, ge=-100, le=100)
    maximum_operating_temperature_c: float | None = Field(default=None, ge=-100, le=100)
    maximum_control_range_km: float | None = Field(default=None, gt=0, le=10_000)
    minimum_turnaround_minutes: float = Field(default=0, ge=0, le=1_440)
    payloads: list[PayloadCapability] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def flight_envelope_is_consistent(self) -> DroneSpec:
        profile = self.takeoff_landing_profile
        if profile is not None:
            if self.autopilot_profile.airframe in {
                AirframeType.FIXED_WING,
                AirframeType.VTOL_FIXED_ROTOR,
            }:
                raise ValueError(
                    "Вертикальная модель взлёта и посадки поддерживает только мультикоптеры."
                )
            if (
                self.maximum_climb_rate_mps is not None
                and profile.climb_rate_mps > self.maximum_climb_rate_mps
            ) or (
                self.maximum_descent_rate_mps is not None
                and profile.descent_rate_mps > self.maximum_descent_rate_mps
            ):
                raise ValueError("Плановая вертикальная скорость превышает предел БВС.")
        if self.initial_charge_percent <= self.battery_reserve_percent:
            raise ValueError("Начальный заряд БВС должен превышать обязательный резерв.")
        if (
            self.minimum_mission_altitude_m is not None
            and self.maximum_mission_altitude_m is not None
            and self.maximum_mission_altitude_m < self.minimum_mission_altitude_m
        ):
            raise ValueError("Максимальная высота БВС должна быть не ниже минимальной.")
        for reference, minimum, maximum in (
            (
                "AGL",
                self.minimum_mission_altitude_agl_m,
                self.maximum_mission_altitude_agl_m,
            ),
            (
                "MSL",
                self.minimum_mission_altitude_msl_m,
                self.maximum_mission_altitude_msl_m,
            ),
        ):
            if minimum is not None and maximum is not None and maximum < minimum:
                raise ValueError(
                    f"Максимальная высота БВС {reference} должна быть не ниже минимальной."
                )
        if (
            self.minimum_cruise_speed_kmh is not None
            and self.maximum_cruise_speed_kmh is not None
            and self.maximum_cruise_speed_kmh < self.minimum_cruise_speed_kmh
        ):
            raise ValueError("Максимальная скорость БВС должна быть не ниже минимальной.")
        if (
            self.minimum_cruise_speed_kmh is not None
            and self.cruise_speed_kmh < self.minimum_cruise_speed_kmh
        ):
            raise ValueError("Крейсерская скорость ниже подтверждённого диапазона БВС.")
        if (
            self.maximum_cruise_speed_kmh is not None
            and self.cruise_speed_kmh > self.maximum_cruise_speed_kmh
        ):
            raise ValueError("Крейсерская скорость выше подтверждённого диапазона БВС.")
        if (
            self.minimum_operating_temperature_c is not None
            and self.maximum_operating_temperature_c is not None
            and self.maximum_operating_temperature_c < self.minimum_operating_temperature_c
        ):
            raise ValueError("Максимальная температура БВС должна быть не ниже минимальной.")
        payload_ids = [payload.payload_id for payload in self.payloads]
        if len(payload_ids) != len(set(payload_ids)):
            raise ValueError("Идентификаторы полезных нагрузок БВС должны быть уникальны.")
        return self

    def minimum_altitude_for(self, reference: AltitudeReference) -> float | None:
        specific = (
            self.minimum_mission_altitude_agl_m
            if reference is AltitudeReference.AGL
            else self.minimum_mission_altitude_msl_m
        )
        return specific if specific is not None else self.minimum_mission_altitude_m

    def resource_limit(self) -> DroneResourceLimit:
        sortie_limits = []
        if self.available_battery_sets is not None:
            sortie_limits.append(self.available_battery_sets)
        if self.maintenance_interval_flights is not None:
            sortie_limits.append(
                max(0, self.maintenance_interval_flights - self.completed_flights_since_maintenance)
            )
        remaining_minutes = (
            max(0, self.maintenance_interval_hours - self.flight_hours_since_maintenance) * 60
            if self.maintenance_interval_hours is not None
            else None
        )
        return DroneResourceLimit(
            maximum_sorties=min(sortie_limits) if sortie_limits else None,
            maximum_flight_minutes=remaining_minutes,
        )

    def maximum_altitude_for(self, reference: AltitudeReference) -> float | None:
        specific = (
            self.maximum_mission_altitude_agl_m
            if reference is AltitudeReference.AGL
            else self.maximum_mission_altitude_msl_m
        )
        return specific if specific is not None else self.maximum_mission_altitude_m


class FlightSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    coordinate: Coordinate
    assigned_drone_ids: list[str] = Field(default_factory=list, max_length=100)
