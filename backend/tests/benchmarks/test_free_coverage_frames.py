from dataclasses import asdict, replace

import pytest

from benchmarks.free_coverage_frames import retime
from benchmarks.free_coverage_overlap import analyze
from benchmarks.free_coverage_reference import candidates
from benchmarks.run_frame_coverage import route_candidates
from geoplan.routing.coverage_patterns import shell_routes, split_ring
from geoplan.routing.freeform import GeometryModel, Leg, Route, evaluate, evaluate_flights
from scenarios.freeform import cases


def model():
    return GeometryModel(next(case for case in cases() if case.case_id == "axis_aligned"))


def test_straight_retiming_preserves_geometry_clock_and_original_speed():
    physical = model()
    route = Route("straight", ((((-600.0, 0.0), (600.0, 0.0)),),))
    original, _ = evaluate(physical, route)
    result = retime(physical, route)
    assert result.flights is not None
    actual = evaluate_flights(physical, route.family, result.flights)
    assert actual["flight_minutes"] == pytest.approx(original["flight_minutes"])
    assert actual["physical_timing_feasible"]
    assert actual["images"] == result.frames_planned
    assert result.slowed_intervals == 0


def test_small_heading_change_gets_local_timing_without_resetting_camera():
    physical = model()
    route = Route("bend", ((((-600.0, 0.0), (0.0, 0.0), (590.0, 110.0)),),))
    original, _ = evaluate(physical, route)
    result = retime(physical, route)
    assert result.flights is not None
    actual = evaluate_flights(physical, route.family, result.flights)
    check = analyze(physical, [[asdict(leg) for leg in flight] for flight in result.flights])
    assert check["pair_overlap_passed"]
    assert actual["images"] == result.frames_planned == check["images_reconstructed"]
    assert actual["physical_timing_feasible"]
    assert actual["flight_minutes"] >= original["flight_minutes"] - 1e-7
    assert actual["flight_minutes"] < original["flight_minutes"] + 0.05


def test_impossible_sharp_heading_change_does_not_get_a_fake_camera_break():
    physical = model()
    route = Route("sharp", ((((-600.0, 0.0), (0.0, 0.0), (0.0, 200.0)),),))
    result = retime(physical, route)
    assert result.flights is None
    assert result.reason == "no_progress_with_required_frame_overlap"


def test_explicit_timing_cannot_accelerate_past_physical_limits():
    physical = model()
    leg = Leg((-600, 0), (600, 0), 0.01, 1.2, "survey")
    result = evaluate_flights(physical, "too_fast", ((leg,),))
    assert not result["physical_timing_feasible"]
    assert not result["screen_passed"]


def test_camera_clock_survives_short_transfers_between_runs():
    physical = model()
    route = Route("runs", ((((-100.0, 0.0), (100.0, 0.0)), ((100.0, 5.0), (-100.0, 5.0))),))
    result = retime(physical, route)
    assert result.flights is not None
    actual = evaluate_flights(physical, "runs", result.flights)
    check = analyze(physical, [[asdict(leg) for leg in flight] for flight in result.flights])
    assert actual["images"] == check["images_reconstructed"] == result.frames_planned
    assert check["pair_overlap_passed"]


def test_retimed_resource_overrun_is_rejected():
    physical = model()
    limited = GeometryModel(replace(physical.case, endurance_minutes=0.5))
    route = Route("long", ((((-600.0, 0.0), (600.0, 0.0)),),))
    result = retime(limited, route)
    assert result.flights is not None
    metrics = evaluate_flights(limited, route.family, result.flights)
    assert not metrics["battery_feasible"]
    assert not metrics["screen_passed"]


def test_explicit_flights_cannot_teleport_or_omit_the_base_transfer():
    physical = model()
    route = Route("straight", ((((-600.0, 0.0), (600.0, 0.0)),),))
    _, flights = evaluate(physical, route)
    incomplete = tuple(leg for leg in flights[0] if leg.phase == "survey")
    result = evaluate_flights(physical, "missing_transfers", (incomplete,))
    assert not result["trajectory_continuous"]
    assert not result["screen_passed"]


def test_expanding_search_does_not_drop_previous_route_alternatives():
    physical = GeometryModel(next(case for case in cases() if case.case_id == "concave_l"))
    previous = candidates(physical)
    expanded = route_candidates(physical)
    assert set(previous).issubset(expanded)
    assert any(route.family == "boundary_return_cross_transit" for route in previous)


@pytest.mark.parametrize("landing", [(120, 80), (0, 0), (200, 0)])
def test_ring_partition_preserves_every_service_edge_without_repetition(landing):
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    ring = ((0, 0), (200, 0), (200, 100), (0, 100), (0, 0))
    arcs = split_ring(ring, landing)
    original = LineString(ring)
    parts = [LineString(arc) for arc in arcs]
    assert sum(part.length for part in parts) == pytest.approx(original.length)
    assert original.difference(unary_union(parts)).length < 1e-8
    assert arcs[0][-1] == arcs[1][0]
    assert arcs[0][0] == arcs[1][-1] == ring[0]


def test_split_shells_preserve_the_closed_shell_service_geometry():
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    physical = model()
    closed = shell_routes(physical, 3, 0)[0]
    split = shell_routes(physical, 3, 0, split=True)[0]

    def lines(route):
        return [LineString(stroke) for stroke in route.sorties[0]]

    before, after = lines(closed), lines(split)
    assert sum(line.length for line in before) == pytest.approx(sum(leg.length for leg in after))
    assert unary_union(before).hausdorff_distance(unary_union(after)) < 1e-8


def test_holdout_dataset_is_distinct_and_respects_requested_camera_interval():
    from scenarios.freeform import dataset

    holdout = dataset("holdout", 1)
    reference = dataset("reference", 2)
    assert not {c.case_id for c in holdout} & {c.case_id for c in reference}
    assert len(holdout) == 6
    assert sum(c.home != c.landing for c in holdout) == 3
    for case in holdout:
        assert case.request.parameters.sensor.minimum_trigger_interval_s == 1
        assert GeometryModel(case).target.is_valid
