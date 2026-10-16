from benchmarks.actionable_feasibility import FeasibilityScenario, run_experiment


def test_actionable_feasibility_benchmark_returns_expected_counterfactuals() -> None:
    measurements = {measurement.scenario: measurement for measurement in run_experiment()}

    assert measurements[FeasibilityScenario.DEADLINE].recommendation_codes == [
        "extend_deadline",
        "add_capable_drone",
    ]
    assert measurements[FeasibilityScenario.DEADLINE].exact_required_values == {
        "completion_deadline_minutes": 20
    }
    assert measurements[FeasibilityScenario.ENDURANCE].recommendation_codes == [
        "extend_endurance",
        "reduce_mission_scope",
    ]
    assert measurements[FeasibilityScenario.ENDURANCE].exact_required_values == {
        "maximum_flight_minutes": 37.5
    }
    assert measurements[FeasibilityScenario.WIND].exact_required_values == {"wind.speed_mps": 10}
    assert measurements[FeasibilityScenario.TASK_CAPABILITY].recommendation_codes == [
        "add_capable_drone"
    ]
    assert measurements[FeasibilityScenario.FLIGHT_SITE].recommendation_codes == [
        "assign_flight_site"
    ]
    assert measurements[FeasibilityScenario.ALTITUDE].exact_required_values == {
        "mission_altitude_m": 100
    }
