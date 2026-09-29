from benchmarks.complete_robust_deconfliction import run_experiment


def test_complete_robust_deconfliction_matrix() -> None:
    measurements = {measurement.scenario: measurement for measurement in run_experiment()}

    assert all(
        measurement.independent_nominal_route_audit_passed for measurement in measurements.values()
    )
    assert measurements["speed_uncertainty"].resolved_conflict_types == ["route"]
    assert measurements["shared_flight_site"].resolved_conflict_types == ["flight_site"]
    assert measurements["critical_emergency_corridor"].resolved_conflict_types == [
        "emergency_corridor"
    ]
    assert measurements["sampled_turn_geometry"].resolved_conflict_types == ["route"]
    assert all(measurement.conflict_marker_count > 0 for measurement in measurements.values())
    assert measurements["priority_vs_two_agent_cbs"].cbs_improvement_minutes > 0
