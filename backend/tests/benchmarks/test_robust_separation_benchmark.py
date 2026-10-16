from benchmarks.robust_separation import run_experiment


def test_robust_separation_benchmark_certifies_every_schedule() -> None:
    measurements = {measurement.scenario: measurement for measurement in run_experiment()}

    assert all(measurement.audit_passed for measurement in measurements.values())
    assert measurements["close_nominal"].delayed_start_minutes == 0
    assert measurements["close_position_robust"].delayed_start_minutes > 0
    assert (
        measurements["crossing_time_robust"].delayed_start_minutes
        > measurements["crossing_nominal"].delayed_start_minutes
    )
    assert measurements["far_combined_robust"].delayed_start_minutes == 0
