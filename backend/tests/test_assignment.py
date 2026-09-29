from src.assignment import HybridAssignmentOptimizer
from src.schemas import (
    AssignmentAlgorithm,
    AssignmentOptimizationRequest,
    Coordinate,
    Mission,
    MissionAssignmentCandidate,
    OptimalityStatus,
    OptimizationCriterion,
    OptimizationMode,
)


def candidate(
    segment_id: str, drone_id: str, duration_minutes: float
) -> MissionAssignmentCandidate:
    return MissionAssignmentCandidate(
        segment_id=segment_id,
        drone_id=drone_id,
        mission=Mission(
            drone_id=drone_id,
            segment_id=segment_id,
            duration_minutes=duration_minutes,
            distance_km=duration_minutes,
            route=[
                Coordinate(longitude=37.4, latitude=55.7),
                Coordinate(longitude=37.41, latitude=55.71),
            ],
            altitude_m=100,
            cruise_speed_kmh=60,
            effective_speed_kmh=60,
            estimated_battery_percent=20,
        ),
    )


def test_exact_optimizer_minimizes_completion_time() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["first", "second"],
        candidate_groups=[
            [candidate("a", "first", 8), candidate("a", "second", 9)],
            [candidate("b", "first", 8), candidate("b", "second", 100)],
        ],
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.algorithm is AssignmentAlgorithm.EXACT_BRANCH_AND_BOUND
    assert result.optimality is OptimalityStatus.PROVEN_OPTIMAL
    assert result.completion_minutes == 9
    assert [item.drone_id for item in result.assignments] == ["second", "first"]


def test_exact_optimizer_minimizes_total_flight_time() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drone_ids=["economical", "fast"],
        candidate_groups=[
            [candidate("a", "economical", 5), candidate("a", "fast", 7)],
            [candidate("b", "economical", 5), candidate("b", "fast", 6)],
        ],
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.total_flight_minutes == 10
    assert result.active_drone_count == 1
    assert {item.drone_id for item in result.assignments} == {"economical"}


def test_optimizer_selects_capable_subset_when_active_fleet_is_limited() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drone_ids=["specialized", "universal"],
        candidate_groups=[
            [candidate("a", "specialized", 1), candidate("a", "universal", 4)],
            [candidate("b", "universal", 4)],
        ],
        maximum_active_drones=1,
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.active_drone_count == 1
    assert {item.drone_id for item in result.assignments} == {"universal"}


def test_balanced_optimizer_minimizes_flight_inside_deadline() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.BALANCED,
        drone_ids=["first", "second"],
        candidate_groups=[
            [candidate("a", "first", 5), candidate("a", "second", 4)],
            [candidate("b", "first", 5), candidate("b", "second", 4)],
        ],
        completion_deadline_minutes=6,
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.completion_minutes == 5
    assert result.total_flight_minutes == 9
    assert {item.drone_id for item in result.assignments} == {"first", "second"}


def test_exact_optimizer_uses_state_count_instead_of_arbitrary_segment_limit() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["first", "second"],
        candidate_groups=[
            [candidate(f"segment-{index}", "first", 5), candidate(f"segment-{index}", "second", 5)]
            for index in range(13)
        ],
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.algorithm is AssignmentAlgorithm.EXACT_BRANCH_AND_BOUND
    assert result.optimality is OptimalityStatus.PROVEN_OPTIMAL
    assert result.completion_minutes == 35


def test_problem_above_exact_state_limit_uses_multistart_local_search() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["first", "second"],
        candidate_groups=[
            [candidate(f"segment-{index}", "first", 5), candidate(f"segment-{index}", "second", 5)]
            for index in range(22)
        ],
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.algorithm is AssignmentAlgorithm.MULTISTART_LOCAL_SEARCH
    assert result.optimality is OptimalityStatus.HEURISTIC
    assert result.completion_minutes == 55


def test_fast_heuristic_is_used_only_when_explicitly_requested() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["first", "second"],
        candidate_groups=[
            [candidate("a", "first", 8), candidate("a", "second", 9)],
            [candidate("b", "first", 8), candidate("b", "second", 10)],
        ],
        optimization_mode=OptimizationMode.FAST_HEURISTIC,
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.algorithm is AssignmentAlgorithm.FAST_GREEDY
    assert result.optimality is OptimalityStatus.HEURISTIC


def test_completion_optimizer_accounts_for_turnaround_between_sorties() -> None:
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["fast-service", "steady"],
        candidate_groups=[
            [
                candidate(f"segment-{index}", "fast-service", 4),
                candidate(f"segment-{index}", "steady", 6),
            ]
            for index in range(3)
        ],
        turnaround_minutes_by_drone={"fast-service": 10, "steady": 0},
    )

    result = HybridAssignmentOptimizer().optimize(request)

    assert result.completion_minutes == 12
    assert result.total_flight_minutes == 16
    assert [item.drone_id for item in result.assignments].count("fast-service") == 1
