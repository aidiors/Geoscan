from benchmarks.vertical_deconfliction import run_experiment
from geoplan.domain.enums import SafetyAuditStatus


def test_vertical_layers_reduce_delay_without_violating_ceiling() -> None:
    measurements = {item.scenario: item for item in run_experiment()}
    temporal = measurements["temporal_only"]
    layered = measurements["vertical_layers"]
    short = measurements["short_conflict_prefers_delay"]
    ceiling = measurements["ceiling_blocks_layer"]

    assert all(item.safety_status is SafetyAuditStatus.PASSED for item in measurements.values())
    assert layered.vertically_deconflicted_mission_count == 1
    assert layered.maximum_vertical_offset_m == 30
    assert layered.total_vertical_phase_minutes > 0
    assert layered.maximum_applied_delay_minutes == 0
    assert layered.makespan_minutes < temporal.makespan_minutes
    assert layered.minimum_battery_reserve_margin_percent < (
        temporal.minimum_battery_reserve_margin_percent
    )
    assert short.vertically_deconflicted_mission_count == 0
    assert short.maximum_applied_delay_minutes > 0
    assert ceiling.vertically_deconflicted_mission_count == 0
    assert ceiling.maximum_vertical_offset_m == 0
    assert ceiling.maximum_applied_delay_minutes > 0
