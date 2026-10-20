import json
from hashlib import sha256
from pathlib import Path

import pytest

from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import WorkSegment
from geoplan.domain.airspace import AllowedAirspace, NoFlyZone
from geoplan.domain.enums import ErrorCode, MissionStageType
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.navigation.geometry import projection_for_coordinates
from geoplan.navigation.routing import route_distance_km, route_is_inside_coverage_area
from geoplan.planning.candidates import _candidate_evaluation
from geoplan.planning.routes import _validate_geometry
from geoplan.planning.survey_adapter import MissionSurveyAdapter
from scenarios.customer import customer_polygons, customer_request


@pytest.fixture(scope="module")
def concave_customer_segment():
    root = Path(__file__).resolve().parents[3]
    frozen = json.loads(
        (root / "reference/benchmarks/customer-coverage-geometry-input.json").read_text(
            encoding="utf-8"
        )
    )
    assert sha256((root / frozen["source"]).read_bytes()).hexdigest() == frozen["source_sha256"]
    polygon = next(
        p for p in customer_polygons(root / frozen["source"]) if p.area.area_id == frozen["area_id"]
    )
    route = [Coordinate.model_validate(p) for p in frozen["route"]]
    segment = WorkSegment(
        segment_id="zone-316-2",
        route=route,
        distance_km=route_distance_km(route),
        coverage_area_id=polygon.area.area_id,
        strip_spacing_m=frozen["strip_spacing_m"],
    )
    return polygon.area, segment, customer_request([polygon])


def evaluate(segment, query, constraints=()):
    dependencies = create_default_dependencies()
    return _candidate_evaluation(
        segment,
        query.drones[0],
        query,
        dependencies.duration_calculator,
        dependencies.route_optimizer,
        dependencies.sweep_transition_planner,
        dependencies.vertical_profile_planner,
        list(constraints),
    )


def test_concave_customer_connector_is_transit_while_both_tracks_remain_inside(
    concave_customer_segment,
):
    area, segment, query = concave_customer_segment
    assert route_is_inside_coverage_area(segment.route[:2], area)
    assert route_is_inside_coverage_area(segment.route[2:], area)
    assert not route_is_inside_coverage_area(segment.route[1:3], area)
    candidate, error = evaluate(segment, query)
    assert error is None, error.message if error else ""
    assert candidate is not None
    survey = [s for s in candidate.mission.stages if s.stage_type is MissionStageType.SURVEY]
    transit = [s for s in candidate.mission.stages if s.stage_type is MissionStageType.TRANSIT]
    assert len(survey) == 2
    assert all(route_is_inside_coverage_area(s.coordinates, area) for s in survey)
    assert any(not route_is_inside_coverage_area(s.coordinates, area) for s in transit)


def test_customer_survey_track_outside_roi_is_still_rejected(concave_customer_segment):
    area, segment, query = concave_customer_segment
    projection = projection_for_coordinates(area.coordinates)
    outside = projection.unproject(100_000, 100_000)
    route = [outside, *segment.route[1:]]
    candidate, error = evaluate(
        segment.model_copy(update={"route": route, "distance_km": route_distance_km(route)}), query
    )
    assert candidate is None
    assert error is not None and error.code is ErrorCode.COVERAGE_GENERATION_FAILED


def test_customer_transit_must_remain_in_allowed_airspace(concave_customer_segment):
    area, segment, query = concave_customer_segment
    restricted = query.model_copy(
        update={
            "allowed_airspaces": [
                AllowedAirspace(
                    airspace_id="field-only",
                    coordinates=area.coordinates,
                    minimum_altitude_m=0,
                    maximum_altitude_m=500,
                )
            ]
        }
    )
    candidate, error = evaluate(segment, restricted)
    assert candidate is None
    assert error is not None and error.code is ErrorCode.OUTSIDE_ALLOWED_AIRSPACE


def test_customer_transit_must_not_cross_a_no_fly_zone(concave_customer_segment):
    area, segment, query = concave_customer_segment
    projection = projection_for_coordinates(area.coordinates)
    a, b = (projection.project(p) for p in segment.route[1:3])
    x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    zone = NoFlyZone(
        zone_id="connector-block",
        coordinates=[
            projection.unproject(x + dx, y + dy) for dx, dy in [(-5, -5), (5, -5), (5, 5), (-5, 5)]
        ],
        safety_margin_m=1,
    )
    dependencies = create_default_dependencies()
    prepared, physical = MissionSurveyAdapter(dependencies.sweep_transition_planner).prepare(
        segment, query.drones[0], query
    )
    with pytest.raises(PlanningError) as caught:
        _validate_geometry(prepared.route, prepared.route, [zone], prepared, physical)
    assert caught.value.code is ErrorCode.ROUTE_BLOCKED
