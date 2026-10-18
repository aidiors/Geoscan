import pytest
from shapely.geometry import Polygon

from benchmarks.optimization_scale import summarize
from geoplan.navigation.geometry import LocalProjection
from scenarios.optimization_scale import scale_scenarios


def test_scale_comparison_has_explicit_sites_real_overlap_and_heterogeneous_fleet():
    projection = LocalProjection(37, 55)
    for case in scale_scenarios():
        request = case.request
        area = Polygon([projection.project(p) for p in request.coverage_areas[0].coordinates])
        assert area.area / 1e6 == pytest.approx(case.area_km2, abs=1e-6)
        assert request.departure_site is not None and request.landing_site is not None
        assert request.survey_parameters is not None
        assert request.survey_parameters.side_overlap_percent >= 50
        assert len(request.drones) == 10
        assert len({drone.cruise_speed_kmh for drone in request.drones}) == 10
        assert len({drone.maximum_flight_minutes for drone in request.drones}) == 10


def test_tuning_and_holdout_are_separate_and_wind_case_has_distant_base():
    cases = {case.name: case for case in scale_scenarios()}
    assert cases["tuning_rectangle_10"].split == "tuning"
    assert all(
        case.split in {"holdout", "evaluation"} for case in cases.values() if case.area_km2 == 100
    )
    assert len([case for case in cases.values() if case.split == "evaluation"]) == 2
    assert cases["wind_distant_100"].request.wind is not None
    assert (
        cases["wind_distant_100"].request.departure_site != cases["flat_100"].request.departure_site
    )


def test_scale_summary_never_treats_failed_audit_as_a_win():
    result = summarize(
        [
            {
                "scenario": "field",
                "criterion": "completion_time",
                "mode": "lns",
                "audited": True,
                "objective": [10, 20],
            },
            {
                "scenario": "field",
                "criterion": "completion_time",
                "mode": "portfolio",
                "audited": False,
                "objective": [1, 1],
            },
        ]
    )
    assert result["audit_or_planning_failures"] == 1
    assert result["pair_counts"]["invalid"] == 1
    assert result["pair_counts"]["improved"] == 0


def test_scale_summary_compares_each_business_criterion_separately():
    result = summarize(
        [
            {
                "scenario": "field",
                "criterion": criterion,
                "mode": mode,
                "audited": True,
                "objective": score,
            }
            for criterion, mode, score in (
                ("completion_time", "lns", [10, 20]),
                ("completion_time", "portfolio", [9, 25]),
                ("total_flight_time", "lns", [20, 10]),
                ("total_flight_time", "portfolio", [25, 9]),
            )
        ]
    )
    assert result["pair_counts"]["improved"] == 1
    assert result["pair_counts"]["regression"] == 1


def test_cross_summary_keeps_same_run_effect_separate_from_independent_run_variation():
    result = summarize(
        [
            {
                "scenario": "field",
                "criterion": "completion_time",
                "mode": "portfolio",
                "audited": True,
                "objective": [8, 20],
            },
            {
                "scenario": "field",
                "criterion": "completion_time",
                "mode": "cross",
                "audited": True,
                "objective": [9, 20],
                "cross_objective_comparison": {
                    "before": [10, 20],
                    "after": [9, 20],
                    "verdict": "improved",
                },
            },
        ]
    )
    assert result["pair_counts"]["improved"] == 1
    assert result["comparisons"][0]["independent_run_verdict"] == "regression"
