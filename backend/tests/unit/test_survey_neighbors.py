from dataclasses import replace
from math import cos, radians, sin

import pytest
from shapely.affinity import rotate

from geoplan.planning.lateral_overlap import SurveyStrip, lateral_overlaps


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
