import pytest
from pydantic import ValidationError

from src.routing import coordinate_distance_km, route_is_clear_of_no_fly_zones
from src.schemas import (
    Coordinate,
    CoverageArea,
    NoFlyZone,
    OptimalityStatus,
    SectionRouteOptimizationRequest,
    SectionRouteVariant,
)
from src.section_optimizer import DubinsSectionTransitionPlanner, ExactSectionRouteOptimizer


def test_exact_section_optimizer_selects_order_and_orientation_with_shortest_transit() -> None:
    request = SectionRouteOptimizationRequest(
        variant_groups=[
            [
                _variant("section-b", "b-backward", 0.004, 0.003),
                _variant("section-b", "b-forward", 0.003, 0.004),
            ],
            [
                _variant("section-a", "a-forward", 0.001, 0.002),
                _variant("section-a", "a-backward", 0.002, 0.001),
            ],
        ],
        coverage_area=_coverage_area(),
        departure_candidates=[_coordinate(0)],
        landing_candidates=[_coordinate(0.005)],
    )

    result = ExactSectionRouteOptimizer().optimize(request)

    assert [selection.variant_id for selection in result.selections] == [
        "a-forward",
        "b-forward",
    ]
    assert result.departure == _coordinate(0)
    assert result.landing == _coordinate(0.005)
    assert result.optimality is OptimalityStatus.PROVEN_OPTIMAL
    assert result.turn_count == 0
    expected_distance = coordinate_distance_km(_coordinate(0), _coordinate(0.005))
    assert result.distance_km == pytest.approx(expected_distance, rel=1e-6)


def test_exact_section_optimizer_builds_safe_transition_around_no_fly_zone() -> None:
    restriction = NoFlyZone(
        zone_id="central-block",
        coordinates=[
            Coordinate(longitude=0.002, latitude=54.9997),
            Coordinate(longitude=0.003, latitude=54.9997),
            Coordinate(longitude=0.003, latitude=55.0003),
            Coordinate(longitude=0.002, latitude=55.0003),
        ],
        safety_margin_m=5,
    )
    request = SectionRouteOptimizationRequest(
        variant_groups=[
            [_variant("section-a", "a", 0, 0.001)],
            [_variant("section-b", "b", 0.004, 0.005)],
        ],
        coverage_area=_coverage_area(),
        no_fly_zones=[restriction],
    )

    result = ExactSectionRouteOptimizer().optimize(request)

    assert route_is_clear_of_no_fly_zones(result.route, [restriction])
    direct_distance = coordinate_distance_km(_coordinate(0), _coordinate(0.005))
    assert result.distance_km > direct_distance


def test_dubins_section_transition_accounts_for_entry_and_exit_headings() -> None:
    request = SectionRouteOptimizationRequest(
        variant_groups=[
            [_variant("section-a", "eastbound", 0, 0.001)],
            [_variant("section-b", "westbound", 0.003, 0.002)],
        ],
        coverage_area=_coverage_area(),
    )

    straight = ExactSectionRouteOptimizer().optimize(request)
    curvature_aware = ExactSectionRouteOptimizer(
        transition_planner=DubinsSectionTransitionPlanner(
            minimum_turn_radius_m=20,
            sampling_step_m=2,
        )
    ).optimize(request)

    assert curvature_aware.distance_km > straight.distance_km
    assert curvature_aware.route[0] in [
        request.variant_groups[0][0].route[0],
        request.variant_groups[1][0].route[0],
    ]
    assert curvature_aware.optimality is OptimalityStatus.PROVEN_OPTIMAL


def test_section_route_request_rejects_duplicate_section_groups() -> None:
    with pytest.raises(ValidationError, match="Идентификаторы секций должны быть уникальными"):
        SectionRouteOptimizationRequest(
            variant_groups=[
                [_variant("section-a", "first", 0, 0.001)],
                [_variant("section-a", "second", 0.002, 0.003)],
            ],
            coverage_area=_coverage_area(),
        )


def _variant(
    section_id: str,
    variant_id: str,
    start_longitude: float,
    end_longitude: float,
) -> SectionRouteVariant:
    return SectionRouteVariant(
        section_id=section_id,
        variant_id=variant_id,
        route=[_coordinate(start_longitude), _coordinate(end_longitude)],
    )


def _coordinate(longitude: float) -> Coordinate:
    return Coordinate(longitude=longitude, latitude=55)


def _coverage_area() -> CoverageArea:
    return CoverageArea(
        area_id="benchmark-area",
        name="Benchmark area",
        coordinates=[
            Coordinate(longitude=-0.001, latitude=54.998),
            Coordinate(longitude=0.007, latitude=54.998),
            Coordinate(longitude=0.007, latitude=55.002),
            Coordinate(longitude=-0.001, latitude=55.002),
        ],
    )
