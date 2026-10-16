from benchmarks.plan_safety_audit import run_experiment


def test_safety_audit_benchmark_detects_every_injected_violation() -> None:
    measurements = run_experiment()

    assert len(measurements) == 5
    assert all(measurement.detected for measurement in measurements)
    assert measurements[0].failed_checks == []
