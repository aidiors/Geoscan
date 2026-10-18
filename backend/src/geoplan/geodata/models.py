from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, Json, RootModel, model_validator

from geoplan.domain.airspace import RestrictionValidity
from geoplan.domain.enums import (
    AirspaceRestrictionType,
    AltitudeMode,
    AltitudeReference,
    ImportedGeometryType,
    ImportFormat,
    ZoneType,
)
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import MissionStage
from geoplan.planning.models import PlanResponse


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
    coverage_area_ids: list[str] = Field(default_factory=list)
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
    photo_capture_route_indices: list[int] | None = None


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
    command: Literal[16, 21, 22, 178, 206, 2000]
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
