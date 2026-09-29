import pytest
from pydantic import ValidationError

from src.dubins import ExactDubinsPlanner
from src.geometry import LocalProjection
from src.routing import route_bearing_degrees, route_distance_km
from src.schemas import Coordinate, DubinsPathRequest, DubinsPathType, DubinsPose


def test_dubins_planner_returns_straight_path_for_aligned_poses() -> None:
    projection = _projection()
    request = DubinsPathRequest(
        start=DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90),
        end=DubinsPose(coordinate=projection.unproject(100, 0), heading_degrees=90),
        minimum_turn_radius_m=20,
        sampling_step_m=5,
    )

    result = ExactDubinsPlanner().plan(request)

    assert result.path_type is DubinsPathType.LEFT_STRAIGHT_LEFT
    assert result.distance_km == pytest.approx(0.1, rel=1e-6)
    assert result.route[0] == request.start.coordinate
    assert result.route[-1] == request.end.coordinate
    assert route_distance_km(result.route) == pytest.approx(result.distance_km, rel=2e-3)
    assert route_bearing_degrees(result.route[0], result.route[1]) == pytest.approx(90, abs=0.1)


def test_dubins_planner_accounts_for_opposite_final_heading() -> None:
    projection = _projection()
    request = DubinsPathRequest(
        start=DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90),
        end=DubinsPose(coordinate=projection.unproject(100, 0), heading_degrees=270),
        minimum_turn_radius_m=25,
        sampling_step_m=2,
    )

    result = ExactDubinsPlanner().plan(request)

    assert result.distance_km > 0.1
    assert route_distance_km(result.route) == pytest.approx(result.distance_km, rel=2e-3)
    final_bearing = route_bearing_degrees(result.route[-2], result.route[-1])
    assert _heading_difference(final_bearing, 270) < 3


def test_dubins_path_becomes_longer_with_larger_turn_radius() -> None:
    projection = _projection()
    start = DubinsPose(coordinate=projection.unproject(0, 0), heading_degrees=90)
    end = DubinsPose(coordinate=projection.unproject(100, 40), heading_degrees=270)

    tight = ExactDubinsPlanner().plan(
        DubinsPathRequest(start=start, end=end, minimum_turn_radius_m=10)
    )
    wide = ExactDubinsPlanner().plan(
        DubinsPathRequest(start=start, end=end, minimum_turn_radius_m=40)
    )

    assert wide.distance_km > tight.distance_km


@pytest.mark.parametrize(
    ("start_heading", "end_heading", "expected_type"),
    [
        (0, 0, DubinsPathType.LEFT_STRAIGHT_LEFT),
        (0, 90, DubinsPathType.LEFT_RIGHT_LEFT),
        (0, 180, DubinsPathType.RIGHT_LEFT_RIGHT),
        (0, 225, DubinsPathType.RIGHT_STRAIGHT_RIGHT),
        (45, 90, DubinsPathType.RIGHT_STRAIGHT_LEFT),
        (45, 225, DubinsPathType.LEFT_STRAIGHT_RIGHT),
    ],
)
def test_dubins_planner_evaluates_all_six_path_families(
    start_heading: float,
    end_heading: float,
    expected_type: DubinsPathType,
) -> None:
    projection = _projection()
    request = DubinsPathRequest(
        start=DubinsPose(
            coordinate=projection.unproject(0, 0),
            heading_degrees=start_heading,
        ),
        end=DubinsPose(
            coordinate=projection.unproject(40, 0),
            heading_degrees=end_heading,
        ),
        minimum_turn_radius_m=20,
        sampling_step_m=1,
    )

    result = ExactDubinsPlanner().plan(request)

    assert result.path_type is expected_type
    assert result.route[0] == request.start.coordinate
    assert result.route[-1] == request.end.coordinate
    assert route_distance_km(result.route) == pytest.approx(result.distance_km, rel=3e-3)


def test_dubins_request_rejects_identical_poses() -> None:
    pose = DubinsPose(coordinate=Coordinate(longitude=37, latitude=55), heading_degrees=0)

    with pytest.raises(ValidationError, match="должны различаться"):
        DubinsPathRequest(start=pose, end=pose, minimum_turn_radius_m=20)


def _projection() -> LocalProjection:
    return LocalProjection(origin_longitude=37, origin_latitude=55)


def _heading_difference(first: float, second: float) -> float:
    difference = abs(first - second)
    return min(difference, 360 - difference)
