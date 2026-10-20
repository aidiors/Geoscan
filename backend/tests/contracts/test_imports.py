import pytest

from geoplan.domain.enums import ErrorCode, ZoneType
from geoplan.errors import PlanningError
from geoplan.geodata.importers import import_geojson, import_kml
from geoplan.geodata.models import (
    GeoJsonImportRequest,
    GeoJsonMultiPolygonGeometry,
    GeoJsonPolygonGeometry,
    GeoJsonPosition,
    GeoJsonZoneFeature,
    GeoJsonZoneFeatureCollection,
    GeoJsonZoneProperties,
    KmlImportRequest,
)


def test_geojson_import_converts_polygon_to_work_area() -> None:
    content = GeoJsonZoneFeatureCollection(
        type="FeatureCollection",
        features=[
            GeoJsonZoneFeature(
                type="Feature",
                properties=GeoJsonZoneProperties(id="north", name="Северная зона"),
                geometry=GeoJsonPolygonGeometry(
                    type="Polygon",
                    coordinates=[
                        [
                            GeoJsonPosition(root=[37.4, 55.7]),
                            GeoJsonPosition(root=[37.41, 55.7]),
                            GeoJsonPosition(root=[37.41, 55.71]),
                            GeoJsonPosition(root=[37.4, 55.7]),
                        ]
                    ],
                ),
            )
        ],
    )
    request = GeoJsonImportRequest.model_validate(
        {
            "source_file_name": "work-area.geojson",
            "zone_type": ZoneType.WORK_AREA,
            "content": content.model_dump_json(),
        }
    )

    response = import_geojson(request)

    assert response.source_format == "geojson"
    assert response.zones[0].zone_id == "north"
    assert response.zones[0].coordinates[1].longitude == 37.41


def test_kml_import_converts_polygon_to_no_fly_zone() -> None:
    response = import_kml(
        KmlImportRequest(
            source_file_name="restriction.kml",
            zone_type=ZoneType.NO_FLY_ZONE,
            content=(
                '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>'
                "<name>Ограничение</name><Polygon><outerBoundaryIs><LinearRing>"
                "<coordinates>37.4,55.7,0 37.41,55.7,0 37.41,55.71,0 "
                "37.4,55.7,0</coordinates>"
                "</LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
            ),
        )
    )

    assert response.source_format == "kml"
    assert response.zones[0].zone_type == ZoneType.NO_FLY_ZONE
    assert response.zones[0].name == "Ограничение"


def test_geojson_import_preserves_allowed_airspace_zone_type() -> None:
    content = GeoJsonZoneFeatureCollection(
        type="FeatureCollection",
        features=[
            GeoJsonZoneFeature(
                type="Feature",
                properties=GeoJsonZoneProperties(name="Воздушный коридор"),
                geometry=GeoJsonPolygonGeometry(
                    type="Polygon",
                    coordinates=[
                        [
                            GeoJsonPosition(root=[37.4, 55.7]),
                            GeoJsonPosition(root=[37.41, 55.7]),
                            GeoJsonPosition(root=[37.4, 55.7]),
                        ]
                    ],
                ),
            )
        ],
    )
    request = GeoJsonImportRequest.model_validate(
        {
            "source_file_name": "airspace.geojson",
            "zone_type": ZoneType.ALLOWED_AIRSPACE,
            "content": content.model_dump_json(),
        }
    )

    response = import_geojson(request)

    assert response.zones[0].zone_type is ZoneType.ALLOWED_AIRSPACE


def test_geojson_import_splits_multipolygon_into_independent_zones() -> None:
    content = GeoJsonZoneFeatureCollection(
        type="FeatureCollection",
        features=[
            GeoJsonZoneFeature(
                type="Feature",
                properties=GeoJsonZoneProperties(id="district", name="Район"),
                geometry=GeoJsonMultiPolygonGeometry(
                    type="MultiPolygon",
                    coordinates=[
                        [
                            [
                                GeoJsonPosition(root=[37.4, 55.7]),
                                GeoJsonPosition(root=[37.41, 55.7]),
                                GeoJsonPosition(root=[37.4, 55.7]),
                            ]
                        ],
                        [
                            [
                                GeoJsonPosition(root=[37.42, 55.7]),
                                GeoJsonPosition(root=[37.43, 55.7]),
                                GeoJsonPosition(root=[37.42, 55.7]),
                            ]
                        ],
                    ],
                ),
            )
        ],
    )
    request = GeoJsonImportRequest.model_validate(
        {
            "source_file_name": "district.geojson",
            "zone_type": ZoneType.WORK_AREA,
            "content": content.model_dump_json(),
        }
    )

    response = import_geojson(request)

    assert [zone.zone_id for zone in response.zones] == ["district", "district-2"]
    assert response.zones[1].name == "Район - контур 2"


def test_kml_import_reads_placemark_from_folder() -> None:
    response = import_kml(
        KmlImportRequest(
            source_file_name="folder.kml",
            zone_type=ZoneType.WORK_AREA,
            content=(
                '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>'
                "<name>Север</name><Placemark><name>Контур</name><Polygon>"
                "<outerBoundaryIs><LinearRing><coordinates>37.4,55.7,0 "
                "37.41,55.7,0 37.4,55.7,0</coordinates></LinearRing>"
                "</outerBoundaryIs></Polygon></Placemark></Folder></Document></kml>"
            ),
        )
    )

    assert response.zones[0].name == "Север / Контур"


def test_kml_import_returns_stable_error_when_polygon_is_missing() -> None:
    request = KmlImportRequest(
        source_file_name="point.kml",
        zone_type=ZoneType.WORK_AREA,
        content="<kml><Placemark><name>Точка</name></Placemark></kml>",
    )

    with pytest.raises(PlanningError) as error:
        import_kml(request)

    assert error.value.code is ErrorCode.INVALID_IMPORT
    assert error.value.message == "KML не содержит поддерживаемой геометрии."


def test_kml_import_preserves_line_altitude_and_restriction_metadata() -> None:
    response = import_kml(
        KmlImportRequest(
            source_file_name="obstacles.kml",
            zone_type=ZoneType.OBSTACLE,
            content=(
                "<kml><Placemark><name>ЛЭП</name><ExtendedData>"
                '<Data name="Type"><value>врем_ограничение</value></Data>'
                '<Data name="Altitudes"><value>От земли до 120 м AGL</value></Data>'
                "</ExtendedData><LineString><altitudeMode>relativeToGround</altitudeMode>"
                "<coordinates>37.4,55.7,80 37.41,55.71,120</coordinates>"
                "</LineString></Placemark></kml>"
            ),
        )
    )

    obstacle = response.zones[0]
    assert obstacle.geometry_type == "line_string"
    assert obstacle.coordinates[1].altitude_m == 120
    assert obstacle.altitude_mode == "relativeToGround"
    assert obstacle.restriction_type == "temporary"
    assert obstacle.altitude_text == "От земли до 120 м AGL"


def test_kml_import_reads_explicit_altitude_and_time_contract() -> None:
    response = import_kml(
        KmlImportRequest(
            source_file_name="four-dimensional-restriction.kml",
            zone_type=ZoneType.NO_FLY_ZONE,
            content=(
                "<kml><Placemark><name>Временное ограничение</name><ExtendedData>"
                '<Data name="Type"><value>временное</value></Data>'
                '<Data name="minimum_altitude_m"><value>80</value></Data>'
                '<Data name="maximum_altitude_m"><value>160</value></Data>'
                '<Data name="altitude_reference"><value>msl</value></Data>'
                '<Data name="valid_from"><value>2026-09-27T07:00:00+03:00</value></Data>'
                '<Data name="valid_to"><value>2026-09-27T08:00:00+03:00</value></Data>'
                "</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                "37.4,55.7 37.41,55.7 37.41,55.71 37.4,55.7"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
            ),
        )
    )

    restriction = response.zones[0]
    assert restriction.minimum_altitude_m == 80
    assert restriction.maximum_altitude_m == 160
    assert restriction.altitude_reference == "msl"
    assert restriction.validity is not None
    assert restriction.validity.mode == "time_window"


def test_kml_import_rejects_numeric_altitudes_without_reference() -> None:
    request = KmlImportRequest(
        source_file_name="ambiguous-altitude.kml",
        zone_type=ZoneType.NO_FLY_ZONE,
        content=(
            "<kml><Placemark><ExtendedData>"
            '<Data name="minimum_altitude_m"><value>80</value></Data>'
            '<Data name="maximum_altitude_m"><value>160</value></Data>'
            "</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            "37.4,55.7 37.41,55.7 37.41,55.71 37.4,55.7"
            "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
        ),
    )

    with pytest.raises(PlanningError, match="altitude_reference"):
        import_kml(request)


def test_kml_import_rejects_partial_time_contract() -> None:
    request = KmlImportRequest(
        source_file_name="partial-window.kml",
        zone_type=ZoneType.NO_FLY_ZONE,
        content=(
            "<kml><Placemark><ExtendedData>"
            '<Data name="valid_from"><value>2026-09-27T07:00:00+03:00</value></Data>'
            "</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            "37.4,55.7 37.41,55.7 37.41,55.71 37.4,55.7"
            "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"
        ),
    )

    with pytest.raises(PlanningError) as error:
        import_kml(request)

    assert error.value.code is ErrorCode.INVALID_IMPORT
    assert error.value.message == "KML должен содержать начало и окончание периода действия."


def test_kml_import_preserves_polygon_interior_rings() -> None:
    response = import_kml(
        KmlImportRequest(
            source_file_name="area-with-hole.kml",
            zone_type=ZoneType.WORK_AREA,
            content=(
                "<kml><Placemark><Polygon>"
                "<outerBoundaryIs><LinearRing><coordinates>"
                "37.4,55.7 37.5,55.7 37.5,55.8 37.4,55.7"
                "</coordinates></LinearRing></outerBoundaryIs>"
                "<innerBoundaryIs><LinearRing><coordinates>"
                "37.43,55.73 37.44,55.73 37.44,55.74 37.43,55.73"
                "</coordinates></LinearRing></innerBoundaryIs>"
                "</Polygon></Placemark></kml>"
            ),
        )
    )

    assert len(response.zones[0].interior_rings) == 1
    assert response.zones[0].interior_rings[0][1].longitude == 37.44
