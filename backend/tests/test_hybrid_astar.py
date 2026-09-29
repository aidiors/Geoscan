import pytest
from pydantic import ValidationError

from src.dubins import ExactDubinsPlanner
from src.geometry import LocalProjection
from src.hybrid_astar import HybridAStarPlanner
from src.routing import route_bearing_degrees, route_is_clear_of_no_fly_zones
from src.schemas import DubinsPose, HybridAStarPathRequest, NoFlyZone


def test_hybrid_astar_finds_curvature_constrained_route_around_large_obstacle() -> None:
    projection = LocalProjection(origin_longitude=37, origin_latitude=55)
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
    request = HybridAStarPathRequest(
        start=DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90),
        end=DubinsPose(coordinate=projection.unproject(400, 0), heading_degrees=90),
        no_fly_zones=[obstacle],
        minimum_turn_radius_m=30,
        primitive_length_m=15,
        sampling_step_m=3,
        grid_resolution_m=8,
        heading_bin_degrees=15,
        analytic_expansion_distance_m=180,
        maximum_expansions=30_000,
    )

    result = HybridAStarPlanner(dubins_planner=ExactDubinsPlanner()).plan(request)

    assert result.expanded_states > 0
    assert result.route[0] == request.start.coordinate
    assert result.route[-1] == request.end.coordinate
    assert route_is_clear_of_no_fly_zones(result.route, [obstacle])
    assert result.distance_km > 0.4
    assert _heading_difference(route_bearing_degrees(result.route[-2], result.route[-1]), 90) < 3


def test_hybrid_astar_uses_exact_dubins_connection_when_route_is_clear() -> None:
    projection = LocalProjection(origin_longitude=37, origin_latitude=55)
    request = HybridAStarPathRequest(
        start=DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90),
        end=DubinsPose(coordinate=projection.unproject(100, 0), heading_degrees=90),
        minimum_turn_radius_m=20,
    )

    result = HybridAStarPlanner(dubins_planner=ExactDubinsPlanner()).plan(request)

    assert result.expanded_states == 0
    assert result.distance_km == pytest.approx(0.1, rel=2e-3)


def test_hybrid_astar_request_rejects_identical_poses() -> None:
    projection = LocalProjection(origin_longitude=37, origin_latitude=55)
    pose = DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90)

    with pytest.raises(ValidationError, match="должны различаться"):
        HybridAStarPathRequest(
            start=pose,
            end=pose,
            minimum_turn_radius_m=20,
        )


def _heading_difference(first: float, second: float) -> float:
    difference = abs(first - second)
    return min(difference, 360 - difference)
