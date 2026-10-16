from __future__ import annotations

from collections.abc import Callable
from math import exp
from random import Random
from time import perf_counter

from benchmarks.models import (
    BenchmarkAssignment,
    BenchmarkDrone,
    BenchmarkTask,
    OptimizationBenchmarkRequest,
    OptimizationBenchmarkResult,
)

Assignment = list[int]
AssignmentAlgorithm = Callable[[OptimizationBenchmarkRequest], Assignment]


def run_optimization_benchmark(
    request: OptimizationBenchmarkRequest,
) -> list[OptimizationBenchmarkResult]:
    algorithms: list[tuple[str, AssignmentAlgorithm]] = [
        ("round_robin", _round_robin),
        ("fastest_platform", _fastest_platform),
        ("cheapest_individual", _cheapest_individual),
        ("greedy_least_loaded", _greedy_least_loaded),
        ("lpt_balance", _lpt_balance),
        ("regret_insertion", _regret_insertion),
        ("local_search", _local_search),
        ("simulated_annealing", _simulated_annealing),
        ("genetic_algorithm", _genetic_algorithm),
        ("exact_branch_and_bound", _exact_branch_and_bound),
    ]
    return [_measure(request, name, algorithm) for name, algorithm in algorithms]


def _measure(
    request: OptimizationBenchmarkRequest,
    name: str,
    algorithm: AssignmentAlgorithm,
) -> OptimizationBenchmarkResult:
    started = perf_counter()
    assignment = algorithm(request)
    runtime_ms = (perf_counter() - started) * 1_000
    loads = _loads(request, assignment)
    return OptimizationBenchmarkResult(
        algorithm=name,
        completion_minutes=round(max(loads), 3),
        total_flight_minutes=round(sum(loads), 3),
        runtime_ms=round(runtime_ms, 3),
        assignments=[
            BenchmarkAssignment(
                drone_id=drone.drone_id,
                task_ids=[
                    task.task_id
                    for task, drone_index in zip(request.tasks, assignment, strict=True)
                    if drone_index == index
                ],
                duration_minutes=round(loads[index], 3),
            )
            for index, drone in enumerate(request.drones)
        ],
    )


def _round_robin(request: OptimizationBenchmarkRequest) -> Assignment:
    return [index % len(request.drones) for index in range(len(request.tasks))]


def _fastest_platform(request: OptimizationBenchmarkRequest) -> Assignment:
    fastest = max(
        range(len(request.drones)),
        key=lambda index: request.drones[index].cruise_speed_kmh,
    )
    return [fastest] * len(request.tasks)


def _cheapest_individual(request: OptimizationBenchmarkRequest) -> Assignment:
    return [
        min(
            range(len(request.drones)),
            key=lambda drone_index: _duration(task, request.drones[drone_index]),
        )
        for task in request.tasks
    ]


def _greedy_least_loaded(request: OptimizationBenchmarkRequest) -> Assignment:
    assignment = [0] * len(request.tasks)
    loads = [0.0] * len(request.drones)
    for task_index, task in enumerate(request.tasks):
        drone_index = min(range(len(request.drones)), key=lambda index: loads[index])
        assignment[task_index] = drone_index
        loads[drone_index] += _duration(task, request.drones[drone_index])
    return assignment


def _lpt_balance(request: OptimizationBenchmarkRequest) -> Assignment:
    order = sorted(
        range(len(request.tasks)),
        key=lambda index: min(_duration(request.tasks[index], drone) for drone in request.drones),
        reverse=True,
    )
    assignment = [0] * len(request.tasks)
    loads = [0.0] * len(request.drones)
    for task_index in order:
        task = request.tasks[task_index]
        drone_index = min(
            range(len(request.drones)),
            key=lambda index: loads[index] + _duration(task, request.drones[index]),
        )
        assignment[task_index] = drone_index
        loads[drone_index] += _duration(task, request.drones[drone_index])
    return assignment


def _regret_insertion(request: OptimizationBenchmarkRequest) -> Assignment:
    def regret(task: BenchmarkTask) -> float:
        costs = sorted(_duration(task, drone) for drone in request.drones)
        return costs[1] - costs[0]

    order = sorted(
        range(len(request.tasks)),
        key=lambda index: regret(request.tasks[index]),
        reverse=True,
    )
    assignment = [0] * len(request.tasks)
    loads = [0.0] * len(request.drones)
    for task_index in order:
        task = request.tasks[task_index]
        drone_index = min(
            range(len(request.drones)),
            key=lambda index: loads[index] + _duration(task, request.drones[index]),
        )
        assignment[task_index] = drone_index
        loads[drone_index] += _duration(task, request.drones[drone_index])
    return assignment


def _local_search(request: OptimizationBenchmarkRequest) -> Assignment:
    assignment = _lpt_balance(request)
    best_score = _score(request, assignment)
    improved = True
    while improved:
        improved = False
        for task_index in range(len(request.tasks)):
            original = assignment[task_index]
            for drone_index in range(len(request.drones)):
                if drone_index == original:
                    continue
                candidate = list(assignment)
                candidate[task_index] = drone_index
                score = _score(request, candidate)
                if score < best_score:
                    assignment = candidate
                    best_score = score
                    improved = True
                    break
            if improved:
                break
    return assignment


def _simulated_annealing(request: OptimizationBenchmarkRequest) -> Assignment:
    random = Random(request.random_seed)
    current = _lpt_balance(request)
    current_value = _weighted_score(request, current)
    best = list(current)
    best_value = current_value
    temperature = 20.0
    for _ in range(2_000):
        candidate = list(current)
        task_index = random.randrange(len(candidate))
        candidate[task_index] = random.randrange(len(request.drones))
        candidate_value = _weighted_score(request, candidate)
        delta = candidate_value - current_value
        if delta <= 0 or random.random() < exp(-delta / temperature):
            current = candidate
            current_value = candidate_value
        if current_value < best_value:
            best = list(current)
            best_value = current_value
        temperature = max(temperature * 0.996, 0.01)
    return best


def _genetic_algorithm(request: OptimizationBenchmarkRequest) -> Assignment:
    random = Random(request.random_seed)
    population_size = 60
    population = [
        [random.randrange(len(request.drones)) for _ in request.tasks]
        for _ in range(population_size - 2)
    ]
    population.extend([_lpt_balance(request), _regret_insertion(request)])
    for _ in range(120):
        population.sort(key=lambda assignment: _weighted_score(request, assignment))
        next_population = [list(assignment) for assignment in population[:10]]
        while len(next_population) < population_size:
            first = population[random.randrange(20)]
            second = population[random.randrange(20)]
            split = random.randrange(1, len(request.tasks))
            child = [*first[:split], *second[split:]]
            if random.random() < 0.25:
                child[random.randrange(len(child))] = random.randrange(len(request.drones))
            next_population.append(child)
        population = next_population
    return min(population, key=lambda assignment: _weighted_score(request, assignment))


def _exact_branch_and_bound(request: OptimizationBenchmarkRequest) -> Assignment:
    best = _lpt_balance(request)
    best_score = _score(request, best)
    order = sorted(
        range(len(request.tasks)),
        key=lambda index: min(_duration(request.tasks[index], drone) for drone in request.drones),
        reverse=True,
    )
    minimum_remaining = [0.0] * (len(order) + 1)
    for position in range(len(order) - 1, -1, -1):
        task = request.tasks[order[position]]
        minimum_remaining[position] = minimum_remaining[position + 1] + min(
            _duration(task, drone) for drone in request.drones
        )
    assignment = [0] * len(request.tasks)
    loads = [0.0] * len(request.drones)

    def search(position: int) -> None:
        nonlocal best, best_score
        optimistic_completion = max(
            max(loads),
            (sum(loads) + minimum_remaining[position]) / len(request.drones),
        )
        if optimistic_completion > best_score[0]:
            return
        if position == len(order):
            candidate_score = max(loads), sum(loads)
            if candidate_score < best_score:
                best = list(assignment)
                best_score = candidate_score
            return
        task_index = order[position]
        task = request.tasks[task_index]
        drone_order = sorted(
            range(len(request.drones)),
            key=lambda index: loads[index] + _duration(task, request.drones[index]),
        )
        for drone_index in drone_order:
            duration = _duration(task, request.drones[drone_index])
            assignment[task_index] = drone_index
            loads[drone_index] += duration
            search(position + 1)
            loads[drone_index] -= duration

    search(0)
    return best


def _duration(task: BenchmarkTask, drone: BenchmarkDrone) -> float:
    transit = task.transit_minutes_by_drone.get(drone.drone_id, 0)
    return task.distance_km / drone.cruise_speed_kmh * 60 + transit


def _loads(request: OptimizationBenchmarkRequest, assignment: Assignment) -> list[float]:
    loads = [0.0] * len(request.drones)
    for task, drone_index in zip(request.tasks, assignment, strict=True):
        loads[drone_index] += _duration(task, request.drones[drone_index])
    return loads


def _score(
    request: OptimizationBenchmarkRequest,
    assignment: Assignment,
) -> tuple[float, float]:
    loads = _loads(request, assignment)
    return max(loads), sum(loads)


def _weighted_score(request: OptimizationBenchmarkRequest, assignment: Assignment) -> float:
    completion, total = _score(request, assignment)
    return completion * 1_000 + total
