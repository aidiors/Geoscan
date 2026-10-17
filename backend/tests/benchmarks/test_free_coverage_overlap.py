from benchmarks.free_coverage_overlap import analyze
from geoplan.routing.freeform import GeometryModel, image_footprint
from scenarios.freeform import cases


def test_rectangle_rotation_can_lose_overlap_without_any_camera_translation():
    first = image_footprint((0, 0), (0, 0), (1, 0), 150, 100)
    second = image_footprint((0, 0), (0, 0), (0, 1), 150, 100)
    overlap = first.intersection(second).area / first.area * 100
    assert 66 < overlap < 67
    assert overlap < 75


def test_straight_flight_respects_the_requested_overlap_and_camera_off_breaks_pairs():
    case = next(case for case in cases() if case.case_id == "axis_aligned")
    model = GeometryModel(case)
    leg = {"start": (-100, 0), "end": (100, 0), "minutes": 200 / 750, "phase": "survey"}
    result = analyze(model, [[leg]])
    assert result["pair_overlap_passed"]
    assert result["pairs_below_requested_overlap"] == 0
    reverse = {**leg, "start": (100, 0), "end": (-100, 0)}
    off = {"start": (100, 0), "end": (100, 0), "minutes": 1, "phase": "connection"}
    separated = analyze(model, [[leg, off, reverse]])
    assert separated["pair_overlap_passed"]
    assert separated["adjacent_camera_on_pairs"] == 2 * result["adjacent_camera_on_pairs"]
