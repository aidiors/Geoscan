"""CP-SAT solver for assignment of prevalidated complete sorties.

No geometry, battery or safety constraints are invented here: admissible missions
come from the caller. This is assignment of complete sorties, not VRP routing.
Lexicographic objectives match HybridAssignmentOptimizer. Proofs concern the
integer model only; all reported costs are recomputed from original durations.
"""

from dataclasses import dataclass
from math import ceil, floor, isfinite
from time import perf_counter

from ortools.sat.python import cp_model

from geoplan.assignment.models import AssignmentOptimizationRequest
from geoplan.domain.enums import OptimizationCriterion


@dataclass(frozen=True)
class AssignmentEvaluation:
    choices: tuple[int, ...]
    total_minutes: float
    completion_minutes: float
    active_drones: int
    score: tuple[float, ...]


def objective_score(
    request: AssignmentOptimizationRequest, total: float, completion: float, active: int
) -> tuple[float, ...]:
    if request.criterion is OptimizationCriterion.COMPLETION_TIME:
        return completion, total, float(active), 0.0
    if request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
        return total, completion, float(active), 0.0
    deadline = request.completion_deadline_minutes
    if deadline is not None and completion <= deadline:
        return 0.0, total, completion, float(active)
    return 1.0, completion - (deadline or 0), total, float(active)


def evaluate_assignment(
    request: AssignmentOptimizationRequest, choices: tuple[int, ...]
) -> AssignmentEvaluation:
    if len(choices) != len(request.candidate_groups):
        raise ValueError("Every task must have exactly one selection")
    loads = dict.fromkeys(request.drone_ids, 0.0)
    counts = dict.fromkeys(request.drone_ids, 0)
    total = 0.0
    for group, index in zip(request.candidate_groups, choices, strict=True):
        if not 0 <= index < len(group):
            raise ValueError("Candidate index outside its group")
        candidate = group[index]
        drone = candidate.drone_id
        duration = candidate.mission.duration_minutes
        loads[drone] += duration + (
            request.turnaround_minutes_by_drone.get(drone, 0) if counts[drone] else 0
        )
        counts[drone] += 1
        total += duration
    active = sum(count > 0 for count in counts.values())
    for drone, limit in request.resource_limits_by_drone.items():
        flight = loads[drone] - max(0, counts[drone] - 1) * request.turnaround_minutes_by_drone.get(
            drone, 0
        )
        if not limit.allows(counts[drone], flight):
            raise ValueError("Aircraft resource limit exceeded")
    if active > (request.maximum_active_drones or len(request.drone_ids)):
        raise ValueError("Active fleet limit exceeded")
    completion = max(loads.values())
    score = objective_score(request, total, completion, active)
    return AssignmentEvaluation(choices, total, completion, active, score)


@dataclass(frozen=True)
class CpSatAssignmentResult:
    evaluation: AssignmentEvaluation | None
    statuses: tuple[str, ...]
    proven_lexicographic: bool
    runtime_seconds: float
    stages: tuple[dict[str, float | str], ...]
    precision_units_per_minute: int
    maximum_time_rounding_error_minutes: float
    hint_supplied: bool


@dataclass(frozen=True)
class CpSatAssignmentSolver:
    time_limit_seconds: float = 2.0
    units_per_minute: int = 1_000_000
    random_seed: int = 0

    def solve(
        self, request: AssignmentOptimizationRequest, initial_choices: tuple[int, ...] | None = None
    ) -> CpSatAssignmentResult:
        if not isfinite(self.time_limit_seconds) or self.time_limit_seconds <= 0:
            raise ValueError("Positive finite time budget required")
        if self.units_per_minute <= 0:
            raise ValueError("Positive precision required")
        started = perf_counter()
        model, selections, objectives = self._model(request)
        if initial_choices is not None:
            evaluate_assignment(request, initial_choices)
            for group, selected in zip(selections, initial_choices, strict=True):
                for index, variable in enumerate(group):
                    model.add_hint(variable, int(index == selected))
        statuses, stages = [], []
        choices = None
        for objective in objectives:
            remaining = self.time_limit_seconds - (perf_counter() - started)
            if remaining <= 0:
                break
            model.minimize(objective)
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = self.random_seed
            status = solver.solve(model)
            name = solver.status_name(status)
            statuses.append(name)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            choices = tuple(
                next(index for index, variable in enumerate(group) if solver.value(variable))
                for group in selections
            )
            stages.append(
                {
                    "status": name,
                    "objective": solver.objective_value,
                    "bound": solver.best_objective_bound,
                }
            )
            if status != cp_model.OPTIMAL:
                break
            model.add(objective == solver.value(objective))
            model.clear_hints()
            for group in selections:
                for variable in group:
                    model.add_hint(variable, solver.value(variable))
        return CpSatAssignmentResult(
            evaluation=evaluate_assignment(request, choices) if choices is not None else None,
            statuses=tuple(statuses),
            proven_lexicographic=(
                len(statuses) == len(objectives) and all(s == "OPTIMAL" for s in statuses)
            ),
            runtime_seconds=perf_counter() - started,
            stages=tuple(stages),
            precision_units_per_minute=self.units_per_minute,
            maximum_time_rounding_error_minutes=(len(selections) * 2 + 1)
            / (2 * self.units_per_minute),
            hint_supplied=initial_choices is not None,
        )

    def _model(self, request: AssignmentOptimizationRequest):
        model = cp_model.CpModel()
        scale = self.units_per_minute
        selections = [
            [model.new_bool_var(f"x_{i}_{j}") for j in range(len(group))]
            for i, group in enumerate(request.candidate_groups)
        ]
        for group in selections:
            model.add_exactly_one(group)
        durations = [
            [round(candidate.mission.duration_minutes * scale) for candidate in group]
            for group in request.candidate_groups
        ]
        turns = {
            d: round(request.turnaround_minutes_by_drone.get(d, 0) * scale)
            for d in request.drone_ids
        }
        horizon = sum(max(group) for group in durations) + max(turns.values()) * len(selections)
        if horizon > 10**15:
            raise ValueError("Assignment exceeds safe integer model range")
        loads, active = [], []
        for drone in request.drone_ids:
            terms = [
                (selections[i][j], durations[i][j])
                for i, group in enumerate(request.candidate_groups)
                for j, candidate in enumerate(group)
                if candidate.drone_id == drone
            ]
            used = model.new_bool_var(f"used_{drone}")
            count = sum(variable for variable, _ in terms)
            limit = request.resource_limits_by_drone.get(drone)
            if limit is not None:
                if limit.maximum_sorties is not None:
                    model.add(count <= limit.maximum_sorties)
                if limit.maximum_flight_minutes is not None:
                    # Round consumption up and capacity down: never manufacture
                    # usable service life through integer-time quantization.
                    flight_terms = [
                        selections[i][j] * ceil(candidate.mission.duration_minutes * scale)
                        for i, group in enumerate(request.candidate_groups)
                        for j, candidate in enumerate(group)
                        if candidate.drone_id == drone
                    ]
                    model.add(sum(flight_terms) <= floor(limit.maximum_flight_minutes * scale))
            model.add(count >= used)
            model.add(count <= len(selections) * used)
            load = model.new_int_var(0, horizon, f"load_{drone}")
            model.add(
                load
                == sum(variable * cost for variable, cost in terms) + turns[drone] * (count - used)
            )
            loads.append(load)
            active.append(used)
        active_count = model.new_int_var(1, len(active), "active")
        model.add(active_count == sum(active))
        model.add(active_count <= (request.maximum_active_drones or len(active)))
        total = model.new_int_var(0, horizon, "total")
        model.add(
            total
            == sum(
                variable * duration
                for group, costs in zip(selections, durations, strict=True)
                for variable, duration in zip(group, costs, strict=True)
            )
        )
        completion = model.new_int_var(0, horizon, "completion")
        model.add_max_equality(completion, loads)
        if request.criterion is OptimizationCriterion.COMPLETION_TIME:
            objectives = [completion, total, active_count]
        elif request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
            objectives = [total, completion, active_count]
        elif request.completion_deadline_minutes is None:
            objectives = [completion, total, active_count]
        else:
            deadline = round(request.completion_deadline_minutes * scale)
            late = model.new_bool_var("late")
            model.add(completion > deadline).only_enforce_if(late)
            model.add(completion <= deadline).only_enforce_if(late.negated())
            primary = model.new_int_var(0, horizon, "conditional_primary")
            secondary = model.new_int_var(0, horizon, "conditional_secondary")
            model.add(primary == completion - deadline).only_enforce_if(late)
            model.add(primary == total).only_enforce_if(late.negated())
            model.add(secondary == total).only_enforce_if(late)
            model.add(secondary == completion).only_enforce_if(late.negated())
            objectives = [late, primary, secondary, active_count]
        return model, selections, objectives
