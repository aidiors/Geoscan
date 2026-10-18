import pytest

from geoplan.routing.freeform import GeometryModel, Leg, Route
from geoplan.routing.pattern_pool import (
    CoverageCandidatePool,
    CoveragePattern,
    DiverseCoverageCandidateSelector,
    PatternFamily,
    RankedCoverageCandidate,
)


def candidate(name, minutes, family=PatternFamily.SHELL, heading=0, layers=1):
    return RankedCoverageCandidate(
        CoveragePattern(Route(name, ()), family, heading, layers),
        (Leg((0, 0), (100, 0), minutes, 0.1, "survey"),),
    )


def test_diversity_adds_different_geometry_after_preserving_cheapest_prefix():
    repeated = [candidate(f"same-{i}", i + 1) for i in range(6)]
    different = candidate("different-angle", 7, heading=35)
    contour = candidate("contour", 8, family=PatternFamily.CONTOURS)
    pool = [*repeated, different, contour]
    selected = DiverseCoverageCandidateSelector(prefix_size=2, maximum_candidates=4).select(pool)
    assert selected[:2] == tuple(repeated[:2])
    assert selected[2:] == (different, contour)


def test_a_valid_candidate_below_the_old_cutoff_can_reach_detailed_validation():
    # Screening can reject all six shorter drawings. Selection must retain a
    # genuinely different seventh geometry without discarding the old six.
    prefix = [candidate(f"incomplete-{i}", i + 1) for i in range(6)]
    complete = candidate("complete", 7, layers=3)
    selected = DiverseCoverageCandidateSelector(maximum_candidates=7).select([*prefix, complete])
    assert selected[:6] == tuple(prefix)
    assert next(item for item in selected if item.pattern.route.family == "complete") == complete


def test_selection_is_bounded_deterministic_and_independent_of_input_order():
    pool = [candidate(f"route-{i}", i + 1, layers=i % 4) for i in range(30)]
    selector = DiverseCoverageCandidateSelector(maximum_candidates=12)
    assert len(selector.select(pool)) == 12
    assert selector.select(pool) == selector.select(list(reversed(pool)))


def test_equal_costs_are_broken_by_stable_route_identifier():
    a, b = candidate("a", 1), candidate("b", 1)
    assert DiverseCoverageCandidateSelector().select([b, a]) == (a, b)


def test_small_pool_is_not_padded_with_duplicates():
    pool = [candidate("only", 1)]
    assert DiverseCoverageCandidateSelector().select(pool) == tuple(pool)
    assert DiverseCoverageCandidateSelector().select([]) == ()


@pytest.mark.parametrize("prefix,limit", [(0, 6), (6, 5), (-1, 6)])
def test_invalid_search_budgets_are_rejected(prefix, limit):
    with pytest.raises(ValueError, match="budget"):
        DiverseCoverageCandidateSelector(prefix_size=prefix, maximum_candidates=limit)


def test_one_unreachable_candidate_does_not_discard_other_routes(monkeypatch):
    import geoplan.routing.pattern_pool as pools

    broken = candidate("unreachable", 1).pattern
    valid = candidate("valid", 2)

    class Generator:
        def generate(self, model, *, additional_only=False):
            return (broken, valid.pattern)

    def physical_legs(model, route):
        if route.family == "unreachable":
            raise ValueError("No safe connection")
        return (valid.legs,)

    monkeypatch.setattr(pools, "route_legs", physical_legs)
    from scenarios.freeform import dataset

    model = GeometryModel(dataset("holdout", 1)[0])
    assert CoverageCandidatePool(generator=Generator()).candidates(model) == (valid,)
