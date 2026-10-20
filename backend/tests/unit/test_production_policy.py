from math import hypot, inf
from pathlib import Path
from random import Random

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from geoplan import budget
from geoplan.coverage.models import CoveragePlanRequest
from geoplan.coverage.production import ProductionCoveragePlanner
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationMode, SweepAngleAlgorithm, ZoneType
from geoplan.domain.geo import Coordinate
from geoplan.geodata.importers import import_kml
from geoplan.geodata.models import KmlImportRequest
from geoplan.navigation.routing import (
    PreparedSafeRoutes,
    _shortest_visibility_path,
    _visibility_vertices,
    build_safe_route,
    route_is_clear_of_no_fly_zones,
)
from geoplan.planning.models import PlanRequest
from scenarios.customer import customer_polygons, customer_request
from scenarios.planning import productive_survey_request


@pytest.mark.parametrize("seed", range(6))
def test_visibility_astar_matches_independent_all_pairs_shortest_path(seed):
    random = Random(seed)
    polygons = [
        box(5 + i * 8, random.uniform(-4, -1), 8 + i * 8, random.uniform(1, 4)) for i in range(4)
    ]
    blocked = unary_union(polygons)
    nodes = [(-2.0, 0.0), (40.0, 0.0), *_visibility_vertices(polygons)]
    distances = [[0.0 if a == b else inf for b in range(len(nodes))] for a in range(len(nodes))]
    for a, start in enumerate(nodes):
        for b, end in enumerate(nodes):
            if a != b and LineString([start, end]).relate(blocked)[0] == "F":
                distances[a][b] = hypot(end[0] - start[0], end[1] - start[1])
    for middle in range(len(nodes)):
        for a in range(len(nodes)):
            for b in range(len(nodes)):
                distances[a][b] = min(distances[a][b], distances[a][middle] + distances[middle][b])
    for cache in (None, {}):
        path = _shortest_visibility_path(nodes, blocked, cache)
        assert path is not None
        assert LineString(path).length == pytest.approx(distances[0][1], abs=1e-8)
        assert LineString(path).relate(blocked)[0] == "F"


def test_nested_compute_budgets_cannot_extend_parent_and_do_not_leak(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(budget, "monotonic", lambda: now[0])
    with budget.planning_budget(10):
        now[0] = 9
        with budget.planning_budget(100):
            assert budget.improvement_allowed(0.5)
            now[0] = 11
            with pytest.raises(budget.PlanningTimeExceeded):
                budget.check_planning_budget()
    budget.check_planning_budget()


def test_shared_geometry_does_not_reuse_another_query_endpoint_altitude():
    points = [
        Coordinate(longitude=37, latitude=55, altitude_m=100),
        Coordinate(longitude=37.01, latitude=55, altitude_m=100),
    ]
    zone = NoFlyZone(
        zone_id="far",
        coordinates=[
            Coordinate(longitude=x, latitude=y)
            for x, y in ((38, 56), (38.01, 56), (38.01, 56.01), (38, 56.01))
        ],
    )
    context = PreparedSafeRoutes.create(points, [zone])
    context.build(points)
    higher = [p.model_copy(update={"altitude_m": 120}) for p in points]
    assert context.build(higher) == higher


def test_import_preserves_duplicate_objects_and_diagnoses_zero_area():
    source = """<kml><Document>
    <Placemark id="same"><Polygon><outerBoundaryIs><LinearRing><coordinates>
    37,55 37.01,55 37.01,55.01 37,55
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark id="same"><Polygon><outerBoundaryIs><LinearRing><coordinates>
    38,55 38.01,55 38.01,55.01 38,55
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark id="invalid"><Polygon><outerBoundaryIs><LinearRing><coordinates>
    37,55 37,55 37,55 37,55
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    </Document></kml>"""
    result = import_kml(
        KmlImportRequest(
            source_file_name="customer.kml", zone_type=ZoneType.WORK_AREA, content=source
        )
    )
    assert len(result.zones) == len({z.zone_id for z in result.zones}) == 3
    assert result.zones[1].metadata["source_zone_id"] == "same"
    assert "geometry_issue" in result.zones[2].metadata


def test_long_area_identifiers_preserve_unique_sortie_suffixes():
    request = productive_survey_request()
    assert request.survey_parameters is not None
    results = []
    for suffix in ("a", "b"):
        area = request.coverage_areas[0].model_copy(update={"area_id": "x" * 63 + suffix})
        result = ProductionCoveragePlanner().plan(
            CoveragePlanRequest(
                area=area,
                mission_altitude_m=request.mission_altitude_m,
                parameters=request.survey_parameters,
                planning_area_count=2,
            )
        )
        results.extend(result.segments)
    assert len(results) > 2
    assert len({s.segment_id for s in results}) == len(results)
    assert all(len(s.segment_id) <= 64 for s in results)


def test_duplicate_work_area_identifiers_are_rejected():
    request = productive_survey_request()
    with pytest.raises(ValidationError, match="уникальными"):
        PlanRequest.model_validate(
            {**request.model_dump(), "coverage_areas": request.coverage_areas * 2}
        )


def test_production_policy_preserves_manual_heading_for_batch_and_fast():
    request = productive_survey_request()
    assert request.survey_parameters is not None
    for mode in OptimizationMode:
        result = ProductionCoveragePlanner().plan(
            CoveragePlanRequest(
                area=request.coverage_areas[0],
                parameters=request.survey_parameters,
                mission_altitude_m=request.mission_altitude_m,
                planning_area_count=32,
                optimization_mode=mode,
            )
        )
        assert result.angle_algorithm is SweepAngleAlgorithm.FIXED_INPUT
        assert all(s.flight_direction_degrees == 90 for s in result.segments)


def test_real_concave_hole_does_not_disconnect_reachable_landing():
    source = Path(__file__).resolve().parents[3] / "reference/hackathon/Границы полетов.kml"
    polygon = next(p for p in customer_polygons(source) if p.area.area_id == "zone-690")
    request = customer_request([polygon])
    assert request.departure_site is not None
    zones = [
        NoFlyZone(zone_id=f"hole-{i}", coordinates=ring, safety_margin_m=0.5)
        for i, ring in enumerate(polygon.area.interior_rings)
    ]
    route = [
        Coordinate(longitude=38.80901938077901, latitude=55.0595324698353),
        request.departure_site.coordinate,
    ]
    safe = build_safe_route(route, zones)
    assert len(safe) > 2
    assert route_is_clear_of_no_fly_zones(safe, zones)
