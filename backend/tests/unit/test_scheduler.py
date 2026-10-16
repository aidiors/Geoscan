from datetime import UTC, datetime

import pytest

from geoplan.domain.enums import ScheduleAlgorithm, ScheduleConflictType
from geoplan.domain.fleet import FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.scheduling.models import (
    MissionScheduleRequest,
    ScheduleTimeWindow,
    VerticalLayerOption,
)
from geoplan.scheduling.scheduler import (
    ConflictAwareMissionScheduler,
    ScheduleWindowUnavailableError,
)


def mission(
    drone_id: str,
    segment_id: str,
    route: list[Coordinate],
    duration_minutes: float = 10,
) -> Mission:
    return Mission(
        drone_id=drone_id,
        segment_id=segment_id,
        duration_minutes=duration_minutes,
        distance_km=1,
        route=route,
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
    )


def test_scheduler_delays_crossing_routes_to_keep_required_separation() -> None:
    crossing = [
        mission(
            "alpha",
            "west-east",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south-north",
            [
                Coordinate(longitude=37.4, latitude=55.694),
                Coordinate(longitude=37.4, latitude=55.706),
            ],
        ),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=crossing,
            minimum_separation_m=50,
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )

    scheduled = {item.drone_id: item for item in result.missions}
    assert result.algorithm is ScheduleAlgorithm.PRIORITY_DECONFLICTION
    assert scheduled["alpha"].start_offset_minutes == 0
    assert scheduled["bravo"].start_offset_minutes is not None
    assert scheduled["bravo"].start_offset_minutes > 0
    assert result.resolved_conflict_count == 1
    conflict = result.resolved_conflicts[0]
    assert conflict.conflict_type is ScheduleConflictType.ROUTE
    assert conflict.delayed_segment_id == "south-north"
    assert conflict.conflicting_segment_id == "west-east"
    assert 37.399 < conflict.coordinate.longitude < 37.401
    assert 55.699 < conflict.coordinate.latitude < 55.701
    assert conflict.applied_delay_minutes > 0
    assert result.makespan_minutes > 10


def test_scheduler_prefers_feasible_vertical_layer_to_route_delay() -> None:
    crossing = [
        mission(
            "alpha",
            "west-east",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south-north",
            [
                Coordinate(longitude=37.4, latitude=55.694),
                Coordinate(longitude=37.4, latitude=55.706),
            ],
        ).model_copy(
            update={
                "remaining_battery_percent": 80,
                "battery_reserve_margin_percent": 60,
            }
        ),
    ]
    layer = VerticalLayerOption(
        offset_m=30,
        climb_duration_minutes=0.05,
        descent_duration_minutes=0.05,
        extra_energy_equivalent_minutes=0.2,
        extra_battery_percent=1,
    )

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=crossing,
            minimum_separation_m=50,
            minimum_vertical_separation_m=30,
            vertical_layers_by_segment_id={
                "south-north": [
                    VerticalLayerOption(
                        offset_m=0,
                        extra_energy_equivalent_minutes=0,
                        extra_battery_percent=0,
                    ),
                    layer,
                ]
            },
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )

    scheduled = {item.drone_id: item for item in result.missions}
    assert scheduled["alpha"].start_offset_minutes == 0
    assert scheduled["bravo"].start_offset_minutes == 0
    assert scheduled["bravo"].altitude_m == 150
    assert scheduled["bravo"].vertical_deconfliction_offset_m == 30
    assert scheduled["bravo"].duration_minutes == 10.1
    assert scheduled["bravo"].vertical_climb_duration_minutes == 0.05
    assert scheduled["bravo"].vertical_descent_duration_minutes == 0.05
    assert scheduled["bravo"].remaining_battery_percent == 79
    assert result.resolved_conflicts[0].applied_delay_minutes == 0
    assert result.resolved_conflicts[0].applied_vertical_offset_m == 30


def test_vertical_layer_does_not_mask_shared_departure_transition() -> None:
    departure = Coordinate(longitude=37.39, latitude=55.7)
    first = mission(
        "alpha",
        "east",
        [departure, Coordinate(longitude=37.41, latitude=55.7)],
    )
    second = mission(
        "bravo",
        "north",
        [departure, Coordinate(longitude=37.39, latitude=55.72)],
    )
    layer = VerticalLayerOption(
        offset_m=30,
        climb_duration_minutes=0.1,
        descent_duration_minutes=0.1,
        extra_energy_equivalent_minutes=0.2,
        extra_battery_percent=1,
    )

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[first, second],
            minimum_separation_m=50,
            minimum_vertical_separation_m=30,
            vertical_layers_by_segment_id={
                "north": [
                    VerticalLayerOption(
                        offset_m=0,
                        extra_energy_equivalent_minutes=0,
                        extra_battery_percent=0,
                    ),
                    layer,
                ]
            },
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )

    scheduled = {item.drone_id: item for item in result.missions}
    assert scheduled["bravo"].start_offset_minutes is not None
    assert scheduled["bravo"].start_offset_minutes > 0


def test_scheduler_reports_prestart_uncertainty_conflict_at_zero_offset() -> None:
    same_route = [
        Coordinate(longitude=37.39, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[
                mission("alpha", "first", same_route),
                mission("bravo", "second", same_route),
            ],
            minimum_separation_m=50,
            start_time_uncertainty_seconds=2,
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )

    assert result.resolved_conflicts
    assert result.resolved_conflicts[0].conflict_offset_minutes == 0


def test_scheduler_preserves_turnaround_after_output_rounding() -> None:
    route = [
        Coordinate(longitude=37.39, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[
                mission("alpha", "first", route, duration_minutes=10.0004),
                mission("alpha", "second", route, duration_minutes=10.0004),
            ],
            minimum_separation_m=50,
            turnaround_minutes=5,
        )
    )

    first, second = result.missions
    assert first.end_offset_minutes is not None
    assert second.start_offset_minutes is not None
    assert first.end_offset_minutes + 5 <= second.start_offset_minutes


def test_scheduler_keeps_spatially_separated_routes_parallel() -> None:
    separated = [
        mission(
            "alpha",
            "north",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south",
            [
                Coordinate(longitude=37.39, latitude=55.71),
                Coordinate(longitude=37.41, latitude=55.71),
            ],
        ),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=separated,
            minimum_separation_m=300,
            turnaround_minutes=0,
        )
    )

    assert all(item.start_offset_minutes == 0 for item in result.missions)
    assert result.resolved_conflict_count == 0
    assert result.makespan_minutes == 10


def test_scheduler_delays_nominally_safe_routes_for_position_uncertainty() -> None:
    close_parallel = [
        mission(
            "alpha",
            "north",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south",
            [
                Coordinate(longitude=37.39, latitude=55.70055),
                Coordinate(longitude=37.41, latitude=55.70055),
            ],
        ),
    ]

    nominal = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=close_parallel,
            minimum_separation_m=50,
            turnaround_minutes=0,
        )
    )
    robust = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=close_parallel,
            minimum_separation_m=50,
            position_uncertainty_m=10,
            turnaround_minutes=0,
        )
    )

    assert nominal.makespan_minutes == 10
    assert robust.makespan_minutes > nominal.makespan_minutes
    robust_starts = [mission.start_offset_minutes for mission in robust.missions]
    assert max(start for start in robust_starts if start is not None) >= 0.3
    assert robust.resolved_conflict_count == 1


def test_scheduler_delays_for_bounded_speed_uncertainty() -> None:
    close_parallel = [
        mission(
            "alpha",
            "north",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south",
            [
                Coordinate(longitude=37.39, latitude=55.70055),
                Coordinate(longitude=37.41, latitude=55.70055),
            ],
        ),
    ]

    nominal = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=close_parallel,
            minimum_separation_m=50,
            turnaround_minutes=0,
        )
    )
    robust = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=close_parallel,
            minimum_separation_m=50,
            speed_uncertainty_percent=1,
            turnaround_minutes=0,
        )
    )

    assert nominal.makespan_minutes == 10
    assert robust.makespan_minutes > nominal.makespan_minutes
    assert robust.resolved_conflicts[0].required_distance_m > 50


def test_scheduler_reserves_shared_flight_site() -> None:
    shared_site = FlightSite(
        site_id="base",
        name="Base",
        coordinate=Coordinate(longitude=37.4, latitude=55.7),
    )
    missions = [
        mission(
            "alpha",
            "west",
            [
                Coordinate(longitude=37.38, latitude=55.69),
                Coordinate(longitude=37.39, latitude=55.69),
            ],
        ).model_copy(update={"departure_site": shared_site}),
        mission(
            "bravo",
            "east",
            [
                Coordinate(longitude=37.41, latitude=55.71),
                Coordinate(longitude=37.42, latitude=55.71),
            ],
        ).model_copy(update={"departure_site": shared_site}),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=missions,
            minimum_separation_m=50,
            flight_site_occupancy_seconds=120,
            turnaround_minutes=0,
            delay_step_seconds=10,
        )
    )

    assert result.makespan_minutes == 12
    assert result.resolved_conflicts[0].conflict_type is ScheduleConflictType.FLIGHT_SITE
    assert result.resolved_conflicts[0].coordinate == shared_site.coordinate


def test_scheduler_protects_critical_emergency_corridor() -> None:
    north = mission(
        "alpha",
        "north",
        [
            Coordinate(longitude=37.39, latitude=55.71),
            Coordinate(longitude=37.41, latitude=55.71),
        ],
    ).model_copy(
        update={
            "critical_alternate_route": [
                Coordinate(longitude=37.4, latitude=55.706),
                Coordinate(longitude=37.4, latitude=55.694),
            ],
            "critical_alternate_start_offset_minutes": 0,
            "critical_alternate_duration_minutes": 10,
        }
    )
    crossing = mission(
        "bravo",
        "crossing",
        [
            Coordinate(longitude=37.39, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.7),
        ],
    )

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[north, crossing],
            minimum_separation_m=50,
            turnaround_minutes=0,
            delay_step_seconds=1,
        )
    )

    assert result.makespan_minutes > 10
    assert result.resolved_conflicts[0].conflict_type is ScheduleConflictType.EMERGENCY_CORRIDOR


def test_scheduler_protects_emergency_corridor_after_nominal_mission_ends() -> None:
    protected = mission(
        "alpha",
        "short-nominal",
        [
            Coordinate(longitude=37.39, latitude=55.71),
            Coordinate(longitude=37.41, latitude=55.71),
        ],
        duration_minutes=2,
    ).model_copy(
        update={
            "critical_alternate_route": [
                Coordinate(longitude=37.4, latitude=55.706),
                Coordinate(longitude=37.4, latitude=55.694),
            ],
            "critical_alternate_start_offset_minutes": 1,
            "critical_alternate_duration_minutes": 10,
        }
    )
    later_crossing = mission(
        "bravo",
        "later-crossing",
        [
            Coordinate(longitude=37.39, latitude=55.7),
            Coordinate(longitude=37.41, latitude=55.7),
        ],
        duration_minutes=2,
    )

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[protected, later_crossing],
            minimum_separation_m=50,
            turnaround_minutes=0,
            delay_step_seconds=1,
            time_windows_by_segment_id={
                "later-crossing": [
                    ScheduleTimeWindow(start_offset_minutes=5, end_offset_minutes=20)
                ]
            },
        )
    )

    crossing = next(item for item in result.missions if item.segment_id == "later-crossing")
    assert crossing.start_offset_minutes is not None
    assert crossing.start_offset_minutes > 5
    assert result.resolved_conflicts[0].conflict_type is ScheduleConflictType.EMERGENCY_CORRIDOR


def test_scheduler_shrinks_airspace_window_for_start_time_uncertainty() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[mission("alpha", "timed", route)],
            minimum_separation_m=50,
            start_time_uncertainty_seconds=60,
            turnaround_minutes=0,
            time_windows_by_segment_id={
                "timed": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=12)]
            },
        )
    )

    assert result.missions[0].start_offset_minutes == 1
    assert result.missions[0].end_offset_minutes == 11


def test_scheduler_shrinks_airspace_window_for_speed_uncertainty() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    with pytest.raises(ScheduleWindowUnavailableError):
        ConflictAwareMissionScheduler().schedule(
            MissionScheduleRequest(
                missions=[mission("alpha", "timed", route)],
                minimum_separation_m=50,
                speed_uncertainty_percent=20,
                turnaround_minutes=0,
                time_windows_by_segment_id={
                    "timed": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=12)]
                },
            )
        )


def test_scheduler_sequences_repeated_sorties_with_turnaround() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[
                mission("alpha", "first", route),
                mission("alpha", "second", route),
            ],
            minimum_separation_m=50,
            turnaround_minutes=2,
        )
    )

    scheduled = {item.segment_id: item for item in result.missions}
    assert scheduled["first"].start_offset_minutes == 0
    assert scheduled["first"].end_offset_minutes == 10
    assert scheduled["second"].start_offset_minutes == 12
    assert scheduled["second"].end_offset_minutes == 22
    assert result.makespan_minutes == 22


def test_scheduler_uses_drone_specific_minimum_turnaround() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[
                mission("alpha", "first", route),
                mission("alpha", "second", route),
            ],
            minimum_separation_m=50,
            turnaround_minutes=2,
            turnaround_minutes_by_drone={"alpha": 7},
        )
    )

    first, second = result.missions
    assert first.end_offset_minutes == 10
    assert second.start_offset_minutes == 17


def test_scheduler_waits_for_airspace_window_and_sets_absolute_times() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]
    planning_start = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=[mission("alpha", "timed", route)],
            minimum_separation_m=50,
            turnaround_minutes=0,
            planning_start_time=planning_start,
            time_windows_by_segment_id={
                "timed": [ScheduleTimeWindow(start_offset_minutes=20, end_offset_minutes=40)]
            },
        )
    )

    scheduled = result.missions[0]
    assert scheduled.start_offset_minutes == 20
    assert scheduled.end_offset_minutes == 30
    assert scheduled.start_time == datetime(2026, 9, 27, 7, 20, tzinfo=UTC)
    assert scheduled.end_time == datetime(2026, 9, 27, 7, 30, tzinfo=UTC)


def test_scheduler_rejects_mission_longer_than_every_airspace_window() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    with pytest.raises(ScheduleWindowUnavailableError):
        ConflictAwareMissionScheduler().schedule(
            MissionScheduleRequest(
                missions=[mission("alpha", "timed", route)],
                minimum_separation_m=50,
                turnaround_minutes=0,
                time_windows_by_segment_id={
                    "timed": [ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=5)]
                },
            )
        )


def test_scheduler_moves_conflicting_mission_to_next_airspace_window() -> None:
    crossing = [
        mission(
            "alpha",
            "west-east",
            [
                Coordinate(longitude=37.39, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.7),
            ],
        ),
        mission(
            "bravo",
            "south-north",
            [
                Coordinate(longitude=37.4, latitude=55.694),
                Coordinate(longitude=37.4, latitude=55.706),
            ],
        ),
    ]

    result = ConflictAwareMissionScheduler().schedule(
        MissionScheduleRequest(
            missions=crossing,
            minimum_separation_m=5_000,
            turnaround_minutes=0,
            delay_step_seconds=60,
            time_windows_by_segment_id={
                "south-north": [
                    ScheduleTimeWindow(start_offset_minutes=0, end_offset_minutes=12),
                    ScheduleTimeWindow(start_offset_minutes=20, end_offset_minutes=40),
                ]
            },
        )
    )

    scheduled = {item.drone_id: item for item in result.missions}
    assert scheduled["alpha"].start_offset_minutes == 0
    assert scheduled["bravo"].start_offset_minutes == 20
    assert result.resolved_conflict_count == 1


def test_mission_rejects_incomplete_schedule_offsets() -> None:
    route = [
        Coordinate(longitude=37.4, latitude=55.7),
        Coordinate(longitude=37.41, latitude=55.7),
    ]

    with pytest.raises(ValueError):
        Mission.model_validate(
            {
                **mission("alpha", "incomplete", route).model_dump(),
                "start_offset_minutes": 0,
            }
        )
    with pytest.raises(ValueError, match="относительных смещений"):
        Mission.model_validate(
            {
                **mission("alpha", "absolute-only", route).model_dump(),
                "start_time": "2026-09-27T07:00:00Z",
                "end_time": "2026-09-27T07:10:00Z",
            }
        )
