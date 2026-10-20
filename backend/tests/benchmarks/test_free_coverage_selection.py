from benchmarks.free_coverage_selection import endpoint_protected_choice


def baseline():
    return {
        "case_id": "same_quality_and_hardware",
        "family": "production_reference",
        "screen_passed": True,
        "flight_minutes": 20,
        "deadhead_minutes": 3,
        "departure_minutes": 1,
        "return_minutes": 1,
    }


def test_endpoint_selection_rejects_a_faster_route_with_a_longer_return():
    reference = baseline()
    fastest = {**reference, "family": "fast_but_far", "flight_minutes": 18, "return_minutes": 2}
    balanced = {
        **reference,
        "family": "near_both_sites",
        "flight_minutes": 19,
        "return_minutes": 0.5,
    }
    assert endpoint_protected_choice([reference, fastest, balanced]) == balanced


def test_endpoint_selection_keeps_the_reference_when_no_joint_improvement_exists():
    reference = baseline()
    for metric in ("flight_minutes", "deadhead_minutes", "departure_minutes", "return_minutes"):
        alternative = {**reference, "family": "tradeoff", "flight_minutes": 19}
        alternative[metric] = reference[metric] + 0.1
        assert endpoint_protected_choice([reference, alternative]) == reference
    invalid = {**reference, "family": "incomplete", "flight_minutes": 10, "screen_passed": False}
    assert endpoint_protected_choice([reference, invalid]) == reference


def test_invalid_reference_cannot_establish_an_endpoint_guarantee():
    assert endpoint_protected_choice([{**baseline(), "screen_passed": False}]) is None


def test_endpoint_selection_cannot_mix_quality_profiles():
    import pytest

    with pytest.raises(ValueError, match="one frozen case"):
        endpoint_protected_choice([baseline(), {**baseline(), "case_id": "lower_quality"}])
