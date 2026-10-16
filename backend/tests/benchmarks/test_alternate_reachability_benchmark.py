from benchmarks.alternate_reachability import run_experiment


def test_adaptive_alternate_benchmark_covers_certified_outcomes() -> None:
    measurements = {measurement.scenario: measurement for measurement in run_experiment()}

    calm = measurements["reachable_10km_calm"]
    headwind = measurements["reachable_10km_headwind"]
    unreachable = measurements["unreachable_30km_calm"]
    obstacle = measurements["adaptive_obstacle_refinement"]

    assert calm.status == "feasible"
    assert calm.evaluated_checkpoint_count == 2
    assert calm.certification_error_minutes == 0
    assert headwind.status == "feasible"
    assert headwind.evaluated_checkpoint_count is not None
    assert headwind.evaluated_checkpoint_count > calm.evaluated_checkpoint_count
    assert headwind.certification_error_minutes is not None
    assert headwind.certification_error_minutes <= 0.1
    assert unreachable.status == "infeasible"
    assert unreachable.error_code == "alternate_landing_unreachable"
    assert obstacle.status == "feasible"
    assert obstacle.evaluated_checkpoint_count is not None
    assert obstacle.evaluated_checkpoint_count > 2
    assert obstacle.certification_error_minutes is not None
    assert obstacle.certification_error_minutes <= 0.1
