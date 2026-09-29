from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Annotated, Literal

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, Json, RootModel, model_validator


class OptimizationCriterion(StrEnum):
    BALANCED = "balanced"
    COMPLETION_TIME = "completion_time"
    TOTAL_FLIGHT_TIME = "total_flight_time"


class FeasibilityStatus(StrEnum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


class RecommendationConfidence(StrEnum):
    EXACT = "exact"
    ESTIMATE = "estimate"


class RecommendationCode(StrEnum):
    ADD_ALTERNATE_SITE = "add_alternate_site"
    ADD_CAPABLE_DRONE = "add_capable_drone"
    ADJUST_MISSION_ALTITUDE = "adjust_mission_altitude"
    ASSIGN_FLIGHT_SITE = "assign_flight_site"
    EXTEND_DEADLINE = "extend_deadline"
    EXTEND_ENDURANCE = "extend_endurance"
    EXTEND_OPERATING_WINDOW = "extend_operating_window"
    INCREASE_ACTIVE_DRONE_LIMIT = "increase_active_drone_limit"
    REDUCE_MISSION_SCOPE = "reduce_mission_scope"
    RECHARGE_BATTERY = "recharge_battery"
    RESCHEDULE_OPERATION = "reschedule_operation"
    REVISE_GEOSPATIAL_CONSTRAINTS = "revise_geospatial_constraints"
    WAIT_FOR_LOWER_WIND = "wait_for_lower_wind"


class SafetyAuditStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class SafetyCheckCode(StrEnum):
    AIRSPACE_COMPLIANCE = "airspace_compliance"
    ALTERNATE_RESERVE = "alternate_reserve"
    BATTERY_RESERVE = "battery_reserve"
    COVERAGE_COMPLETENESS = "coverage_completeness"
    DRONE_SEPARATION = "drone_separation"
    EXPORT_CONSISTENCY = "export_consistency"
    FLIGHT_ENVELOPE = "flight_envelope"
    NO_FLY_CLEARANCE = "no_fly_clearance"
    OBSTACLE_CLEARANCE = "obstacle_clearance"
    SCHEDULE_INTEGRITY = "schedule_integrity"
    VERTICAL_PROFILE = "vertical_profile"
    VERTICAL_SEPARATION = "vertical_separation"


class AssignmentAlgorithm(StrEnum):
    EXACT_BRANCH_AND_BOUND = "exact_branch_and_bound"
    FAST_GREEDY = "fast_greedy"
    MULTISTART_LOCAL_SEARCH = "multistart_local_search"
    MINIMUM_COST = "minimum_cost"
    DEADLINE_AWARE_LOCAL_SEARCH = "deadline_aware_local_search"


class ScheduleAlgorithm(StrEnum):
    PRIORITY_DECONFLICTION = "priority_deconfliction"


class ScheduleConflictType(StrEnum):
    ROUTE = "route"
    FLIGHT_SITE = "flight_site"
    EMERGENCY_CORRIDOR = "emergency_corridor"


class CoverageAlgorithm(StrEnum):
    BOUSTROPHEDON_SWEEP = "boustrophedon_sweep"
    ADAPTIVE_DECOMPOSED_SWEEP = "adaptive_decomposed_sweep"


class CoverageAngleCost(StrEnum):
    GEOMETRIC_DISTANCE = "geometric_distance"
    WIND_ADJUSTED_DURATION = "wind_adjusted_duration"


class SweepDirectionMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class SweepAngleAlgorithm(StrEnum):
    FIXED_INPUT = "fixed_input"
    MINIMUM_ROTATED_RECTANGLE = "minimum_rotated_rectangle"
    MINIMUM_RECTANGLE_REFINED = "minimum_rectangle_refined"
    CONVEX_HULL_EDGES = "convex_hull_edges"
    GRID_30_DEGREES = "grid_30_degrees"
    GRID_15_DEGREES = "grid_15_degrees"
    GRID_10_DEGREES = "grid_10_degrees"
    GRID_5_DEGREES = "grid_5_degrees"
    COARSE_TO_FINE = "coarse_to_fine"
    EXHAUSTIVE_1_DEGREE = "exhaustive_1_degree"
    PARTITIONED_MULTI_ANGLE = "partitioned_multi_angle"
    ADAPTIVE_DECOMPOSITION = "adaptive_decomposition"
    ADAPTIVE_SECTION_DP = "adaptive_section_dp"
    ADAPTIVE_SECTION_DUBINS = "adaptive_section_dubins"
    ADAPTIVE_FULL_DUBINS = "adaptive_full_dubins"
    ADAPTIVE_HYBRID_ASTAR = "adaptive_hybrid_astar"
    ADAPTIVE_TURN_AWARE = "adaptive_turn_aware"
    PRODUCTION_ADAPTIVE = "production_adaptive"
    GEOMETRY_AWARE = "geometry_aware"


class RouteAlgorithm(StrEnum):
    BIDIRECTIONAL_VISIBILITY_GRAPH = "bidirectional_visibility_graph"


class OptimizationScope(StrEnum):
    GENERATED_ROUTE_ASSIGNMENT = "generated_route_assignment"


class OptimalityStatus(StrEnum):
    PROVEN_OPTIMAL = "proven_optimal"
    HEURISTIC = "heuristic"


class OptimizationMode(StrEnum):
    FAST_HEURISTIC = "fast_heuristic"
    OPTIMIZED = "optimized"


class DubinsPathType(StrEnum):
    LEFT_STRAIGHT_LEFT = "LSL"
    RIGHT_STRAIGHT_RIGHT = "RSR"
    LEFT_STRAIGHT_RIGHT = "LSR"
    RIGHT_STRAIGHT_LEFT = "RSL"
    RIGHT_LEFT_RIGHT = "RLR"
    LEFT_RIGHT_LEFT = "LRL"


class MissionTask(StrEnum):
    RGB = "rgb"
    MULTISPECTRAL = "multispectral"
    THERMAL = "thermal"
    LIDAR = "lidar"
    VIDEO = "video"
    GEOPHYSICAL = "geophysical"


class AutopilotFirmware(StrEnum):
    GENERIC_MAVLINK = "generic_mavlink"
    ARDUPILOT = "ardupilot"
    PX4 = "px4"


class AirframeType(StrEnum):
    FIXED_WING = "fixed_wing"
    QUADROTOR = "quadrotor"
    HEXAROTOR = "hexarotor"
    OCTOROTOR = "octorotor"
    VTOL_FIXED_ROTOR = "vtol_fixed_rotor"
    GENERIC_MULTIROTOR = "generic_multirotor"


class AltitudeReference(StrEnum):
    MSL = "msl"
    AGL = "agl"


class AutopilotProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    firmware: AutopilotFirmware = AutopilotFirmware.GENERIC_MAVLINK
    airframe: AirframeType = AirframeType.GENERIC_MULTIROTOR
    hover_speed_mps: float = Field(default=5, gt=0, le=100)


class ErrorCode(StrEnum):
    ALTERNATE_LANDING_UNREACHABLE = "alternate_landing_unreachable"
    AIRSPACE_WINDOW_UNAVAILABLE = "airspace_window_unavailable"
    COVERAGE_GENERATION_FAILED = "coverage_generation_failed"
    DRONE_NOT_FOUND = "drone_not_found"
    GSD_REQUIREMENT_UNSATISFIED = "gsd_requirement_unsatisfied"
    FLIGHT_ENVELOPE_EXCEEDED = "flight_envelope_exceeded"
    INVALID_IMPORT = "invalid_import"
    INVALID_REQUEST = "invalid_request"
    NO_CAPABLE_DRONE = "no_capable_drone"
    NO_ELIGIBLE_FLIGHT_SITE = "no_eligible_flight_site"
    NO_EXPORTABLE_MISSIONS = "no_exportable_missions"
    OUTSIDE_ALLOWED_AIRSPACE = "outside_allowed_airspace"
    ROUTE_BLOCKED = "route_blocked"
    SCHEDULE_GENERATION_FAILED = "schedule_generation_failed"
    TERRAIN_DATA_UNAVAILABLE = "terrain_data_unavailable"
    TURN_RADIUS_UNSATISFIED = "turn_radius_unsatisfied"
    TEMPERATURE_LIMIT_EXCEEDED = "temperature_limit_exceeded"
    UNPLANNABLE_SEGMENT = "unplannable_segment"
    VERTICAL_PROFILE_UNSATISFIED = "vertical_profile_unsatisfied"
    WIND_LIMIT_EXCEEDED = "wind_limit_exceeded"


class PayloadCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    supported_tasks: list[MissionTask] = Field(min_length=1, max_length=16)


class DroneSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

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

    def maximum_altitude_for(self, reference: AltitudeReference) -> float | None:
        specific = (
            self.maximum_mission_altitude_agl_m
            if reference is AltitudeReference.AGL
            else self.maximum_mission_altitude_msl_m
        )
        return specific if specific is not None else self.maximum_mission_altitude_m


class Coordinate(BaseModel):
    model_config = ConfigDict(frozen=True)

    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    altitude_m: float | None = Field(default=None, ge=-500, le=100_000)


class TerrainElevationSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    elevation_m: float = Field(ge=-500, le=10_000)


class InlineTerrainSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal["inline"] = "inline"
    samples: list[TerrainElevationSample] = Field(min_length=1, max_length=100_000)


class GeoTiffTerrainSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal["geotiff"] = "geotiff"
    content: Base64Bytes = Field(min_length=1, max_length=64 * 1024 * 1024)
    band: int = Field(default=1, ge=1, le=256)


TerrainSource = Annotated[
    InlineTerrainSource | GeoTiffTerrainSource,
    Field(discriminator="source_type"),
]


class VerticalProfilePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    distance_from_start_m: float = Field(ge=0)
    terrain_elevation_m: float = Field(ge=-500, le=10_000)
    target_altitude_msl: float = Field(ge=-500, le=100_000)
    target_agl_m: float = Field(gt=0, le=10_000)
    required_vertical_rate_mps: float = Field(ge=-100, le=100)


class DubinsPose(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    heading_degrees: float = Field(ge=0, lt=360)


class DubinsPathRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: DubinsPose
    end: DubinsPose
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)

    @model_validator(mode="after")
    def poses_do_not_describe_zero_length_path(self) -> DubinsPathRequest:
        if self.start == self.end:
            raise ValueError("Начальная и конечная позы Dubins-маршрута должны различаться.")
        return self


class DubinsPathResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path_type: DubinsPathType
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)


class FlightSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    coordinate: Coordinate
    assigned_drone_ids: list[str] = Field(default_factory=list, max_length=100)


class ValidityMode(StrEnum):
    PERMANENT = "permanent"
    TIME_WINDOW = "time_window"


class RestrictionValidity(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ValidityMode
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def time_window_is_complete(self) -> RestrictionValidity:
        if self.mode is ValidityMode.PERMANENT:
            return self
        if self.valid_from is None or self.valid_to is None:
            raise ValueError("Для временного ограничения нужны начало и окончание действия.")
        if self.valid_from.utcoffset() is None or self.valid_to.utcoffset() is None:
            raise ValueError("Временное ограничение должно содержать часовой пояс.")
        if self.valid_to <= self.valid_from:
            raise ValueError("Окончание ограничения должно быть позже начала.")
        return self


class AllowedAirspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    airspace_id: str = Field(min_length=1, max_length=64)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)
    minimum_altitude_m: float = Field(ge=-500, le=100_000)
    maximum_altitude_m: float = Field(gt=-500, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    validity: RestrictionValidity | None = None

    @model_validator(mode="after")
    def maximum_altitude_exceeds_minimum(self) -> AllowedAirspace:
        if self.maximum_altitude_m <= self.minimum_altitude_m:
            raise ValueError("Максимальная высота должна быть больше минимальной.")
        return self


class WindCondition(BaseModel):
    model_config = ConfigDict(frozen=True)

    speed_mps: float = Field(ge=0, le=100)
    direction_degrees: float = Field(ge=0, lt=360)


class ZoneType(StrEnum):
    ALLOWED_AIRSPACE = "allowed_airspace"
    WORK_AREA = "work_area"
    NO_FLY_ZONE = "no_fly_zone"
    OBSTACLE = "obstacle"


class ImportedGeometryType(StrEnum):
    POINT = "point"
    POLYGON = "polygon"
    LINE_STRING = "line_string"


class AltitudeMode(StrEnum):
    ABSOLUTE = "absolute"
    CLAMP_TO_GROUND = "clampToGround"
    RELATIVE_TO_GROUND = "relativeToGround"
    RELATIVE_TO_SEA_FLOOR = "relativeToSeaFloor"
    UNKNOWN = "unknown"


class AirspaceRestrictionType(StrEnum):
    PERMANENT = "permanent"
    PROHIBITED = "prohibited"
    SPECIAL = "special"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class ImportFormat(StrEnum):
    GEOJSON = "geojson"
    KML = "kml"


class ImportedZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    zone_type: ZoneType
    geometry_type: ImportedGeometryType = ImportedGeometryType.POLYGON
    coordinates: list[Coordinate] = Field(min_length=1, max_length=100_000)
    interior_rings: list[list[Coordinate]] = Field(default_factory=list, max_length=1_000)
    altitude_mode: AltitudeMode = AltitudeMode.UNKNOWN
    restriction_type: AirspaceRestrictionType | None = None
    altitude_text: str | None = Field(default=None, max_length=4_000)
    minimum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    maximum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    altitude_reference: AltitudeReference | None = None
    validity: RestrictionValidity | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def polygon_has_enough_coordinates(self) -> ImportedZone:
        if self.geometry_type is ImportedGeometryType.POLYGON and len(self.coordinates) < 3:
            raise ValueError("Полигон должен содержать не менее трёх координат.")
        if self.geometry_type is ImportedGeometryType.LINE_STRING and len(self.coordinates) < 2:
            raise ValueError("Линия должна содержать не менее двух координат.")
        if (self.minimum_altitude_m is None) != (self.maximum_altitude_m is None):
            raise ValueError("Высотный диапазон должен содержать нижнюю и верхнюю границы.")
        if (
            self.minimum_altitude_m is not None
            and self.maximum_altitude_m is not None
            and self.maximum_altitude_m <= self.minimum_altitude_m
        ):
            raise ValueError("Верхняя граница высоты должна быть выше нижней.")
        if self.minimum_altitude_m is not None and self.altitude_reference is None:
            raise ValueError("Для числового высотного диапазона нужна система отсчёта AGL/MSL.")
        return self


class ImportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_file_name: str = Field(min_length=1, max_length=256)
    source_format: ImportFormat
    zones: list[ImportedZone] = Field(min_length=1, max_length=20_000)


class GeoJsonPosition(RootModel[list[float]]):
    root: list[float] = Field(min_length=2, max_length=3)


class GeoJsonZoneProperties(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str | int | None = None
    name: str | None = None
    altitude_mode: AltitudeMode = AltitudeMode.UNKNOWN
    altitude_text: str | None = None
    minimum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    maximum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    altitude_reference: AltitudeReference | None = None
    restriction_type: AirspaceRestrictionType | None = None
    validity: RestrictionValidity | None = None

    @model_validator(mode="after")
    def altitude_range_is_complete(self) -> GeoJsonZoneProperties:
        if (self.minimum_altitude_m is None) != (self.maximum_altitude_m is None):
            raise ValueError("Высотный диапазон GeoJSON требует нижнюю и верхнюю границы.")
        if (
            self.minimum_altitude_m is not None
            and self.maximum_altitude_m is not None
            and self.maximum_altitude_m <= self.minimum_altitude_m
        ):
            raise ValueError("Верхняя граница высоты должна быть выше нижней.")
        if self.minimum_altitude_m is not None and self.altitude_reference is None:
            raise ValueError("Высотный диапазон GeoJSON требует систему отсчёта AGL/MSL.")
        return self


class GeoJsonPolygonGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["Polygon"]
    coordinates: list[list[GeoJsonPosition]] = Field(min_length=1, max_length=1_000)


class GeoJsonMultiPolygonGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["MultiPolygon"]
    coordinates: list[list[list[GeoJsonPosition]]] = Field(min_length=1, max_length=1_000)


GeoJsonZoneGeometry = GeoJsonPolygonGeometry | GeoJsonMultiPolygonGeometry


class GeoJsonZoneFeature(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["Feature"]
    properties: GeoJsonZoneProperties = Field(default_factory=GeoJsonZoneProperties)
    geometry: GeoJsonZoneGeometry


class GeoJsonZoneFeatureCollection(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["FeatureCollection"]
    features: list[GeoJsonZoneFeature] = Field(min_length=1, max_length=1_000)


class GeoJsonImportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_file_name: str = Field(min_length=1, max_length=256)
    zone_type: ZoneType
    content: Json[GeoJsonZoneFeatureCollection]


class KmlImportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_file_name: str = Field(min_length=1, max_length=256)
    zone_type: ZoneType
    content: str = Field(min_length=1, max_length=25_000_000)


class WorkSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1, max_length=64)
    distance_km: float = Field(gt=0, le=5_000)
    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    alternative_routes: list[list[Coordinate]] = Field(default_factory=list, max_length=8)
    coverage_area_id: str | None = Field(default=None, min_length=1, max_length=64)
    image_count: int | None = Field(default=None, ge=1)
    computed_gsd_cm: float | None = Field(default=None, gt=0)
    strip_spacing_m: float | None = Field(default=None, gt=0)
    survey_speed_limit_kmh: float | None = Field(default=None, gt=0)
    flight_direction_degrees: float | None = Field(default=None, ge=0, lt=180)


class CoverageArea(BaseModel):
    model_config = ConfigDict(frozen=True)

    area_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=100_000)
    interior_rings: list[list[Coordinate]] = Field(default_factory=list, max_length=1_000)


class SensorProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensor_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    sensor_width_mm: float = Field(gt=0, le=200)
    sensor_height_mm: float = Field(gt=0, le=200)
    focal_length_mm: float = Field(gt=0, le=1_000)
    image_width_px: int = Field(gt=0, le=200_000)
    image_height_px: int = Field(gt=0, le=200_000)
    minimum_trigger_interval_s: float = Field(gt=0, le=120)


class SurveyParameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction_mode: SweepDirectionMode = SweepDirectionMode.AUTOMATIC
    flight_direction_degrees: float = Field(ge=0, lt=180)
    side_overlap_percent: float = Field(ge=0, lt=95)
    forward_overlap_percent: float = Field(ge=0, lt=95)
    maximum_gsd_cm: float = Field(gt=0, le=100)
    strip_width_m: float = Field(gt=0, le=5_000)
    maximum_segment_distance_km: float = Field(default=20, gt=0, le=500)
    sensor: SensorProfile


class NoFlyZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_id: str = Field(min_length=1, max_length=64)
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)
    interior_rings: list[list[Coordinate]] = Field(default_factory=list, max_length=1_000)
    safety_margin_m: float = Field(default=30, ge=0, le=5_000)
    minimum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    maximum_altitude_m: float | None = Field(default=None, ge=-500, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    validity: RestrictionValidity | None = None

    @model_validator(mode="after")
    def altitude_range_is_complete(self) -> NoFlyZone:
        if (self.minimum_altitude_m is None) != (self.maximum_altitude_m is None):
            raise ValueError("Высотный диапазон запретной зоны требует нижнюю и верхнюю границы.")
        if (
            self.minimum_altitude_m is not None
            and self.maximum_altitude_m is not None
            and self.maximum_altitude_m <= self.minimum_altitude_m
        ):
            raise ValueError("Верхняя граница запретной зоны должна быть выше нижней.")
        return self


class HybridAStarPathRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: DubinsPose
    end: DubinsPose
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    primitive_length_m: float = Field(default=20, ge=2, le=5_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)
    grid_resolution_m: float = Field(default=10, ge=1, le=1_000)
    heading_bin_degrees: float = Field(default=15, ge=1, le=90)
    analytic_expansion_distance_m: float = Field(default=200, gt=0, le=100_000)
    maximum_expansions: int = Field(default=50_000, ge=1, le=2_000_000)

    @model_validator(mode="after")
    def poses_do_not_describe_zero_length_path(self) -> HybridAStarPathRequest:
        if self.start == self.end:
            raise ValueError("Начальная и конечная позы Hybrid A* должны различаться.")
        return self


class HybridAStarPathResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)
    expanded_states: int = Field(ge=0)
    optimality: OptimalityStatus = OptimalityStatus.HEURISTIC


class SweepRouteCurvatureRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    coverage_area: CoverageArea
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)

    @model_validator(mode="after")
    def route_contains_complete_survey_legs(self) -> SweepRouteCurvatureRequest:
        if len(self.route) % 2 != 0:
            raise ValueError("Маршрут галсов должен содержать чётное число точек.")
        return self


class SweepRouteCurvatureResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(min_length=1, max_length=5_000)
    transition_path_types: list[DubinsPathType] = Field(default_factory=list, max_length=5_000)
    distance_km: float = Field(gt=0)
    transition_distance_km: float = Field(ge=0)


class TurnAwareSweepRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    coverage_area: CoverageArea
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)
    strip_width_m: float = Field(gt=0, le=5_000)
    footprint_height_m: float = Field(gt=0, le=5_000)

    @model_validator(mode="after")
    def route_contains_complete_survey_legs(self) -> TurnAwareSweepRequest:
        if len(self.route) % 2 != 0:
            raise ValueError("Маршрут галсов должен содержать чётное число точек.")
        return self


class TurnAwareSweepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(min_length=1, max_length=5_000)
    distance_km: float = Field(gt=0)
    transition_distance_km: float = Field(ge=0)
    endpoint_retreat_m: float = Field(ge=0)
    uncovered_area_m2: float = Field(ge=0)


class FlightObstacle(BaseModel):
    model_config = ConfigDict(frozen=True)

    obstacle_id: str = Field(min_length=1, max_length=64)
    geometry_type: ImportedGeometryType
    coordinates: list[Coordinate] = Field(min_length=1, max_length=100_000)
    height_m: float = Field(ge=0, le=100_000)
    altitude_mode: AltitudeMode = AltitudeMode.UNKNOWN
    safety_radius_m: float = Field(default=30, gt=0, le=5_000)


class PlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drones: list[DroneSpec] = Field(min_length=1, max_length=100)
    segments: list[WorkSegment] = Field(default_factory=list, max_length=10_000)
    coverage_areas: list[CoverageArea] = Field(default_factory=list, max_length=1_000)
    survey_parameters: SurveyParameters | None = None
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    obstacles: list[FlightObstacle] = Field(default_factory=list, max_length=20_000)
    mission_altitude_m: float = Field(gt=0, le=100_000)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    terrain_elevation_samples: list[TerrainElevationSample] = Field(
        default_factory=list,
        max_length=100_000,
    )
    terrain_source: TerrainSource | None = None
    terrain_vertical_uncertainty_m: float = Field(default=0, ge=0, le=500)
    terrain_sampling_interval_m: float = Field(default=25, ge=5, le=1_000)
    terrain_max_interpolation_distance_m: float = Field(default=5_000, gt=0, le=100_000)
    task: MissionTask
    departure_site: FlightSite | None = None
    landing_site: FlightSite | None = None
    departure_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    landing_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    alternate_landing_sites: list[FlightSite] = Field(default_factory=list, max_length=100)
    allowed_airspaces: list[AllowedAirspace] = Field(default_factory=list, max_length=1_000)
    wind: WindCondition | None = None
    ambient_temperature_c: float | None = Field(default=None, ge=-100, le=100)
    maximum_active_drones: int | None = Field(default=None, gt=0, le=100)
    completion_deadline_minutes: float | None = Field(default=None, gt=0, le=10_080)
    minimum_separation_m: float = Field(default=50, gt=0, le=10_000)
    minimum_vertical_separation_m: float = Field(default=30, gt=0, le=1_000)
    vertical_deconfliction_step_m: float = Field(default=30, gt=0, le=500)
    maximum_vertical_deconfliction_m: float = Field(default=0, ge=0, le=1_000)
    position_uncertainty_m: float = Field(default=0, ge=0, le=1_000)
    start_time_uncertainty_seconds: float = Field(default=0, ge=0, le=300)
    speed_uncertainty_percent: float = Field(default=0, ge=0, le=50)
    flight_site_occupancy_seconds: float = Field(default=0, ge=0, le=600)
    turnaround_minutes: float = Field(default=0, ge=0, le=1_440)
    minimum_turn_radius_m: float = Field(default=0, ge=0, le=10_000)
    optimization_mode: OptimizationMode = OptimizationMode.OPTIMIZED
    planning_start_time: datetime | None = None
    planning_end_time: datetime | None = None

    @model_validator(mode="after")
    def has_route_source(self) -> PlanRequest:
        if not self.segments and not self.coverage_areas:
            raise ValueError("Нужен хотя бы один участок или целевая зона покрытия.")
        if self.coverage_areas and self.survey_parameters is None:
            raise ValueError("Для целевой зоны нужны параметры съёмки.")
        if self.altitude_reference is AltitudeReference.AGL:
            if not self.terrain_elevation_samples and self.terrain_source is None:
                raise ValueError("Для высоты AGL нужны отметки рельефа.")
            if self.terrain_elevation_samples and self.terrain_source is not None:
                raise ValueError(
                    "Specify either legacy terrain samples or terrain_source, not both."
                )
            if any(
                drone.maximum_climb_rate_mps is None or drone.maximum_descent_rate_mps is None
                for drone in self.drones
                if self.task in drone.supported_tasks
            ):
                raise ValueError("Для высоты AGL задайте пределы набора и снижения каждого БВС.")
        elif any(
            airspace.altitude_reference is AltitudeReference.AGL
            for airspace in self.allowed_airspaces
        ) or any(
            zone.altitude_reference is AltitudeReference.AGL and zone.minimum_altitude_m is not None
            for zone in self.no_fly_zones
        ):
            raise ValueError(
                "Ограничения AGL требуют режима задания AGL и данных рельефа; "
                "direct comparison with MSL is not permitted."
            )
        if self.minimum_turn_radius_m > 0 and self.segments:
            raise ValueError("Радиус разворота поддерживается для генерируемых галсов.")
        if self.maximum_active_drones is not None and self.maximum_active_drones > len(self.drones):
            raise ValueError("Ограничение числа БВС превышает размер доступного парка.")
        if (
            self.maximum_vertical_deconfliction_m > 0
            and self.vertical_deconfliction_step_m < self.minimum_vertical_separation_m
        ):
            raise ValueError(
                "Шаг вертикального эшелонирования должен быть не меньше "
                "минимального вертикального разделения."
            )
        if self.planning_start_time is not None and self.planning_start_time.utcoffset() is None:
            raise ValueError("Время начала плана должно содержать часовой пояс.")
        if self.planning_end_time is not None and self.planning_end_time.utcoffset() is None:
            raise ValueError("Время окончания плана должно содержать часовой пояс.")
        if self.planning_end_time is not None and self.planning_start_time is None:
            raise ValueError("Для окончания рабочего окна задайте начало плана.")
        if (
            self.planning_start_time is not None
            and self.planning_end_time is not None
            and self.planning_end_time <= self.planning_start_time
        ):
            raise ValueError("Окончание рабочего окна должно быть позже начала плана.")
        has_timed_airspace = any(
            airspace.validity is not None and airspace.validity.mode is ValidityMode.TIME_WINDOW
            for airspace in self.allowed_airspaces
        )
        if has_timed_airspace and self.planning_start_time is None:
            raise ValueError("Для временного воздушного пространства задайте начало плана.")
        has_timed_no_fly_zone = any(
            zone.validity is not None and zone.validity.mode is ValidityMode.TIME_WINDOW
            for zone in self.no_fly_zones
        )
        if has_timed_no_fly_zone and self.planning_start_time is None:
            raise ValueError("Для временной запретной зоны задайте начало плана.")
        if (
            self.criterion is OptimizationCriterion.BALANCED
            and self.completion_deadline_minutes is None
        ):
            raise ValueError("Для сбалансированного плана необходимо задать срок выполнения.")
        aerial_imaging_tasks = {
            MissionTask.RGB,
            MissionTask.MULTISPECTRAL,
            MissionTask.THERMAL,
        }
        if (
            self.task in aerial_imaging_tasks
            and self.survey_parameters is not None
            and self.survey_parameters.side_overlap_percent < 50
        ):
            raise ValueError("Для аэрофотосъёмки боковое перекрытие должно быть не менее 50%.")
        return self


class Mission(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    segment_id: str
    duration_minutes: float = Field(gt=0)
    distance_km: float = Field(gt=0)
    route: list[Coordinate] = Field(min_length=2)
    altitude_m: float = Field(gt=0)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    vertical_profile: list[VerticalProfilePoint] = Field(default_factory=list, max_length=100_000)
    minimum_agl_m: float | None = Field(default=None, gt=0)
    minimum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    maximum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    maximum_required_climb_rate_mps: float | None = Field(default=None, ge=0, le=100)
    maximum_required_descent_rate_mps: float | None = Field(default=None, ge=0, le=100)
    vertical_distance_m: float = Field(default=0, ge=0)
    vertical_energy_equivalent_minutes: float = Field(default=0, ge=0)
    vertical_climb_duration_minutes: float = Field(default=0, ge=0)
    vertical_descent_duration_minutes: float = Field(default=0, ge=0)
    terrain_vertical_uncertainty_m: float = Field(default=0, ge=0, le=500)
    vertical_deconfliction_offset_m: float = Field(default=0, ge=0, le=1_000)
    cruise_speed_kmh: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    estimated_battery_percent: float = Field(gt=0)
    starting_battery_percent: float = Field(default=100, gt=0, le=100)
    remaining_battery_percent: float | None = Field(default=None, ge=0, le=100)
    battery_reserve_margin_percent: float | None = None
    minimum_alternate_margin_percent: float | None = None
    critical_alternate_site_id: str | None = Field(default=None, min_length=1, max_length=64)
    critical_alternate_route: list[Coordinate] = Field(default_factory=list, max_length=100_000)
    critical_alternate_start_offset_minutes: float | None = Field(default=None, ge=0)
    critical_alternate_duration_minutes: float | None = Field(default=None, ge=0)
    alternate_check_spacing_m: float | None = Field(default=None, gt=0)
    alternate_evaluated_checkpoint_count: int | None = Field(default=None, gt=0)
    alternate_certification_error_minutes: float | None = Field(default=None, ge=0)
    survey_distance_km: float | None = Field(default=None, ge=0)
    transit_distance_km: float | None = Field(default=None, ge=0)
    survey_duration_minutes: float | None = Field(default=None, ge=0)
    transit_duration_minutes: float | None = Field(default=None, ge=0)
    useful_flight_percent: float | None = Field(default=None, ge=0, le=100)
    image_count: int | None = Field(default=None, ge=1)
    computed_gsd_cm: float | None = Field(default=None, gt=0)
    strip_spacing_m: float | None = Field(default=None, gt=0)
    flight_direction_degrees: float | None = Field(default=None, ge=0, lt=180)
    departure_site: FlightSite | None = None
    landing_site: FlightSite | None = None
    start_offset_minutes: float | None = Field(default=None, ge=0)
    end_offset_minutes: float | None = Field(default=None, gt=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    stages: list[MissionStage] = Field(default_factory=list)

    @model_validator(mode="after")
    def schedule_offsets_are_complete(self) -> Mission:
        if (
            self.vertical_climb_duration_minutes + self.vertical_descent_duration_minutes
            >= self.duration_minutes
        ):
            raise ValueError(
                "Суммарная длительность вертикальных фаз должна быть меньше длительности миссии."
            )
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("Абсолютные начало и окончание миссии должны быть заданы вместе.")
        if self.start_offset_minutes is None and self.end_offset_minutes is None:
            if self.start_time is not None:
                raise ValueError("Абсолютное расписание требует относительных смещений.")
            return self
        if self.start_offset_minutes is None or self.end_offset_minutes is None:
            raise ValueError("Начало и окончание миссии должны быть заданы вместе.")
        if self.end_offset_minutes <= self.start_offset_minutes:
            raise ValueError("Окончание миссии должно быть позже начала.")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("Абсолютное окончание миссии должно быть позже начала.")
        return self


class MissionStageType(StrEnum):
    TAKEOFF = "takeoff"
    TRANSIT = "transit"
    SURVEY = "survey"
    LANDING = "landing"


class MissionStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_type: MissionStageType
    coordinates: list[Coordinate] = Field(min_length=1)
    duration_minutes: float = Field(default=0, ge=0)


class ScheduleTimeWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_offset_minutes: float = Field(ge=0)
    end_offset_minutes: float = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> ScheduleTimeWindow:
        if self.end_offset_minutes <= self.start_offset_minutes:
            raise ValueError("Окончание окна должно быть позже начала.")
        return self


class VerticalLayerOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    offset_m: float = Field(ge=0, le=1_000)
    climb_duration_minutes: float = Field(default=0, ge=0)
    descent_duration_minutes: float = Field(default=0, ge=0)
    extra_energy_equivalent_minutes: float = Field(ge=0)
    extra_battery_percent: float = Field(ge=0, le=100)


class MissionScheduleRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[Mission] = Field(min_length=1, max_length=10_000)
    minimum_separation_m: float = Field(gt=0, le=10_000)
    minimum_vertical_separation_m: float = Field(default=30, gt=0, le=1_000)
    position_uncertainty_m: float = Field(default=0, ge=0, le=1_000)
    start_time_uncertainty_seconds: float = Field(default=0, ge=0, le=300)
    speed_uncertainty_percent: float = Field(default=0, ge=0, le=50)
    flight_site_occupancy_seconds: float = Field(default=0, ge=0, le=600)
    turnaround_minutes: float = Field(ge=0, le=1_440)
    turnaround_minutes_by_drone: dict[str, float] = Field(default_factory=dict)
    delay_step_seconds: float = Field(default=5, gt=0, le=300)
    planning_start_time: datetime | None = None
    time_windows_by_segment_id: dict[str, list[ScheduleTimeWindow]] = Field(default_factory=dict)
    vertical_layers_by_segment_id: dict[str, list[VerticalLayerOption]] = Field(
        default_factory=dict
    )


class ResolvedScheduleConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    conflict_type: ScheduleConflictType
    delayed_drone_id: str = Field(min_length=1, max_length=64)
    delayed_segment_id: str = Field(min_length=1, max_length=64)
    conflicting_drone_id: str = Field(min_length=1, max_length=64)
    conflicting_segment_id: str = Field(min_length=1, max_length=64)
    coordinate: Coordinate
    conflict_offset_minutes: float = Field(ge=0)
    nominal_distance_m: float = Field(ge=0)
    required_distance_m: float = Field(gt=0)
    applied_delay_minutes: float = Field(ge=0)
    applied_vertical_offset_m: float = Field(default=0, ge=0, le=1_000)


class MissionScheduleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    missions: list[Mission] = Field(min_length=1, max_length=10_000)
    makespan_minutes: float = Field(gt=0)
    resolved_conflict_count: int = Field(ge=0)
    resolved_conflicts: list[ResolvedScheduleConflict] = Field(
        default_factory=list, max_length=10_000
    )
    algorithm: ScheduleAlgorithm


class DronePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    mission_count: int = Field(ge=0)
    total_duration_minutes: float = Field(ge=0)
    missions: list[Mission]
    autopilot_profile: AutopilotProfile = Field(default_factory=AutopilotProfile)
    maintenance: MaintenanceReport | None = None


class MaintenanceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    projected_flights: int = Field(ge=0)
    projected_flight_hours: float = Field(ge=0)
    remaining_flights: int | None = Field(default=None, ge=0)
    remaining_hours: float | None = Field(default=None, ge=0)
    maintenance_due_after_plan: bool
    message: str = Field(min_length=1, max_length=256)


class PlanMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    total_distance_km: float = Field(ge=0)
    survey_distance_km: float = Field(default=0, ge=0)
    transit_distance_km: float = Field(default=0, ge=0)
    useful_flight_percent: float = Field(default=0, ge=0, le=100)
    average_fleet_utilization_percent: float = Field(ge=0, le=100)
    active_drone_count: int = Field(default=0, ge=0)
    sortie_count: int = Field(default=0, ge=0)


class FeasibilityRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: RecommendationCode
    message: str = Field(min_length=1, max_length=512)
    confidence: RecommendationConfidence
    parameter: str | None = Field(default=None, min_length=1, max_length=64)
    current_value: float | None = None
    required_value: float | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    affected_ids: list[str] = Field(default_factory=list, max_length=10_000)


class FeasibilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: FeasibilityStatus
    message: str = Field(min_length=1, max_length=512)
    requested_deadline_minutes: float | None = Field(default=None, gt=0)
    maximum_active_drones: int | None = Field(default=None, gt=0)
    used_drone_count: int = Field(ge=0)
    estimated_required_drone_count: int | None = Field(default=None, gt=0)
    deadline_margin_minutes: float | None = None
    recommendations: list[FeasibilityRecommendation] = Field(default_factory=list, max_length=32)


class MissionAssignmentCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1, max_length=64)
    drone_id: str = Field(min_length=1, max_length=64)
    mission: Mission


class AssignmentOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drone_ids: list[str] = Field(min_length=1, max_length=100)
    candidate_groups: list[list[MissionAssignmentCandidate]] = Field(
        min_length=1,
        max_length=10_000,
    )
    maximum_active_drones: int | None = Field(default=None, gt=0, le=100)
    completion_deadline_minutes: float | None = Field(default=None, gt=0, le=10_080)
    optimization_mode: OptimizationMode = OptimizationMode.OPTIMIZED
    turnaround_minutes_by_drone: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def candidate_groups_match_problem(self) -> AssignmentOptimizationRequest:
        known_drones = set(self.drone_ids)
        if len(known_drones) != len(self.drone_ids):
            raise ValueError("Идентификаторы БВС в задаче распределения должны быть уникальны.")
        if any(not group for group in self.candidate_groups):
            raise ValueError("Для каждого участка нужен хотя бы один допустимый кандидат.")
        if any(
            candidate.drone_id not in known_drones
            for group in self.candidate_groups
            for candidate in group
        ):
            raise ValueError("Кандидат ссылается на неизвестный БВС.")
        if self.maximum_active_drones is not None and self.maximum_active_drones > len(
            known_drones
        ):
            raise ValueError("Лимит активных БВС превышает размер парка.")
        if any(
            drone_id not in known_drones or minutes < 0 or minutes > 1_440
            for drone_id, minutes in self.turnaround_minutes_by_drone.items()
        ):
            raise ValueError("Время между вылетами задано неверно.")
        return self


class AssignmentOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assignments: list[MissionAssignmentCandidate] = Field(min_length=1, max_length=10_000)
    algorithm: AssignmentAlgorithm
    optimality: OptimalityStatus
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    active_drone_count: int = Field(gt=0)


class OptimizationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: AssignmentAlgorithm
    coverage_algorithm: CoverageAlgorithm
    route_algorithm: RouteAlgorithm
    scope: OptimizationScope
    optimality: OptimalityStatus
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    active_drone_count: int = Field(gt=0)
    survey_turn_radius_m: float = Field(default=0, ge=0, le=10_000)
    coverage_angle_cost: CoverageAngleCost = CoverageAngleCost.GEOMETRIC_DISTANCE
    coverage_reference_airspeed_kmh: float | None = Field(default=None, gt=0, le=300)


class ScheduleSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: ScheduleAlgorithm
    makespan_minutes: float = Field(gt=0)
    minimum_separation_m: float = Field(gt=0)
    minimum_vertical_separation_m: float = Field(default=30, gt=0)
    vertically_deconflicted_mission_count: int = Field(default=0, ge=0)
    position_uncertainty_m: float = Field(ge=0)
    start_time_uncertainty_seconds: float = Field(ge=0)
    speed_uncertainty_percent: float = Field(ge=0)
    flight_site_occupancy_seconds: float = Field(ge=0)
    turnaround_minutes: float = Field(ge=0)
    resolved_conflict_count: int = Field(ge=0)
    resolved_conflicts: list[ResolvedScheduleConflict] = Field(
        default_factory=list, max_length=10_000
    )


class SafetyAuditCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: SafetyCheckCode
    status: SafetyAuditStatus
    measured_value: float | None = None
    required_value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    message: str = Field(min_length=1, max_length=512)
    affected_ids: list[str] = Field(default_factory=list, max_length=10_000)


class SafetyAuditReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: SafetyAuditStatus
    audited_mission_count: int = Field(ge=0)
    checks: list[SafetyAuditCheck] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list, max_length=100)


class PlanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: OptimizationCriterion
    drone_plans: list[DronePlan]
    metrics: PlanMetrics
    feasibility: FeasibilityReport | None = None
    optimization: OptimizationSummary | None = None
    schedule: ScheduleSummary | None = None
    safety_audit: SafetyAuditReport | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: PlanResponse
    drone_id: str = Field(min_length=1, max_length=64)


class GeoJsonGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2)


class GeoJsonProperties(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    segment_id: str
    duration_minutes: float = Field(gt=0)
    distance_km: float = Field(gt=0)
    altitude_m: float = Field(gt=0)
    altitude_reference: AltitudeReference = AltitudeReference.MSL
    minimum_agl_m: float | None = Field(default=None, gt=0)
    minimum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    maximum_terrain_elevation_m: float | None = Field(default=None, ge=-500, le=10_000)
    vertical_distance_m: float = Field(default=0, ge=0)
    vertical_energy_equivalent_minutes: float = Field(default=0, ge=0)
    vertical_climb_duration_minutes: float = Field(default=0, ge=0)
    vertical_descent_duration_minutes: float = Field(default=0, ge=0)
    terrain_vertical_uncertainty_m: float = Field(default=0, ge=0, le=500)
    vertical_deconfliction_offset_m: float = Field(default=0, ge=0, le=1_000)
    cruise_speed_kmh: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    estimated_battery_percent: float = Field(gt=0)
    starting_battery_percent: float = Field(default=100, gt=0, le=100)
    remaining_battery_percent: float | None = Field(default=None, ge=0, le=100)
    battery_reserve_margin_percent: float | None = None
    minimum_alternate_margin_percent: float | None = None
    critical_alternate_site_id: str | None = None
    alternate_check_spacing_m: float | None = Field(default=None, gt=0)
    alternate_evaluated_checkpoint_count: int | None = Field(default=None, gt=0)
    alternate_certification_error_minutes: float | None = Field(default=None, ge=0)
    survey_duration_minutes: float | None = Field(default=None, ge=0)
    transit_duration_minutes: float | None = Field(default=None, ge=0)
    start_offset_minutes: float | None = Field(default=None, ge=0)
    end_offset_minutes: float | None = Field(default=None, gt=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    image_count: int | None = Field(default=None, ge=1)
    computed_gsd_cm: float | None = Field(default=None, gt=0)
    strip_spacing_m: float | None = Field(default=None, gt=0)
    stages: list[MissionStage] = Field(default_factory=list)


class RouteOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    alternative_routes: list[list[Coordinate]] = Field(default_factory=list, max_length=8)
    departure: Coordinate
    landings: list[Coordinate] = Field(default_factory=list, max_length=100)
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)


class RouteOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2)
    reversed_for_shorter_transit: bool
    selected_route_index: int = Field(default=0, ge=0)


class SectionRouteVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str = Field(min_length=1, max_length=64)
    variant_id: str = Field(min_length=1, max_length=64)
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(default_factory=list, max_length=5_000)


class SectionRouteOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant_groups: list[list[SectionRouteVariant]] = Field(min_length=1, max_length=12)
    coverage_area: CoverageArea
    departure_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    landing_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def groups_contain_unique_sections_and_variants(self) -> SectionRouteOptimizationRequest:
        if any(not group for group in self.variant_groups):
            raise ValueError("Каждая секция должна содержать хотя бы один вариант маршрута.")
        section_ids = [group[0].section_id for group in self.variant_groups]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Идентификаторы секций должны быть уникальными.")
        for group in self.variant_groups:
            section_id = group[0].section_id
            if any(variant.section_id != section_id for variant in group):
                raise ValueError("Группа содержит варианты разных секций.")
            variant_ids = [variant.variant_id for variant in group]
            if len(variant_ids) != len(set(variant_ids)):
                raise ValueError("Идентификаторы вариантов внутри секции должны быть уникальными.")
        return self


class SectionRouteSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str = Field(min_length=1, max_length=64)
    variant_id: str = Field(min_length=1, max_length=64)


class SectionRouteOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    selections: list[SectionRouteSelection] = Field(min_length=1, max_length=12)
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)
    turn_count: int = Field(ge=0)
    departure: Coordinate | None = None
    landing: Coordinate | None = None
    optimality: OptimalityStatus


class MissionDurationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    distance_km: float = Field(gt=0)
    cruise_speed_kmh: float = Field(gt=0)
    route: list[Coordinate] = Field(min_length=2)
    wind: WindCondition | None = None
    speed_limit_kmh: float | None = Field(default=None, gt=0)
    transit_route: list[Coordinate] | None = Field(default=None, min_length=2)
    transit_distance_km: float | None = Field(default=None, ge=0)
    survey_route: list[Coordinate] | None = Field(default=None, min_length=2)
    survey_distance_km: float | None = Field(default=None, gt=0)
    landing_route: list[Coordinate] | None = Field(default=None, min_length=2)
    landing_distance_km: float | None = Field(default=None, ge=0)


class CoveragePlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    area: CoverageArea
    mission_altitude_m: float = Field(gt=0, le=100_000)
    parameters: SurveyParameters
    exclusions: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    departure_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    landing_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    wind: WindCondition | None = None
    reference_airspeed_kmh: float | None = Field(default=None, gt=0, le=300)


class CoveragePlanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: list[WorkSegment] = Field(min_length=1, max_length=10_000)
    computed_gsd_cm: float = Field(gt=0)
    footprint_width_m: float = Field(gt=0)
    footprint_height_m: float = Field(gt=0)
    strip_spacing_m: float = Field(gt=0)
    trigger_spacing_m: float = Field(gt=0)
    selected_flight_direction_degrees: float = Field(ge=0, lt=180)
    angle_algorithm: SweepAngleAlgorithm


class TurnRadiusStatus(StrEnum):
    FEASIBLE = "feasible"
    NO_ROUTE_FOR_SELECTED_SWEEPS = "no_route_for_selected_sweeps"
    NOT_EVALUATED = "not_evaluated"


class TurnRadiusAnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    coverage: CoveragePlanRequest
    allowed_airspaces: list[AllowedAirspace] = Field(default_factory=list, max_length=1_000)
    radii_m: list[float] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def radii_are_distinct_and_ascending(self) -> TurnRadiusAnalysisRequest:
        if any(not isfinite(radius) or radius < 0 or radius > 10_000 for radius in self.radii_m):
            raise ValueError("Радиусы должны быть в диапазоне [0, 10 000] м.")
        if self.radii_m != sorted(set(self.radii_m)):
            raise ValueError("Радиусы должны быть различными и перечислены по возрастанию.")
        return self


class TurnRadiusScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    radius_m: float = Field(ge=0, le=10_000)
    status: TurnRadiusStatus
    message: str = Field(min_length=1)
    distance_km: float | None = Field(default=None, gt=0)
    uncovered_area_m2: float | None = Field(default=None, ge=0)


class TurnRadiusAnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    area_id: str = Field(min_length=1)
    scenarios: list[TurnRadiusScenario] = Field(min_length=1, max_length=5)
    common_survey_route: list[Coordinate] | None = None
    message: str = Field(min_length=1)


class MissionDurationEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    duration_minutes: float = Field(gt=0)
    effective_speed_kmh: float = Field(gt=0)
    transit_duration_minutes: float = Field(default=0, ge=0)
    survey_duration_minutes: float = Field(default=0, ge=0)
    landing_duration_minutes: float = Field(default=0, ge=0)


class BenchmarkDrone(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str = Field(min_length=1, max_length=64)
    cruise_speed_kmh: float = Field(gt=0, le=300)


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=64)
    distance_km: float = Field(gt=0, le=5_000)
    transit_minutes_by_drone: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def transit_minutes_are_non_negative(self) -> BenchmarkTask:
        if any(value < 0 for value in self.transit_minutes_by_drone.values()):
            raise ValueError("Время транзита не может быть отрицательным.")
        return self


class OptimizationBenchmarkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    drones: list[BenchmarkDrone] = Field(min_length=2, max_length=20)
    tasks: list[BenchmarkTask] = Field(min_length=2, max_length=20)
    random_seed: int = Field(default=42, ge=0)


class BenchmarkAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    task_ids: list[str]
    duration_minutes: float = Field(ge=0)


class OptimizationBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: str
    completion_minutes: float = Field(ge=0)
    total_flight_minutes: float = Field(ge=0)
    runtime_ms: float = Field(ge=0)
    assignments: list[BenchmarkAssignment]


class CoverageBenchmarkCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=256)
    request: CoveragePlanRequest
    partitioned_requests: list[CoveragePlanRequest] = Field(default_factory=list, max_length=100)


class CoverageBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    algorithm: SweepAngleAlgorithm
    selected_angle_degrees: float | None = Field(default=None, ge=0, lt=180)
    route_distance_km: float | None = Field(default=None, gt=0)
    turn_count: int | None = Field(default=None, ge=0)
    segment_count: int = Field(ge=0)
    runtime_ms: float = Field(ge=0)
    gap_to_reference_percent: float | None = Field(default=None, ge=0)
    geometry_valid: bool
    error_message: str | None = Field(default=None, min_length=1, max_length=512)


class SmallRadiusBenchmarkMethod(StrEnum):
    DUBINS = "dubins"
    HYBRID_ASTAR = "hybrid_astar"


class SmallRadiusBenchmarkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: list[CoverageBenchmarkCase] = Field(min_length=1, max_length=100)
    radii_m: list[float] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def radii_are_distinct_and_ascending(self) -> SmallRadiusBenchmarkRequest:
        if any(not isfinite(radius) or radius <= 0 or radius > 10_000 for radius in self.radii_m):
            raise ValueError("Радиусы benchmark должны быть в диапазоне (0, 10 000] м.")
        if self.radii_m != sorted(set(self.radii_m)):
            raise ValueError("Радиусы benchmark должны возрастать без повторений.")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Идентификаторы сценариев benchmark должны быть уникальны.")
        return self


class SmallRadiusBenchmarkCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    method: SmallRadiusBenchmarkMethod
    scenarios: list[TurnRadiusScenario] = Field(min_length=2, max_length=5)
    runtime_ms: float = Field(ge=0)


class SmallRadiusBenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_ids: list[str] = Field(min_length=1, max_length=100)
    radii_m: list[float] = Field(min_length=2, max_length=5)
    methods: list[SmallRadiusBenchmarkMethod] = Field(min_length=1, max_length=2)
    results: list[SmallRadiusBenchmarkCaseResult] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def contains_complete_result_matrix(self) -> SmallRadiusBenchmarkReport:
        expected = {(case_id, method) for case_id in self.case_ids for method in self.methods}
        observed = {(result.case_id, result.method) for result in self.results}
        if len(self.results) != len(expected) or observed != expected:
            raise ValueError("Отчёт benchmark должен содержать каждую пару сценария и метода.")
        if any(
            [scenario.radius_m for scenario in result.scenarios] != self.radii_m
            for result in self.results
        ):
            raise ValueError("Each experiment row must match the report radii.")
        return self


class UrbanObstacleOrderMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: Literal["original", "reordered_blocks"]
    radius_m: float = Field(gt=0, le=10_000)
    status: Literal["FEASIBLE", "NO_ROUTE_FOR_SELECTED_SWEEPS"]
    distance_km: float | None = Field(default=None, gt=0)
    uncovered_area_m2: float | None = Field(default=None, ge=0)
    selected_survey_order_changed: bool | None = None
    error: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def result_fields_match_status(self) -> UrbanObstacleOrderMeasurement:
        if self.status == "FEASIBLE":
            if self.distance_km is None or self.uncovered_area_m2 is None:
                raise ValueError("Для построенного маршрута нужны длина и площадь непокрытия.")
            if self.error is not None:
                raise ValueError("Для построенного маршрута ошибка должна отсутствовать.")
            return self
        if self.distance_km is not None or self.uncovered_area_m2 is not None:
            raise ValueError("Для отсутствующего маршрута нельзя указывать измерения.")
        return self


class UrbanObstacleOrderBenchmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    coverage_angle_degrees: float = Field(ge=0, lt=180)
    survey_leg_count: int = Field(ge=1, le=10_000)
    comparison: str = Field(min_length=1, max_length=256)
    planner: str = Field(min_length=1, max_length=256)
    results: list[UrbanObstacleOrderMeasurement] = Field(min_length=1, max_length=100)


class CoverageExperimentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(min_length=1, max_length=64)
    generated_at_utc: datetime
    scenario_ids: list[str] = Field(min_length=1, max_length=1_000)
    algorithms: list[SweepAngleAlgorithm] = Field(min_length=1, max_length=100)
    results: list[CoverageBenchmarkResult] = Field(min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def contains_complete_result_matrix(self) -> CoverageExperimentReport:
        expected = len(self.scenario_ids) * len(self.algorithms)
        if len(self.results) != expected:
            raise ValueError("The report must contain every scenario and algorithm pair.")
        pairs = {(result.case_id, result.algorithm) for result in self.results}
        expected_pairs = {
            (scenario_id, algorithm)
            for scenario_id in self.scenario_ids
            for algorithm in self.algorithms
        }
        if pairs != expected_pairs:
            raise ValueError("The report contains missing, duplicate, or unexpected result pairs.")
        return self


class CoverageExperimentArtifacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(min_length=1, max_length=64)
    directory: str = Field(min_length=1)
    csv_path: str = Field(min_length=1)
    json_path: str = Field(min_length=1)
    markdown_path: str = Field(min_length=1)


class GeoJsonFeature(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["Feature"]
    properties: GeoJsonProperties
    geometry: GeoJsonGeometry


class GeoJsonFeatureCollection(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["FeatureCollection"]
    features: list[GeoJsonFeature]


class KmlExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_name: str = Field(min_length=1)
    content: str = Field(min_length=1)


class QGroundControlSimpleItem(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["SimpleItem"]
    amsl_alt_above_terrain: float | None = Field(
        default=None,
        alias="AMSLAltAboveTerrain",
    )
    altitude: float = Field(default=0, alias="Altitude")
    altitude_mode: Literal[0, 4] = Field(default=0, alias="AltitudeMode")
    auto_continue: bool = Field(default=True, alias="autoContinue")
    command: Literal[16, 21, 22, 178, 206]
    do_jump_id: int = Field(ge=1, alias="doJumpId")
    frame: Literal[2, 3, 10]
    params: list[float | None] = Field(min_length=7, max_length=7)


class QGroundControlMission(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    cruise_speed: float = Field(gt=0, alias="cruiseSpeed")
    firmware_type: Literal[0, 3, 12] = Field(alias="firmwareType")
    global_plan_altitude_mode: Literal[1, 4] = Field(alias="globalPlanAltitudeMode")
    hover_speed: float = Field(gt=0, alias="hoverSpeed")
    items: list[QGroundControlSimpleItem]
    planned_home_position: list[float] = Field(
        min_length=3,
        max_length=3,
        alias="plannedHomePosition",
    )
    vehicle_type: Literal[1, 2, 13, 14, 22, 43] = Field(alias="vehicleType")
    version: Literal[2]


class QGroundControlRallyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    altitude: float
    latitude: float
    longitude: float


class QGroundControlEmptyCollection(BaseModel):
    model_config = ConfigDict(frozen=True)

    points: list[QGroundControlRallyPoint] = Field(default_factory=list)
    version: Literal[2]


class QGroundControlCircle(BaseModel):
    model_config = ConfigDict(frozen=True)

    center: list[float] = Field(min_length=2, max_length=2)
    inclusion: bool
    radius: float = Field(gt=0)


class QGroundControlPolygon(BaseModel):
    model_config = ConfigDict(frozen=True)

    inclusion: bool
    polygon: list[list[float]] = Field(min_length=3)


class QGroundControlGeoFence(BaseModel):
    model_config = ConfigDict(frozen=True)

    circles: list[QGroundControlCircle] = Field(default_factory=list)
    polygons: list[QGroundControlPolygon] = Field(default_factory=list)
    version: Literal[2]


class QGroundControlPlan(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    file_type: Literal["Plan"] = Field(alias="fileType")
    geo_fence: QGroundControlGeoFence = Field(alias="geoFence")
    ground_station: Literal["QGroundControl"] = Field(alias="groundStation")
    mission: QGroundControlMission
    rally_points: QGroundControlEmptyCollection = Field(alias="rallyPoints")
    version: Literal[1]


class QGroundControlExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_name: str = Field(min_length=1)
    content: str = Field(min_length=1)


class MissionPlannerExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_name: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str = Field(min_length=1)
    recommendations: list[FeasibilityRecommendation] = Field(default_factory=list, max_length=32)


class HealthStatus(StrEnum):
    HEALTHY = "healthy"


class HealthReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: HealthStatus
    message: str = Field(min_length=1)
