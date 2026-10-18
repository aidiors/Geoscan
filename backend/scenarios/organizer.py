"""An explicitly selected real work polygon; aircraft/site/weather are demo inputs."""

from pathlib import Path

from geoplan.coverage.models import CoverageArea
from geoplan.domain.enums import ZoneType
from geoplan.geodata.importers import import_kml
from geoplan.geodata.models import KmlImportRequest
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates
from geoplan.planning.models import PlanRequest
from scenarios.planning import productive_survey_request


def organizer_request(source: Path, zone_id: str = "1932") -> tuple[PlanRequest, dict]:
    imported = import_kml(
        KmlImportRequest(
            source_file_name=source.name,
            zone_type=ZoneType.WORK_AREA,
            content=source.read_text(encoding="utf-8-sig"),
        )
    )
    zone = next(z for z in imported.zones if z.zone_id == zone_id)
    projection = projection_for_coordinates(zone.coordinates)
    geometry = polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection)
    source_request = productive_survey_request()
    assert source_request.departure_site is not None
    assert source_request.survey_parameters is not None
    # An illustrative operator-selected base, not an automatically approved site.
    minx, miny, _, _ = geometry.bounds
    base = source_request.departure_site.model_copy(
        update={"coordinate": projection.unproject(minx - 300, miny - 300)}
    )
    request = PlanRequest.model_validate(
        {
            **source_request.model_dump(),
            "coverage_areas": [
                CoverageArea(
                    area_id=zone.zone_id,
                    name=zone.name,
                    coordinates=zone.coordinates,
                    interior_rings=zone.interior_rings,
                )
            ],
            "departure_site": base,
            "landing_site": base,
            "survey_parameters": source_request.survey_parameters.model_copy(
                update={"maximum_segment_distance_km": 2}
            ),
        }
    )
    return request, {
        "source_file": source.name,
        "imported_zone_count": len(imported.zones),
        "selected_zone_id": zone.zone_id,
        "selected_zone_name": zone.name,
        "selected_area_km2": geometry.area / 1e6,
        "assumptions": [
            "Геометрия выбранной рабочей области взята без упрощения из KML заказчика.",
            "Площадка, парк, камера, погода и высота являются демонстрационными вводными.",
            "Это выбранная область, не полный расчёт всех 696 областей.",
            "Высотные препятствия и московские ограничения здесь не подключены; "
            "прохождение этого сценария не подтверждает проверку полного набора данных заказчика.",
        ],
    }
