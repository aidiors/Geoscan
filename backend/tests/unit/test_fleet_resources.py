from itertools import product

import pytest

from geoplan.assignment.models import AssignmentOptimizationRequest
from geoplan.assignment.solver import HybridAssignmentOptimizer, _score
from geoplan.domain.enums import OptimizationCriterion, OptimizationMode, SafetyAuditStatus
from geoplan.domain.fleet import DroneResourceLimit
from geoplan.safety.audit import _fleet_resources_check
from scenarios.planning import productive_survey_request
from tests.unit.test_assignment import candidate


def test_aircraft_budget_combines_batteries_and_remaining_maintenance():
    drone = (
        productive_survey_request()
        .drones[0]
        .model_copy(
            update={
                "available_battery_sets": 4,
                "maintenance_interval_flights": 10,
                "completed_flights_since_maintenance": 7,
                "maintenance_interval_hours": 20,
                "flight_hours_since_maintenance": 19.5,
            }
        )
    )
    limit = drone.resource_limit()
    assert limit.maximum_sorties == 3
    assert limit.maximum_flight_minutes == 30
    assert limit.allows(3, 30)
    assert not limit.allows(4, 20)
    assert not limit.allows(2, 30.01)


@pytest.mark.parametrize("mode", list(OptimizationMode))
@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_optimizers_respect_sortie_and_flight_budgets_with_turnaround(mode, criterion):
    request = AssignmentOptimizationRequest(
        criterion=criterion,
        optimization_mode=mode,
        drone_ids=["a", "b"],
        candidate_groups=[[candidate(str(i), "a", 3), candidate(str(i), "b", 4)] for i in range(4)],
        resource_limits_by_drone={
            "a": DroneResourceLimit(maximum_sorties=2),
            "b": DroneResourceLimit(maximum_flight_minutes=8),
        },
        turnaround_minutes_by_drone={"a": 10, "b": 10},
        completion_deadline_minutes=30,
    )
    result = HybridAssignmentOptimizer().optimize(request)
    counts = [sum(c.drone_id == drone for c in result.assignments) for drone in ("a", "b")]
    assert counts == [2, 2]


def test_cp_sat_respects_resource_limits():
    pytest.importorskip("ortools")
    from geoplan.assignment.cp_sat import CpSatAssignmentSolver

    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drone_ids=["a", "b"],
        candidate_groups=[[candidate(str(i), "a", 3), candidate(str(i), "b", 4)] for i in range(4)],
        resource_limits_by_drone={
            "a": DroneResourceLimit(maximum_sorties=2),
            "b": DroneResourceLimit(maximum_flight_minutes=8),
        },
    )
    solved = CpSatAssignmentSolver().solve(request)
    assert solved.evaluation is not None
    assert sorted(solved.evaluation.choices) == [0, 0, 1, 1]


def test_exact_search_continues_when_greedy_seed_has_no_feasible_assignment():
    # Greedy takes a for the longer task; only the opposite allocation fits.
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drone_ids=["a", "b"],
        candidate_groups=[
            [candidate("long", "a", 9), candidate("long", "b", 10)],
            [candidate("short", "a", 2), candidate("short", "b", 11)],
        ],
        resource_limits_by_drone={
            "a": DroneResourceLimit(maximum_flight_minutes=10),
            "b": DroneResourceLimit(maximum_flight_minutes=10),
        },
    )
    result = HybridAssignmentOptimizer().optimize(request)
    assert [c.drone_id for c in result.assignments] == ["b", "a"]


@pytest.mark.parametrize("criterion", list(OptimizationCriterion))
def test_constrained_exact_assignment_matches_independent_enumeration(criterion):
    request = AssignmentOptimizationRequest(
        criterion=criterion,
        drone_ids=["a", "b"],
        completion_deadline_minutes=20,
        candidate_groups=[
            [candidate(str(i), "a", a), candidate(str(i), "b", b)]
            for i, (a, b) in enumerate(((4, 7), (7, 5), (3, 8), (8, 6)))
        ],
        resource_limits_by_drone={
            "a": DroneResourceLimit(maximum_sorties=2, maximum_flight_minutes=12),
            "b": DroneResourceLimit(maximum_sorties=3, maximum_flight_minutes=17),
        },
    )
    expected = []
    for choices in product(range(2), repeat=4):
        flight = {drone: 0.0 for drone in request.drone_ids}
        counts = {drone: 0 for drone in request.drone_ids}
        for group, selected in zip(request.candidate_groups, choices, strict=True):
            c = group[selected]
            flight[c.drone_id] += c.mission.duration_minutes
            counts[c.drone_id] += 1
        if counts["a"] > 2 or counts["b"] > 3 or flight["a"] > 12 or flight["b"] > 17:
            continue
        total, completion = sum(flight.values()), max(flight.values())
        if criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
            score = total, completion, 2.0, 0.0
        elif criterion is OptimizationCriterion.COMPLETION_TIME:
            score = completion, total, 2.0, 0.0
        else:
            score = 0.0, total, completion, 2.0
        expected.append(score)
    result = HybridAssignmentOptimizer().optimize(request)
    choices = [0 if c.drone_id == "a" else 1 for c in result.assignments]
    assert _score(request, choices) == min(expected)


def test_audit_recounts_actual_sorties_and_hours_instead_of_trusting_optimizer():
    request = productive_survey_request()
    drone = request.drones[0].model_copy(update={"available_battery_sets": 1})
    request = request.model_copy(update={"drones": [drone]})
    missions = [candidate(str(i), drone.drone_id, 3).mission for i in range(2)]
    assert _fleet_resources_check(request, missions).status is SafetyAuditStatus.FAILED
    assert _fleet_resources_check(request, missions[:1]).status is SafetyAuditStatus.PASSED


def test_forced_tasks_with_choices_cannot_exceed_active_fleet_limit():
    request = AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=["a", "b"],
        maximum_active_drones=1,
        candidate_groups=[
            [candidate("a", "a", 1)],
            [candidate("b", "b", 1)],
            [candidate("choice", "a", 1), candidate("choice", "b", 1)],
        ],
    )
    with pytest.raises(ValueError):
        HybridAssignmentOptimizer().optimize(request)
