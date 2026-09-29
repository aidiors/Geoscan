from benchmarks.airspace_time_windows import AirspaceScenario, run_experiment
from src.schemas import ErrorCode


def test_airspace_time_window_benchmark_matches_expected_schedule() -> None:
    measurements = {measurement.scenario: measurement for measurement in run_experiment()}

    assert measurements[AirspaceScenario.PERMANENT].start_offset_minutes == 0
    assert measurements[AirspaceScenario.DELAYED].start_offset_minutes == 30
    assert measurements[AirspaceScenario.SECOND_WINDOW].start_offset_minutes == 20
    assert measurements[AirspaceScenario.SPLIT_UNION].start_offset_minutes == 10
    assert (
        measurements[AirspaceScenario.TOO_SHORT].error_code is ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE
    )
    assert measurements[AirspaceScenario.NO_FLY_ACTIVE].start_offset_minutes == 30
    assert measurements[AirspaceScenario.NO_FLY_ABOVE].start_offset_minutes == 0
    assert (
        measurements[AirspaceScenario.OPERATING_WINDOW_TOO_SHORT].error_code
        is ErrorCode.AIRSPACE_WINDOW_UNAVAILABLE
    )
