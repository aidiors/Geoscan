import pytest
from pydantic import ValidationError

from geoplan.coverage.models import CoverageArea
from geoplan.domain.airspace import NoFlyZone
from geoplan.navigation.curvature import (
    HybridAStarSweepTransitionPlanner,
    build_curvature_aware_sweep_route,
    build_curvature_aware_sweep_route_with_planner,
)
from geoplan.navigation.dubins import ExactDubinsPlanner
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.hybrid_astar import HybridAStarPlanner
from geoplan.navigation.models import SweepRouteCurvatureRequest
from geoplan.navigation.routing import (
    route_distance_km,
    route_is_clear_of_no_fly_zones,
    route_is_inside_coverage_area,
)


def test_curvature_aware_sweep_replaces_sharp_connectors_with_dubins_paths() -> None:
    projection = _projection()
    source_route = [
        projection.unproject(0, 0),
        projection.unproject(100, 0),
        projection.unproject(100, 20),
        projection.unproject(0, 20),
        projection.unproject(0, 40),
        projection.unproject(100, 40),
    ]
    request = SweepRouteCurvatureRequest(
        route=source_route,
        coverage_area=_coverage_area(projection),
        minimum_turn_radius_m=10,
        sampling_step_m=1,
    )

    result = build_curvature_aware_sweep_route(request)

    assert len(result.survey_legs) == 3
    assert len(result.transition_path_types) == 2
    assert result.distance_km > route_distance_km(source_route)
    assert result.transition_distance_km > 0.04
    assert all(
        route_is_inside_coverage_area(leg, request.coverage_area) for leg in result.survey_legs
    )
    assert result.route[0] == source_route[0]
    assert result.route[-1] == source_route[-1]


def test_larger_turn_radius_increases_curvature_aware_sweep_distance() -> None:
    projection = _projection()
    route = [
        projection.unproject(0, 0),
        projection.unproject(100, 0),
        projection.unproject(100, 30),
        projection.unproject(0, 30),
    ]
    area = _coverage_area(projection)

    tight = build_curvature_aware_sweep_route(
        SweepRouteCurvatureRequest(
            route=route,
            coverage_area=area,
            minimum_turn_radius_m=10,
        )
    )
    wide = build_curvature_aware_sweep_route(
        SweepRouteCurvatureRequest(
            route=route,
            coverage_area=area,
            minimum_turn_radius_m=30,
        )
    )

    assert wide.distance_km > tight.distance_km


def test_curvature_request_rejects_incomplete_survey_leg() -> None:
    projection = _projection()

    with pytest.raises(ValidationError, match="чётное число точек"):
        SweepRouteCurvatureRequest(
            route=[
                projection.unproject(0, 0),
                projection.unproject(100, 0),
                projection.unproject(100, 20),
            ],
            coverage_area=_coverage_area(projection),
            minimum_turn_radius_m=10,
        )


def test_hybrid_astar_connects_survey_legs_around_large_restriction() -> None:
    projection = _projection()
    obstacle = NoFlyZone(
        zone_id="central-block",
        coordinates=[
            projection.unproject(150, -120),
            projection.unproject(250, -120),
            projection.unproject(250, 120),
            projection.unproject(150, 120),
        ],
        safety_margin_m=10,
    )
    source_route = [
        projection.unproject(-50, 0),
        projection.unproject(0, 0),
        projection.unproject(400, 0),
        projection.unproject(450, 0),
    ]
    request = SweepRouteCurvatureRequest(
        route=source_route,
        coverage_area=CoverageArea(
            area_id="hybrid-curvature-test",
            name="Hybrid curvature test",
            coordinates=[
                projection.unproject(-100, -250),
                projection.unproject(500, -250),
                projection.unproject(500, 250),
                projection.unproject(-100, 250),
            ],
        ),
        no_fly_zones=[obstacle],
        minimum_turn_radius_m=30,
        sampling_step_m=3,
    )
    transition_planner = HybridAStarSweepTransitionPlanner(
        planner=HybridAStarPlanner(dubins_planner=ExactDubinsPlanner()),
        primitive_length_m=15,
        grid_resolution_m=8,
        heading_bin_degrees=15,
        analytic_expansion_distance_m=180,
        maximum_expansions=30_000,
    )

    result = build_curvature_aware_sweep_route_with_planner(request, transition_planner)

    assert route_is_clear_of_no_fly_zones(result.route, [obstacle])
    assert result.route[0] == source_route[0]
    assert result.route[-1] == source_route[-1]
    assert result.distance_km > 0.5


def _projection() -> LocalProjection:
    return LocalProjection(origin_longitude=37, origin_latitude=55)


def _coverage_area(projection: LocalProjection) -> CoverageArea:
    return CoverageArea(
        area_id="curvature-test",
        name="Curvature test",
        coordinates=[
            projection.unproject(-10, -10),
            projection.unproject(110, -10),
            projection.unproject(110, 50),
            projection.unproject(-10, 50),
        ],
    )
