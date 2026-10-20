from dataclasses import replace
from math import cos, radians, sin

import pytest
from shapely.affinity import rotate
from shapely.geometry import box
from shapely.ops import unary_union

from geoplan.planning.lateral_overlap import SurveyStrip, lateral_overlaps


def test_disconnected_targets_do_not_require_photographic_overlap_across_empty_space():
    strips = [
        SurveyStrip(("a", "one"), (0, 0), (100, 0), 100, 0),
        SurveyStrip(("a", "two"), (0, 400), (100, 400), 100, 1),
    ]
    assert lateral_overlaps(strips, 60) == ([], 0, [])


def test_connected_u_shape_does_not_require_overlap_across_its_empty_bay():
    area = unary_union([box(0, -20, 1100, 20), box(0, 380, 1100, 420), box(1000, 0, 1100, 400)])
    strips = [
        SurveyStrip(("a", "one"), (0, 0), (900, 0), 100),
        SurveyStrip(("a", "two"), (0, 400), (900, 400), 100),
    ]
    assert lateral_overlaps(strips, 60, survey_target=area) == ([], 0, [])


def test_a_hole_does_not_hide_real_bad_overlap_in_the_remaining_continuous_part():
    area = box(0, -20, 1000, 100).difference(box(0, 20, 500, 60))
    strips = [
        SurveyStrip(("a", "one"), (0, 0), (1000, 0), 100),
        SurveyStrip(("a", "two"), (0, 80), (1000, 80), 100),
    ]
    values, _, problems = lateral_overlaps(strips, 60, survey_target=area)
    assert min(values) == pytest.approx(20)
    assert problems
    assert all(p.bounds[0] >= 500 - 1e-7 for p in problems)


@pytest.mark.parametrize("angle", [0, 35, 90])
@pytest.mark.parametrize("reverse", [False, True])
def test_short_strip_does_not_hide_a_weaker_neighbor_after_its_endpoint(angle, reverse):
    strips = [
        SurveyStrip(("uav", "a"), (0, 0), (1000, 0), 100),
        SurveyStrip(("uav", "b"), (0, 40), (500, 40), 100),
        SurveyStrip(("uav", "c"), (400, 80), (1000, 80), 100),
    ]

    def transform(point):
        x, y = point
        theta = radians(angle)
        return x * cos(theta) - y * sin(theta), x * sin(theta) + y * cos(theta)

    strips = [
        replace(
            s,
            start=transform(s.end if reverse else s.start),
            end=transform(s.start if reverse else s.end),
        )
        for s in strips
    ]
    overlaps, cross, problems = lateral_overlaps(strips, 60)
    assert min(overlaps) == pytest.approx(20)
    assert cross == 3
    assert problems
    aligned = [rotate(p, -angle, origin=(0, 0)) for p in problems]
    assert any(p.bounds[0] >= 500 - 1e-6 and p.bounds[2] >= 999 for p in aligned)


def test_a_complete_intermediate_strip_still_hides_the_distant_strip():
    strips = [
        SurveyStrip(("uav", str(i)), (0, y), (1000, y), 100) for i, y in enumerate((0, 40, 80))
    ]
    overlaps, cross, problems = lateral_overlaps(strips, 60)
    assert min(overlaps) == pytest.approx(60)
    assert cross == 2
    assert not problems
