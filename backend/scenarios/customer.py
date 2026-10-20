"""Unmodified customer polygons; fleet, weather and bases are explicitly synthetic."""

from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point

from geoplan.coverage.models import CoverageArea
from geoplan.domain.enums import SweepDirectionMode, ZoneType
from geoplan.geodata.importers import import_kml
from geoplan.geodata.models import KmlImportRequest
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates
from geoplan.planning.models import PlanRequest
from scenarios.planning import productive_survey_request


@dataclass(frozen=True)
class CustomerPolygon:
    area: CoverageArea
    area_km2: float
    longitude: float
    latitude: float


def customer_polygons(source: Path) -> list[CustomerPolygon]:
    imported = import_kml(
        KmlImportRequest(
            source_file_name=source.name,
            zone_type=ZoneType.WORK_AREA,
            content=source.read_text(encoding="utf-8-sig"),
        )
    )
    result = []
    for zone in imported.zones:
        projection = projection_for_coordinates(zone.coordinates)
        geometry = polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection)
        result.append(
            CustomerPolygon(
                CoverageArea(
                    area_id=zone.zone_id,
                    name=zone.name,
                    coordinates=zone.coordinates,
                    interior_rings=zone.interior_rings,
                ),
                geometry.area / 1e6,
                projection.origin_longitude,
                projection.origin_latitude,
            )
        )
    return result


def stratified_polygons(polygons: list[CustomerPolygon], count: int) -> list[CustomerPolygon]:
    """Even area quantiles, including both extremes; no observed winner selection."""
    ordered = sorted(
        (p for p in polygons if p.area_km2 >= 1e-6), key=lambda p: (p.area_km2, p.area.area_id)
    )
    count = min(count, len(ordered))
    return [ordered[round(i * (len(ordered) - 1) / max(1, count - 1))] for i in range(count)]


def local_batch(
    polygons: list[CustomerPolygon], seed: CustomerPolygon, count: int
) -> list[CustomerPolygon]:
    return sorted(
        (p for p in polygons if p.area_km2 >= 1e-6),
        key=lambda p: (
            ((p.longitude - seed.longitude) * 0.57) ** 2 + (p.latitude - seed.latitude) ** 2,
            p.area.area_id,
        ),
    )[:count]


def customer_request(polygons: list[CustomerPolygon], **overrides) -> PlanRequest:
    base = productive_survey_request()
    assert base.departure_site is not None and base.survey_parameters is not None
    coordinates = [c for p in polygons for c in p.area.coordinates]
    projection = projection_for_coordinates(coordinates)
    # Synthetic central base avoids pretending all regional objects share an approved site.
    holes = [
        polygon_from_coordinates(ring, [], projection).buffer(1)
        for p in polygons
        for ring in p.area.interior_rings
    ]
    candidates = sorted(
        ((x, y) for x in (0, -300, 300, -1000, 1000) for y in (0, -300, 300, -1000, 1000)),
        key=lambda xy: (xy[0] ** 2 + xy[1] ** 2, xy),
    )
    xy = next((xy for xy in candidates if all(not h.covers(Point(xy)) for h in holes)), None)
    if xy is None:
        raise ValueError("No synthetic base outside polygon holes; configure a real site")
    site = base.departure_site.model_copy(update={"coordinate": projection.unproject(*xy)})
    fleet = [
        base.drones[0].model_copy(
            update={
                "drone_id": f"demo-{i}",
                "name": f"Demo {i}",
                "cruise_speed_kmh": 70 + 5 * i,
                "maximum_flight_minutes": 75 + 5 * i,
                "minimum_turnaround_minutes": 3,
            }
        )
        for i in range(4)
    ]
    return PlanRequest.model_validate(
        {
            **base.model_dump(),
            "coverage_areas": [p.area for p in polygons],
            "drones": fleet,
            "maximum_active_drones": 4,
            "departure_site": site,
            "landing_site": site,
            "flight_site_occupancy_seconds": 10,
            "survey_parameters": base.survey_parameters.model_copy(
                update={
                    "direction_mode": SweepDirectionMode.AUTOMATIC,
                    "maximum_segment_distance_km": 8,
                    "sensor": base.survey_parameters.sensor.model_copy(
                        update={"minimum_trigger_interval_s": 1}
                    ),
                }
            ),
            **overrides,
        }
    )
