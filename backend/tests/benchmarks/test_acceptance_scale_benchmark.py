import pytest
from shapely.geometry import Polygon

from benchmarks.acceptance_scale import EXPECTED_AREA_M2, FLEET_SIZE, build_scenarios
from geoplan.domain.enums import AltitudeReference
from geoplan.navigation.geometry import LocalProjection


def test_acceptance_matrix_has_three_100_km2_scenarios_and_ten_aircraft() -> None:
    scenarios = build_scenarios()

    assert [scenario.name for scenario in scenarios] == [
        "flat_territory",
        "urban_constraints",
        "terrain_and_time_window",
    ]
    for scenario in scenarios:
        assert len(scenario.request.drones) == FLEET_SIZE
        area = scenario.request.coverage_areas[0]
        projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
        measured = Polygon([projection.project(point) for point in area.coordinates]).area
        assert measured == pytest.approx(EXPECTED_AREA_M2, abs=0.01)


def test_complex_scenario_exercises_geotiff_uncertainty_and_time_window() -> None:
    request = build_scenarios()[2].request

    assert request.altitude_reference is AltitudeReference.AGL
    assert request.terrain_source is not None
    assert request.terrain_source.source_type == "geotiff"
    assert request.terrain_vertical_uncertainty_m > 0
    assert request.allowed_airspaces[0].validity is not None
