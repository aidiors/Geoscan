from benchmarks.wind_aware_sweep import run_experiment


def test_wind_aware_sweep_benchmark_has_no_duration_regressions() -> None:
    measurements = run_experiment()

    assert len(measurements) == 4
    assert all(measurement.duration_improvement_percent >= 0 for measurement in measurements)
    assert max(measurement.duration_improvement_percent for measurement in measurements) >= 20
