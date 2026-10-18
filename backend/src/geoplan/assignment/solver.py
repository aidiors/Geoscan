from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb, prod
from typing import Protocol

from geoplan.assignment.models import (
    AssignmentOptimizationRequest,
    AssignmentOptimizationResult,
    MissionAssignmentCandidate,
)
from geoplan.assignment.neighborhood import AssignmentNeighborhoodSearch
from geoplan.budget import check_planning_budget
from geoplan.domain.enums import (
    AssignmentAlgorithm,
    OptimalityStatus,
    OptimizationCriterion,
    OptimizationMode,
)

Assignment = list[int]
Score = tuple[float, float, float, float]
INFEASIBLE_SCORE: Score = (float("inf"), float("inf"), float("inf"), float("inf"))


class AssignmentOptimizer(Protocol):
    def optimize(self, request: AssignmentOptimizationRequest) -> AssignmentOptimizationResult: ...


@dataclass(frozen=True)
class HybridAssignmentOptimizer:
    exact_drone_limit: int = 6
    exact_state_limit: int = 2_000_000
    subset_limit: int = 128

    def optimize(self, request: AssignmentOptimizationRequest) -> AssignmentOptimizationResult:
        if request.optimization_mode is OptimizationMode.FAST_HEURISTIC:
            assignment = _best_greedy_assignment(request, self.subset_limit)
            return _result(
                request,
                assignment,
                AssignmentAlgorithm.FAST_GREEDY,
                OptimalityStatus.HEURISTIC,
            )
        exact_states = prod(len(group) for group in request.candidate_groups)
        use_exact = (
            len(request.drone_ids) <= self.exact_drone_limit
            and exact_states <= self.exact_state_limit
        )
        if use_exact:
            assignment = _exact_branch_and_bound(request)
            algorithm = AssignmentAlgorithm.EXACT_BRANCH_AND_BOUND
            optimality = OptimalityStatus.PROVEN_OPTIMAL
        else:
            assignment = _best_heuristic_assignment(request, self.subset_limit)
            algorithm = _heuristic_algorithm(request.criterion)
            optimality = OptimalityStatus.HEURISTIC
        return _result(request, assignment, algorithm, optimality)


def _heuristic_algorithm(criterion: OptimizationCriterion) -> AssignmentAlgorithm:
    if criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
        return AssignmentAlgorithm.MINIMUM_COST
    if criterion is OptimizationCriterion.BALANCED:
        return AssignmentAlgorithm.DEADLINE_AWARE_LOCAL_SEARCH
    return AssignmentAlgorithm.MULTISTART_LOCAL_SEARCH


def _result(
    request: AssignmentOptimizationRequest,
    assignment: Assignment,
    algorithm: AssignmentAlgorithm,
    optimality: OptimalityStatus,
) -> AssignmentOptimizationResult:
    selected = [
        group[candidate_index]
        for group, candidate_index in zip(request.candidate_groups, assignment, strict=True)
    ]
    loads = _loads(request, assignment)
    active_count = sum(load > 0 for load in loads.values())
    return AssignmentOptimizationResult(
        assignments=selected,
        algorithm=algorithm,
        optimality=optimality,
        completion_minutes=round(max(loads.values(), default=0), 2),
        total_flight_minutes=round(_total_flight_minutes(request, assignment), 2),
        active_drone_count=active_count,
    )


def _best_heuristic_assignment(
    request: AssignmentOptimizationRequest,
    subset_limit: int,
) -> Assignment:
    best: Assignment | None = None
    best_score: Score | None = None
    for allowed_drones in _drone_subsets(request, subset_limit):
        candidate = _greedy_assignment(request, allowed_drones)
        if candidate is None:
            continue
        improved = _local_search(request, candidate, allowed_drones)
        score = _score(request, improved)
        if best_score is None or score < best_score:
            best = improved
            best_score = score
    if best is None:
        raise ValueError("Ни один допустимый набор БВС не покрывает все участки.")
    return best


def _best_greedy_assignment(
    request: AssignmentOptimizationRequest,
    subset_limit: int,
) -> Assignment:
    best: Assignment | None = None
    best_score: Score | None = None
    for allowed_drones in _drone_subsets(request, subset_limit):
        candidate = _greedy_assignment(request, allowed_drones)
        if candidate is None:
            continue
        score = _score(request, candidate)
        if best_score is None or score < best_score:
            best = candidate
            best_score = score
    if best is None:
        raise ValueError("Ни один допустимый набор БВС не покрывает все участки.")
    return best


def _drone_subsets(
    request: AssignmentOptimizationRequest,
    subset_limit: int,
) -> list[frozenset[str]]:
    maximum = request.maximum_active_drones
    if maximum is None or maximum >= len(request.drone_ids):
        return [frozenset(request.drone_ids)]
    subset_count = comb(len(request.drone_ids), maximum)
    if subset_count <= subset_limit:
        return [frozenset(subset) for subset in combinations(request.drone_ids, maximum)]
    ranked = sorted(
        request.drone_ids,
        key=lambda drone_id: _drone_rank(request, drone_id),
    )
    return [frozenset(ranked[:maximum])]


def _drone_rank(
    request: AssignmentOptimizationRequest,
    drone_id: str,
) -> tuple[int, float, str]:
    durations = [
        candidate.mission.duration_minutes
        for group in request.candidate_groups
        for candidate in group
        if candidate.drone_id == drone_id
    ]
    missing = len(request.candidate_groups) - len(durations)
    average = sum(durations) / len(durations) if durations else float("inf")
    return missing, average, drone_id


def _greedy_assignment(
    request: AssignmentOptimizationRequest,
    allowed_drones: frozenset[str],
) -> Assignment | None:
    order = _task_order(request)
    assignment = [0] * len(request.candidate_groups)
    loads = {drone_id: 0.0 for drone_id in request.drone_ids}
    counts = {drone_id: 0 for drone_id in request.drone_ids}
    total_flight_minutes = 0.0
    for task_index in order:
        check_planning_budget()
        group = request.candidate_groups[task_index]
        choices = [
            (candidate_index, candidate)
            for candidate_index, candidate in enumerate(group)
            if candidate.drone_id in allowed_drones
            and _addition_allowed(request, loads, counts, candidate)
        ]
        if not choices:
            return None
        candidate_index, candidate = min(
            choices,
            key=lambda choice: _projected_score(
                request, loads, counts, total_flight_minutes, choice[1]
            ),
        )
        assignment[task_index] = candidate_index
        loads[candidate.drone_id] += candidate.mission.duration_minutes + (
            _turnaround_minutes(request, candidate.drone_id)
            if counts[candidate.drone_id] > 0
            else 0
        )
        counts[candidate.drone_id] += 1
        total_flight_minutes += candidate.mission.duration_minutes
    return assignment


def _task_order(request: AssignmentOptimizationRequest) -> list[int]:
    if request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
        return sorted(
            range(len(request.candidate_groups)),
            key=lambda i: len(request.candidate_groups[i]) != 1,
        )
    return sorted(
        range(len(request.candidate_groups)),
        key=lambda index: (
            len(request.candidate_groups[index]) != 1,
            -min(c.mission.duration_minutes for c in request.candidate_groups[index]),
        ),
    )


def _projected_score(
    request: AssignmentOptimizationRequest,
    loads: dict[str, float],
    counts: dict[str, int],
    total_flight_minutes: float,
    candidate: MissionAssignmentCandidate,
) -> Score:
    projected = dict(loads)
    projected[candidate.drone_id] += candidate.mission.duration_minutes + (
        _turnaround_minutes(request, candidate.drone_id) if counts[candidate.drone_id] > 0 else 0
    )
    return _objective(
        request,
        projected,
        total_flight_minutes + candidate.mission.duration_minutes,
    )


def _local_search(
    request: AssignmentOptimizationRequest,
    assignment: Assignment,
    allowed_drones: frozenset[str],
) -> Assignment:
    return AssignmentNeighborhoodSearch().improve(
        request, assignment, allowed_drones, lambda choices: _score(request, choices)
    )


def _exact_branch_and_bound(request: AssignmentOptimizationRequest) -> Assignment:
    if all(len(group) == 1 for group in request.candidate_groups):
        # No search is needed, but the active-fleet constraint still applies.
        active = {group[0].drone_id for group in request.candidate_groups}
        if (
            request.maximum_active_drones is not None
            and len(active) > request.maximum_active_drones
        ):
            raise ValueError("Ни один допустимый набор БВС не покрывает все участки.")
        forced = [0] * len(request.candidate_groups)
        if _score(request, forced)[0] == float("inf"):
            raise ValueError("Aircraft resources do not cover mandatory sorties.")
        check_planning_budget()
        return forced
    try:
        initial = _best_heuristic_assignment(request, subset_limit=128)
    except ValueError:
        initial = None
    best = list(initial) if initial is not None else None
    best_score = _score(request, best) if best is not None else INFEASIBLE_SCORE
    # Forced assignments must not consume recursion depth. With the exact-state
    # bound, only genuinely branching tasks remain (at most log2(state_limit)).
    order = [i for i in _task_order(request) if len(request.candidate_groups[i]) > 1]
    assignment = [0] * len(request.candidate_groups)
    loads = {drone_id: 0.0 for drone_id in request.drone_ids}
    counts = {drone_id: 0 for drone_id in request.drone_ids}
    total_flight_minutes = 0.0
    active: set[str] = set()
    maximum_active = request.maximum_active_drones or len(request.drone_ids)
    for group in request.candidate_groups:
        if len(group) != 1:
            continue
        check_planning_budget()
        candidate = group[0]
        if not _addition_allowed(request, loads, counts, candidate):
            raise ValueError("Aircraft resources do not cover mandatory sorties.")
        loads[candidate.drone_id] += candidate.mission.duration_minutes + (
            _turnaround_minutes(request, candidate.drone_id) if counts[candidate.drone_id] else 0
        )
        counts[candidate.drone_id] += 1
        total_flight_minutes += candidate.mission.duration_minutes
        active.add(candidate.drone_id)
    if len(active) > maximum_active:
        raise ValueError("Mandatory assignments exceed the active fleet limit.")
    minimum_remaining = _minimum_remaining_costs(request, order)

    def search(position: int) -> None:
        check_planning_budget()
        nonlocal best, best_score, total_flight_minutes
        if _branch_cannot_improve(
            request,
            loads,
            total_flight_minutes,
            minimum_remaining[position],
            maximum_active,
            best_score,
        ):
            return
        if position == len(order):
            score = _objective(request, loads, total_flight_minutes)
            if score < best_score:
                best = list(assignment)
                best_score = score
            return
        task_index = order[position]
        group = request.candidate_groups[task_index]
        choices = sorted(
            enumerate(group),
            key=lambda choice: _projected_score(
                request, loads, counts, total_flight_minutes, choice[1]
            ),
        )
        for candidate_index, candidate in choices:
            if not _addition_allowed(request, loads, counts, candidate):
                continue
            opens_drone = candidate.drone_id not in active
            if opens_drone and len(active) >= maximum_active:
                continue
            assignment[task_index] = candidate_index
            turnaround = (
                _turnaround_minutes(request, candidate.drone_id)
                if counts[candidate.drone_id] > 0
                else 0
            )
            loads[candidate.drone_id] += candidate.mission.duration_minutes + turnaround
            counts[candidate.drone_id] += 1
            total_flight_minutes += candidate.mission.duration_minutes
            if opens_drone:
                active.add(candidate.drone_id)
            search(position + 1)
            if opens_drone:
                active.remove(candidate.drone_id)
            total_flight_minutes -= candidate.mission.duration_minutes
            counts[candidate.drone_id] -= 1
            loads[candidate.drone_id] -= candidate.mission.duration_minutes + turnaround

    search(0)
    if best is None:
        raise ValueError("No assignment found within fleet and resource limits.")
    return best


def _minimum_remaining_costs(
    request: AssignmentOptimizationRequest,
    order: list[int],
) -> list[float]:
    remaining = [0.0] * (len(order) + 1)
    for position in range(len(order) - 1, -1, -1):
        group = request.candidate_groups[order[position]]
        remaining[position] = remaining[position + 1] + min(
            candidate.mission.duration_minutes for candidate in group
        )
    return remaining


def _branch_cannot_improve(
    request: AssignmentOptimizationRequest,
    loads: dict[str, float],
    current_total_flight: float,
    minimum_remaining: float,
    maximum_active: int,
    best_score: Score,
) -> bool:
    optimistic_total = current_total_flight + minimum_remaining
    optimistic_completion = max(
        max(loads.values(), default=0),
        optimistic_total / maximum_active,
    )
    if request.criterion is OptimizationCriterion.COMPLETION_TIME:
        return optimistic_completion > best_score[0]
    if request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
        return optimistic_total > best_score[0]
    deadline = request.completion_deadline_minutes
    if deadline is None or best_score[0] > 0:
        return False
    return optimistic_completion > deadline or optimistic_total > best_score[1]


def _loads(
    request: AssignmentOptimizationRequest,
    assignment: Assignment,
) -> dict[str, float]:
    loads = {drone_id: 0.0 for drone_id in request.drone_ids}
    counts = {drone_id: 0 for drone_id in request.drone_ids}
    for group, candidate_index in zip(request.candidate_groups, assignment, strict=True):
        candidate = group[candidate_index]
        loads[candidate.drone_id] += candidate.mission.duration_minutes + (
            _turnaround_minutes(request, candidate.drone_id)
            if counts[candidate.drone_id] > 0
            else 0
        )
        counts[candidate.drone_id] += 1
    return loads


def _score(request: AssignmentOptimizationRequest, assignment: Assignment) -> Score:
    if request.resource_limits_by_drone:
        counts = dict.fromkeys(request.drone_ids, 0)
        minutes = dict.fromkeys(request.drone_ids, 0.0)
        for group, index in zip(request.candidate_groups, assignment, strict=True):
            candidate = group[index]
            counts[candidate.drone_id] += 1
            minutes[candidate.drone_id] += candidate.mission.duration_minutes
        if any(
            not limit.allows(counts[drone], minutes[drone])
            for drone, limit in request.resource_limits_by_drone.items()
        ):
            return INFEASIBLE_SCORE
    return _objective(
        request,
        _loads(request, assignment),
        _total_flight_minutes(request, assignment),
    )


def _addition_allowed(
    request: AssignmentOptimizationRequest,
    loads: dict[str, float],
    counts: dict[str, int],
    candidate: MissionAssignmentCandidate,
) -> bool:
    drone = candidate.drone_id
    limit = request.resource_limits_by_drone.get(drone)
    if limit is None:
        return True
    flight = loads[drone] - max(0, counts[drone] - 1) * _turnaround_minutes(request, drone)
    return limit.allows(counts[drone] + 1, flight + candidate.mission.duration_minutes)


def _objective(
    request: AssignmentOptimizationRequest,
    loads: dict[str, float],
    total_flight_minutes: float,
) -> Score:
    completion = max(loads.values(), default=0)
    total = total_flight_minutes
    active = float(sum(load > 0 for load in loads.values()))
    if request.criterion is OptimizationCriterion.COMPLETION_TIME:
        return completion, total, active, 0.0
    if request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
        return total, completion, active, 0.0
    deadline = request.completion_deadline_minutes
    if deadline is not None and completion <= deadline:
        return 0.0, total, completion, active
    overrun = completion - deadline if deadline is not None else completion
    return 1.0, overrun, total, active


def _total_flight_minutes(
    request: AssignmentOptimizationRequest,
    assignment: Assignment,
) -> float:
    return sum(
        group[candidate_index].mission.duration_minutes
        for group, candidate_index in zip(request.candidate_groups, assignment, strict=True)
    )


def _turnaround_minutes(request: AssignmentOptimizationRequest, drone_id: str) -> float:
    return request.turnaround_minutes_by_drone.get(drone_id, 0)
