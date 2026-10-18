from dataclasses import dataclass
from math import isfinite
from xml.etree.ElementTree import Element, ParseError, fromstring

from pydantic import ValidationError

from geoplan.domain.airspace import RestrictionValidity
from geoplan.domain.enums import (
    AirspaceRestrictionType,
    AltitudeMode,
    AltitudeReference,
    ErrorCode,
    ImportedGeometryType,
    ImportFormat,
    ValidityMode,
    ZoneType,
)
from geoplan.domain.geo import Coordinate
from geoplan.domain.identifiers import derived_id
from geoplan.errors import PlanningError
from geoplan.geodata.models import (
    GeoJsonImportRequest,
    GeoJsonMultiPolygonGeometry,
    GeoJsonPolygonGeometry,
    GeoJsonPosition,
    GeoJsonZoneFeature,
    ImportedZone,
    ImportResponse,
    KmlImportRequest,
)
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates


@dataclass(frozen=True)
class KmlPlacemark:
    element: Element
    folder_path: tuple[str, ...]


@dataclass(frozen=True)
class KmlGeometry:
    geometry_type: ImportedGeometryType
    coordinates: list[Coordinate]
    interior_rings: list[list[Coordinate]]
    altitude_mode: AltitudeMode


def import_geojson(request: GeoJsonImportRequest) -> ImportResponse:
    zones = [
        zone
        for index, feature in enumerate(request.content.features)
        for zone in _zones_from_geojson_feature(request, feature, index)
    ]
    return ImportResponse(
        source_file_name=request.source_file_name,
        source_format=ImportFormat.GEOJSON,
        zones=_prepare_zones(zones),
    )


def import_kml(request: KmlImportRequest) -> ImportResponse:
    document = _parse_kml_document(request.content)
    placemarks = _find_kml_placemarks(document, ())
    zones = [
        zone
        for placemark_index, placemark in enumerate(placemarks)
        for zone in _zones_from_kml_placemark(request, placemark, placemark_index)
    ]
    if not zones:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML не содержит поддерживаемой геометрии.",
        )
    return ImportResponse(
        source_file_name=request.source_file_name,
        source_format=ImportFormat.KML,
        zones=_prepare_zones(zones),
    )


def _prepare_zones(zones: list[ImportedZone]) -> list[ImportedZone]:
    """Preserve every object, diagnose unusable work geometry and disambiguate source IDs."""
    reserved = {zone.zone_id for zone in zones}
    used: set[str] = set()
    result = []
    for index, zone in enumerate(zones):
        metadata = dict(zone.metadata)
        identifier = zone.zone_id
        if identifier in used:
            metadata["source_zone_id"] = identifier
            suffix = index + 1
            identifier = derived_id(zone.zone_id, f"-object-{suffix}")
            while identifier in reserved or identifier in used:
                suffix += 1
                identifier = derived_id(zone.zone_id, f"-object-{suffix}")
        if (
            zone.zone_type is ZoneType.WORK_AREA
            and zone.geometry_type is ImportedGeometryType.POLYGON
        ):
            try:
                geometry = polygon_from_coordinates(
                    zone.coordinates,
                    zone.interior_rings,
                    projection_for_coordinates(zone.coordinates),
                )
                if geometry.area <= 0:
                    metadata["geometry_issue"] = (
                        "Область не имеет площади; исправьте или удалите её."
                    )
                elif geometry.area < 1:
                    metadata["geometry_warning"] = (
                        "Площадь меньше 1 м²; проверьте исходную геометрию."
                    )
            except ValueError:
                metadata["geometry_issue"] = (
                    "Область не образует полигон; исправьте или удалите её."
                )
        used.add(identifier)
        result.append(zone.model_copy(update={"zone_id": identifier, "metadata": metadata}))
    return result


def _zones_from_geojson_feature(
    request: GeoJsonImportRequest,
    feature: GeoJsonZoneFeature,
    index: int,
) -> list[ImportedZone]:
    polygons = _geojson_polygons(feature.geometry)
    zone_id = _zone_id(feature.properties.id, index)
    zone_name = feature.properties.name or f"Зона {index + 1}"
    return [
        _zone_from_geojson_polygon(
            request,
            polygon,
            zone_id,
            zone_name,
            polygon_index,
            feature,
        )
        for polygon_index, polygon in enumerate(polygons)
    ]


def _geojson_polygons(
    geometry: GeoJsonPolygonGeometry | GeoJsonMultiPolygonGeometry,
) -> list[list[list[GeoJsonPosition]]]:
    if isinstance(geometry, GeoJsonPolygonGeometry):
        return [geometry.coordinates]
    return geometry.coordinates


def _zone_from_geojson_polygon(
    request: GeoJsonImportRequest,
    rings: list[list[GeoJsonPosition]],
    zone_id: str,
    zone_name: str,
    polygon_index: int,
    feature: GeoJsonZoneFeature,
) -> ImportedZone:
    coordinates = [_coordinate_from_geojson_position(position.root) for position in rings[0]]
    _validate_coordinates(coordinates, ImportedGeometryType.POLYGON)
    interior_rings = [
        [_coordinate_from_geojson_position(position.root) for position in ring]
        for ring in rings[1:]
    ]
    suffix = "" if polygon_index == 0 else f"-{polygon_index + 1}"
    name_suffix = "" if polygon_index == 0 else f" — контур {polygon_index + 1}"
    return ImportedZone(
        zone_id=derived_id(zone_id, suffix),
        name=f"{zone_name}{name_suffix}",
        zone_type=request.zone_type,
        geometry_type=ImportedGeometryType.POLYGON,
        coordinates=coordinates,
        interior_rings=interior_rings,
        altitude_mode=feature.properties.altitude_mode,
        altitude_text=feature.properties.altitude_text,
        minimum_altitude_m=feature.properties.minimum_altitude_m,
        maximum_altitude_m=feature.properties.maximum_altitude_m,
        altitude_reference=feature.properties.altitude_reference,
        restriction_type=feature.properties.restriction_type,
        validity=feature.properties.validity,
    )


def _zones_from_kml_placemark(
    request: KmlImportRequest,
    placemark: KmlPlacemark,
    index: int,
) -> list[ImportedZone]:
    geometries = _kml_geometries(placemark.element)
    metadata = _kml_metadata(placemark.element)
    name = placemark.element.findtext("{*}name") or metadata.get("Name") or f"Объект {index + 1}"
    full_name = _kml_zone_name(placemark.folder_path, name)
    base_id = placemark.element.get("id") or metadata.get("FID") or f"zone-{index + 1}"
    restriction_type = _restriction_type(metadata.get("Type"))
    validity = _restriction_validity(metadata, restriction_type)
    minimum_altitude_m, maximum_altitude_m = _altitude_range(metadata)
    altitude_text = metadata.get("Altitudes") or placemark.element.findtext("{*}description")
    return [
        ImportedZone(
            zone_id=_geometry_id(base_id, geometry_index),
            name=_geometry_name(full_name, geometry_index, len(geometries)),
            zone_type=request.zone_type,
            geometry_type=geometry.geometry_type,
            coordinates=geometry.coordinates,
            interior_rings=geometry.interior_rings,
            altitude_mode=geometry.altitude_mode,
            restriction_type=restriction_type,
            altitude_text=altitude_text,
            minimum_altitude_m=minimum_altitude_m,
            maximum_altitude_m=maximum_altitude_m,
            altitude_reference=_altitude_reference(
                metadata,
                geometry.altitude_mode,
                minimum_altitude_m is not None,
            ),
            validity=validity,
            metadata=metadata,
        )
        for geometry_index, geometry in enumerate(geometries)
    ]


def _kml_geometries(placemark: Element) -> list[KmlGeometry]:
    geometries: list[KmlGeometry] = []
    for polygon in placemark.findall(".//{*}Polygon"):
        outer_node = polygon.find("./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
        if outer_node is None or outer_node.text is None:
            continue
        coordinates = _valid_kml_coordinates(
            outer_node.text,
            ImportedGeometryType.POLYGON,
        )
        if coordinates is None:
            continue
        interior_rings: list[list[Coordinate]] = []
        for node in polygon.findall("./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"):
            if node.text is None:
                continue
            ring = _valid_kml_coordinates(node.text, ImportedGeometryType.POLYGON)
            if ring is not None:
                interior_rings.append(ring)
        geometries.append(
            KmlGeometry(
                geometry_type=ImportedGeometryType.POLYGON,
                coordinates=coordinates,
                interior_rings=interior_rings,
                altitude_mode=_altitude_mode(polygon),
            )
        )
    for line in placemark.findall(".//{*}LineString"):
        coordinates_node = line.find("./{*}coordinates")
        if coordinates_node is None or coordinates_node.text is None:
            continue
        coordinates = _valid_kml_coordinates(
            coordinates_node.text,
            ImportedGeometryType.LINE_STRING,
        )
        if coordinates is None:
            continue
        geometries.append(
            KmlGeometry(
                geometry_type=ImportedGeometryType.LINE_STRING,
                coordinates=coordinates,
                interior_rings=[],
                altitude_mode=_altitude_mode(line),
            )
        )
    for point in placemark.findall(".//{*}Point"):
        coordinates_node = point.find("./{*}coordinates")
        if coordinates_node is None or coordinates_node.text is None:
            continue
        coordinates = _valid_kml_coordinates(
            coordinates_node.text,
            ImportedGeometryType.POINT,
        )
        if coordinates is None:
            continue
        geometries.append(
            KmlGeometry(
                geometry_type=ImportedGeometryType.POINT,
                coordinates=coordinates,
                interior_rings=[],
                altitude_mode=_altitude_mode(point),
            )
        )
    return geometries


def _kml_metadata(placemark: Element) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for data in placemark.findall(".//{*}ExtendedData/{*}Data"):
        name = data.get("name")
        value = data.findtext("{*}value")
        if name and value:
            metadata[name] = value.strip()
    for data in placemark.findall(".//{*}ExtendedData/{*}SchemaData/{*}SimpleData"):
        name = data.get("name")
        if name and data.text:
            metadata[name] = data.text.strip()
    return metadata


def _restriction_type(value: str | None) -> AirspaceRestrictionType | None:
    if value is None:
        return None
    normalized = value.casefold()
    if "врем" in normalized:
        return AirspaceRestrictionType.TEMPORARY
    if "пост" in normalized:
        return AirspaceRestrictionType.PERMANENT
    if "запрет" in normalized:
        return AirspaceRestrictionType.PROHIBITED
    if normalized == "svo":
        return AirspaceRestrictionType.SPECIAL
    return AirspaceRestrictionType.UNKNOWN


def _restriction_validity(
    metadata: dict[str, str],
    restriction_type: AirspaceRestrictionType | None,
) -> RestrictionValidity | None:
    valid_from = metadata.get("valid_from")
    valid_to = metadata.get("valid_to")
    if (valid_from is None) != (valid_to is None):
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML должен содержать начало и окончание периода действия.",
        )
    if valid_from is not None and valid_to is not None:
        try:
            return RestrictionValidity.model_validate(
                {
                    "mode": ValidityMode.TIME_WINDOW,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                }
            )
        except ValidationError as exc:
            raise PlanningError(
                code=ErrorCode.INVALID_IMPORT,
                message="KML содержит некорректный период действия ограничения.",
            ) from exc
    if restriction_type in {
        AirspaceRestrictionType.PERMANENT,
        AirspaceRestrictionType.PROHIBITED,
    }:
        return RestrictionValidity(mode=ValidityMode.PERMANENT)
    return None


def _altitude_range(metadata: dict[str, str]) -> tuple[float | None, float | None]:
    minimum = metadata.get("minimum_altitude_m")
    maximum = metadata.get("maximum_altitude_m")
    if minimum is None and maximum is None:
        return None, None
    if minimum is None or maximum is None:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML должен содержать нижнюю и верхнюю числовые границы высоты.",
        )
    try:
        parsed_minimum = float(minimum)
        parsed_maximum = float(maximum)
    except ValueError as exc:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML содержит некорректную числовую границу высоты.",
        ) from exc
    if (
        not isfinite(parsed_minimum)
        or not isfinite(parsed_maximum)
        or parsed_maximum <= parsed_minimum
    ):
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="Верхняя граница высоты KML должна быть выше нижней.",
        )
    return parsed_minimum, parsed_maximum


def _altitude_mode(geometry: Element) -> AltitudeMode:
    value = geometry.findtext("./{*}altitudeMode")
    if value is None:
        return AltitudeMode.UNKNOWN
    try:
        return AltitudeMode(value.strip())
    except ValueError:
        return AltitudeMode.UNKNOWN


def _altitude_reference(
    metadata: dict[str, str],
    altitude_mode: AltitudeMode,
    has_numeric_range: bool,
) -> AltitudeReference | None:
    value = metadata.get("altitude_reference")
    if value is not None:
        try:
            return AltitudeReference(value.casefold())
        except ValueError as exc:
            raise PlanningError(
                code=ErrorCode.INVALID_IMPORT,
                message="KML содержит неизвестную систему отсчёта высоты; используйте AGL или MSL.",
            ) from exc
    if altitude_mode is AltitudeMode.ABSOLUTE:
        return AltitudeReference.MSL
    if altitude_mode is AltitudeMode.RELATIVE_TO_GROUND:
        return AltitudeReference.AGL
    if has_numeric_range:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message=(
                "Числовой диапазон высоты KML требует altitude_reference=agl/msl "
                "или однозначный altitudeMode."
            ),
        )
    return None


def _geometry_id(base_id: str, geometry_index: int) -> str:
    if geometry_index == 0:
        return derived_id(base_id, "")
    suffix = f"-{geometry_index + 1}"
    return derived_id(base_id, suffix)


def _geometry_name(name: str, geometry_index: int, geometry_count: int) -> str:
    if geometry_count == 1:
        return name[:128]
    suffix = f" — геометрия {geometry_index + 1}"
    return f"{name[: 128 - len(suffix)]}{suffix}"


def _parse_kml_document(content: str) -> Element:
    try:
        return fromstring(content)
    except ParseError as exc:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML содержит некорректный XML.",
        ) from exc


def _find_kml_placemarks(element: Element, folder_path: tuple[str, ...]) -> list[KmlPlacemark]:
    placemarks: list[KmlPlacemark] = []
    for child in element:
        local_name = _local_name(child.tag)
        if local_name == "Placemark":
            placemarks.append(KmlPlacemark(element=child, folder_path=folder_path))
        if local_name == "Folder":
            folder_name = child.findtext("{*}name") or "Папка"
            placemarks.extend(_find_kml_placemarks(child, (*folder_path, folder_name)))
        if local_name == "Document":
            placemarks.extend(_find_kml_placemarks(child, folder_path))
    return placemarks


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _kml_zone_name(folder_path: tuple[str, ...], name: str) -> str:
    if not folder_path:
        return name
    return f"{' / '.join(folder_path)} / {name}"


def _coordinates_from_kml_text(
    content: str,
    geometry_type: ImportedGeometryType,
) -> list[Coordinate]:
    coordinates = [_coordinate_from_kml_group(group) for group in content.split()]
    _validate_coordinates(coordinates, geometry_type)
    return coordinates


def _valid_kml_coordinates(
    content: str,
    geometry_type: ImportedGeometryType,
) -> list[Coordinate] | None:
    try:
        return _coordinates_from_kml_text(content, geometry_type)
    except PlanningError:
        return None


def _coordinate_from_kml_group(group: str) -> Coordinate:
    values = group.split(",")
    if len(values) < 2:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="KML содержит координату без широты или долготы.",
        )
    longitude = _parse_coordinate_value(values[0])
    latitude = _parse_coordinate_value(values[1])
    altitude = _parse_coordinate_value(values[2]) if len(values) >= 3 and values[2] else None
    return _create_coordinate(longitude, latitude, altitude)


def _coordinate_from_geojson_position(position: list[float]) -> Coordinate:
    altitude = position[2] if len(position) == 3 else None
    return _create_coordinate(position[0], position[1], altitude)


def _parse_coordinate_value(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="Файл содержит некорректное числовое значение координаты.",
        ) from exc


def _create_coordinate(
    longitude: float,
    latitude: float,
    altitude: float | None,
) -> Coordinate:
    try:
        return Coordinate(longitude=longitude, latitude=latitude, altitude_m=altitude)
    except ValidationError as exc:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message="Координата находится вне допустимого диапазона.",
        ) from exc


def _validate_coordinates(
    coordinates: list[Coordinate],
    geometry_type: ImportedGeometryType,
) -> None:
    minimum = {
        ImportedGeometryType.POINT: 1,
        ImportedGeometryType.LINE_STRING: 2,
        ImportedGeometryType.POLYGON: 3,
    }[geometry_type]
    if len(coordinates) < minimum:
        raise PlanningError(
            code=ErrorCode.INVALID_IMPORT,
            message=f"Геометрия {geometry_type.value} содержит недостаточно координат.",
        )


def _zone_id(value: str | int | None, index: int) -> str:
    if value is None:
        return f"zone-{index + 1}"
    return str(value)[:64]
