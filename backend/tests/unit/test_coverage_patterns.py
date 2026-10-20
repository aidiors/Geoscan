from dataclasses import replace

import pytest
from shapely.affinity import rotate
from shapely.geometry import Polygon

from geoplan.domain.environment import WindCondition
from geoplan.navigation.geometry import LocalProjection
from geoplan.routing.coverage_patterns import coverage_angles, shell_routes
from geoplan.routing.freeform import GeometryModel, evaluate, route_legs
from geoplan.routing.pattern_pool import ShellCoveragePatternGenerator
from scenarios.freeform import dataset


def rotated_model(angle: float, wind: WindCondition | None = None) -> GeometryModel:
    case = dataset("holdout", 1)[0]
    projection = LocalProjection(37.62, 55.75)
    shape = rotate(Polygon([(-1000, -400), (1000, -400), (1000, 400), (-1000, 400)]), angle)
    area = case.request.area.model_copy(
        update={
            "coordinates": [projection.unproject(x, y) for x, y in list(shape.exterior.coords)[:-1]]
        }
    )
    request = case.request.model_copy(update={"area": area, "wind": wind})
    return GeometryModel(
        replace(case, request=request, home=(-1500, -1500), landing=(-1500, -1500))
    )


@pytest.mark.parametrize("angle", [17, 35, 73, 127])
def test_shell_directions_follow_rotated_area_and_preserve_original_axes(angle):
    model = rotated_model(angle)
    headings = coverage_angles(model)
    assert headings[:2] == (0, 90)
    assert any(abs(heading - angle) < 1e-4 for heading in headings)
    assert any(abs(heading - ((angle + 90) % 180)) < 1e-4 for heading in headings)
    assert len(headings) <= 6


def test_wind_compass_bearing_is_converted_to_cartesian_shell_angle():
    headings = coverage_angles(rotated_model(35, WindCondition(speed_mps=5, direction_degrees=30)))
    assert 60 in headings
    assert 150 in headings
    assert len(headings) == 6


def test_circular_area_has_no_tessellation_dependent_major_axis():
    original = dataset("holdout", 1)[0]
    model = GeometryModel(replace(original, home=(0, 0), landing=(0, 0)))
    assert coverage_angles(model) == (0, 90)


def test_distinct_sites_add_their_axis_when_the_shape_has_no_major_axis():
    original = dataset("holdout", 1)[0]
    model = GeometryModel(replace(original, home=(-1000, -1000), landing=(1000, 1000)))
    assert coverage_angles(model) == (0, 90, 45, 135)


def test_geometry_aligned_shell_reduces_connecting_distance_on_rotated_area():
    model = rotated_model(35)

    def shortest(angles):
        routes = [route for angle in angles for route in shell_routes(model, 3, angle)]
        return min(
            routes, key=lambda route: sum(leg.minutes for leg in route_legs(model, route)[0])
        )

    old = shortest((0, 90))
    aligned = shortest(coverage_angles(model))
    old_cost = sum(leg.minutes for leg in route_legs(model, old)[0])
    new_cost = sum(leg.minutes for leg in route_legs(model, aligned)[0])
    assert new_cost < old_cost
    assert model.case.request.parameters.side_overlap_percent == 60
    assert model.case.request.parameters.forward_overlap_percent == 75
    # A cheaper drawing must still satisfy the finite-frame and spacing screen.
    result, _ = evaluate(model, aligned)
    assert result["screen_passed"]


def test_expanded_production_pool_keeps_every_original_shell_candidate():
    model = rotated_model(35)
    generator = ShellCoveragePatternGenerator()
    original = {pattern.route for pattern in generator.generate(model)}
    additional = {pattern.route for pattern in generator.generate(model, additional_only=True)}
    assert additional
    assert original.isdisjoint(additional)
    expanded = original | additional
    for layers in (1, 3, 5, 8, 9):
        for angle in (0, 90):
            for split in (False, True):
                assert set(shell_routes(model, layers, angle, split=split)).issubset(expanded)
