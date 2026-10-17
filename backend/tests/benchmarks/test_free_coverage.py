"""Negative controls for the independent free-geometry experiment screen."""

from dataclasses import replace
from itertools import product

import pytest

from benchmarks.free_coverage_reference import boundary_return_routes, production_reference
from geoplan.routing.freeform import (
    GeometryModel,
    Route,
    anchor_contours,
    contour_strokes,
    evaluate,
    oriented_order,
)
from scenarios.freeform import cases


@pytest.fixture
def model():
    return GeometryModel(next(case for case in cases() if case.case_id == "axis_aligned"))


def test_actual_images_do_not_hide_insufficient_interior_spacing(model):
    strokes = anchor_contours(model, contour_strokes(model))
    raw = Route("contours", (strokes,), True)
    result, _ = evaluate(model, raw)
    assert result["image_coverage_percent"] >= 99.99
    assert result["interior_spacing_coverage_percent"] < 99.9
    assert not result["screen_passed"]


def test_repeated_survey_adds_time_without_extra_coverage(model):
    reference = production_reference(model)
    original, _ = evaluate(model, reference)
    flat = tuple(stroke for sortie in reference.sorties for stroke in sortie)
    repeated, _ = evaluate(model, Route("repeated", ((*flat, *flat),)))
    assert original["screen_passed"]
    assert repeated["image_coverage_percent"] == pytest.approx(original["image_coverage_percent"])
    assert repeated["flight_minutes"] > original["flight_minutes"] * 1.9
    assert (
        repeated["effective_width_inefficiency_percent"]
        > original["effective_width_inefficiency_percent"] + 20
    )
    assert (
        repeated["nonproductive_survey_proxy_minutes"]
        > original["nonproductive_survey_proxy_minutes"] + 10
    )


def test_battery_is_checked_even_for_complete_image_coverage(model):
    route = production_reference(model)
    tight = GeometryModel(replace(model.case, endurance_minutes=5))
    result, _ = evaluate(tight, route)
    assert result["image_coverage_percent"] >= 99.99
    assert not result["battery_feasible"]
    assert not result["screen_passed"]


def test_camera_clock_does_not_restart_at_each_short_leg(model):
    # One hundred collinear edges are shorter than the trigger spacing. A camera
    # reset at every edge would fabricate many impossible additional images.
    stroke = tuple((-600 + i * 12, 0) for i in range(101))
    result, _ = evaluate(model, Route("subdivided", ((stroke,),)))
    assert result["trigger_feasible"]
    assert result["images"] <= result["survey_minutes"] / model.interval_minutes + 1.00001
    assert result["images"] < 100


def test_redundancy_diagnostic_is_independent_of_polyline_subdivision(model):
    coarse = ((-600.0, 0.0), (600.0, 0.0), (-600.0, 0.0))
    fine = (
        *((-600.0 + i * 12, 0.0) for i in range(101)),
        *((600.0 - i * 12, 0.0) for i in range(1, 101)),
    )
    a, _ = evaluate(model, Route("coarse", ((coarse,),)))
    b, _ = evaluate(model, Route("fine", ((fine,),)))
    for key in ("effective_width_inefficiency_percent", "nonproductive_time_proxy_minutes"):
        assert a[key] == pytest.approx(b[key], rel=1e-8, abs=1e-8)


def test_survey_cannot_cut_through_an_obstacle():
    model = GeometryModel(next(case for case in cases() if case.case_id == "obstacle"))
    min_x, min_y, max_x, max_y = model.target.bounds
    y = (min_y + max_y) / 2
    result, _ = evaluate(model, Route("unsafe", ((((min_x, y), (max_x, y)),),)))
    assert not result["safe_geometry"]
    assert not result["screen_passed"]


def test_orientation_dp_uses_actual_landing_and_agrees_with_brute_force():
    case = next(case for case in cases() if case.case_id == "different_landing")
    model = GeometryModel(case)
    strokes = (
        ((-500.0, -100.0), (500.0, -100.0)),
        ((-500.0, 0.0), (500.0, 0.0)),
        ((-500.0, 100.0), (500.0, 100.0)),
    )

    def cost(order):
        previous = case.home
        total = 0.0
        for stroke in order:
            total += model.travel_minutes(previous, stroke[0]) + model.path_minutes(stroke)
            previous = stroke[-1]
        return total + model.travel_minutes(previous, case.landing)

    expected = min(
        cost(
            tuple(tuple(reversed(s)) if flag else s for s, flag in zip(strokes, flags, strict=True))
        )
        for flags in product((False, True), repeat=len(strokes))
    )
    actual = oriented_order(model, strokes)
    assert cost(actual) == pytest.approx(expected)
    result, _ = evaluate(model, Route("sites", (actual,)))
    assert result["return_minutes"] == pytest.approx(
        model.travel_minutes(actual[-1][-1], case.landing)
    )


def test_an_empty_survey_cannot_pass(model):
    result, _ = evaluate(model, Route("empty", ()))
    assert not result["screen_passed"]
    assert result["image_coverage_percent"] == 0


def test_boundary_return_reduces_empty_flight_but_can_lose_on_total_time(model):
    reference = production_reference(model)
    original, _ = evaluate(model, reference)
    mixed = next(r for r in boundary_return_routes(model) if r.family == "boundary_return")
    result, _ = evaluate(model, mixed)
    assert result["screen_passed"]
    assert result["deadhead_minutes"] < original["deadhead_minutes"]
    assert result["flight_minutes"] > original["flight_minutes"]


def test_mixed_geometry_closes_near_launch_and_covers_rotated_area():
    case = next(case for case in cases() if case.case_id == "rotated_35")
    model = GeometryModel(case)
    original, _ = evaluate(model, production_reference(model))
    mixed = next(r for r in boundary_return_routes(model) if r.family == "boundary_return")
    result, _ = evaluate(model, mixed)
    assert result["screen_passed"]
    assert result["image_coverage_percent"] >= 99.99
    assert result["interior_spacing_coverage_percent"] >= 99.9
    assert result["deadhead_minutes"] < original["deadhead_minutes"]
    assert mixed.sorties[0][0][0] == mixed.sorties[0][-1][-1]
    assert result["return_minutes"] == pytest.approx(result["departure_minutes"])
