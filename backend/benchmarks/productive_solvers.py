"""Experimental construction/repair heuristics for a finite fixed-track model."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from itertools import permutations, product
from math import inf
from random import Random
from time import perf_counter

from benchmarks.productive_model import CostModel, Solution, Trip, reverse, variants
from geoplan.routing.allocation import assign_trips


def endpoint_search(model: CostModel, initial: Solution, deadline: float) -> Solution:
    current = initial
    for index, trip in enumerate(current):
        if perf_counter() >= deadline:
            break
        options = [
            (*current[:index], Trip(plane, visits), *current[index + 1 :])
            for plane in range(len(model.problem.aircraft))
            for visits in variants(trip.visits)
        ]
        current = min([current, *options], key=model.score)
    return current


def insertion(
    model: CostModel, remaining: set[int], initial: Solution, deadline: float, regret: int = 2
) -> Solution:
    current = initial
    while remaining:
        if perf_counter() >= deadline:
            return ()
        choices: list[tuple[tuple[float, ...], int, Solution]] = []
        for task in sorted(remaining):
            options: list[tuple[tuple[float, ...], Solution]] = []
            for plane in range(len(model.problem.aircraft)):
                placements: list[Solution] = []
                for orientation in (0, 1):
                    trip = Trip(plane, (2 * task + orientation,))
                    if model.trip(trip) is not None:
                        candidate = (*current, trip)
                        placements.append(candidate)
                if placements:
                    candidate = min(placements, key=lambda s: model.score(s, complete=False))
                    options.append((model.score(candidate, complete=False), candidate))
            for index, trip in enumerate(current):
                placements = []
                for position in range(len(trip.visits) + 1):
                    for orientation in (0, 1):
                        visits = (
                            *trip.visits[:position],
                            2 * task + orientation,
                            *trip.visits[position:],
                        )
                        replacement = Trip(trip.aircraft, visits)
                        if model.trip(replacement) is not None:
                            candidate = (*current[:index], replacement, *current[index + 1 :])
                            placements.append(candidate)
                if placements:
                    candidate = min(placements, key=lambda s: model.score(s, complete=False))
                    options.append((model.score(candidate, complete=False), candidate))
            if not options:
                return ()
            options.sort(key=lambda item: item[0])
            gap = tuple(
                worst - best
                for worst, best in zip(
                    options[min(regret - 1, len(options) - 1)][0],
                    options[0][0],
                    strict=True,
                )
            )
            # Few choices are considered first; no infinite penalty arithmetic.
            priority = (inf,) * len(gap) if len(options) < regret else gap
            choices.append((priority, task, options[0][1]))
            if perf_counter() >= deadline:
                return ()
        _, task, current = max(choices, key=lambda item: (item[0], -item[1]))
        remaining.remove(task)
    return current


def savings_merge(model: CostModel, initial: Solution, deadline: float) -> Solution:
    """MEM-inspired full-cost merge; not the original homogeneous MEM algorithm."""
    current = initial
    while perf_counter() < deadline:
        best = current
        best_score = model.score(current)
        for left in range(len(current)):
            for right in range(left + 1, len(current)):
                first, second = current[left].visits, current[right].visits
                orders = {a + b for a in (first, reverse(first)) for b in (second, reverse(second))}
                orders |= {
                    b + a for a in (first, reverse(first)) for b in (second, reverse(second))
                }
                rest = tuple(trip for i, trip in enumerate(current) if i not in (left, right))
                for plane in range(len(model.problem.aircraft)):
                    for visits in sorted(orders):
                        if model.trip(Trip(plane, visits)) is None:
                            continue
                        candidate = (*rest, Trip(plane, visits))
                        score = model.score(candidate)
                        if score < best_score:
                            best, best_score = candidate, score
                if perf_counter() >= deadline:
                    return best
        if best == current:
            break
        current = best
    return current


def split_order(model: CostModel, order: tuple[int, ...], deadline: float) -> Solution:
    """Exact additive-cost split of this oriented order, NOT exact finite-fleet scheduling."""
    n = len(order)
    costs = [0.0, *([inf] * n)]
    paths: list[Solution] = [()] * (n + 1)
    prefix = [0.0]
    for token in order:
        prefix.append(prefix[-1] + model.track_distances[token // 2])
    for end in range(1, n + 1):
        # Service distance alone is a lower bound for every internal path.
        minimum_start = bisect_left(
            prefix,
            prefix[end] - (model.problem.maximum_internal_distance_km or inf) - 1e-8,
            0,
            end,
        )
        for start in range(minimum_start, end):
            if perf_counter() >= deadline:
                return ()
            if costs[start] == inf:
                continue
            for plane in range(len(model.problem.aircraft)):
                for visits in (order[start:end], reverse(order[start:end])):
                    trip = Trip(plane, visits)
                    cost = model.trip(trip)
                    if cost is not None and costs[start] + cost.total < costs[end]:
                        costs[end] = costs[start] + cost.total
                        paths[end] = (*paths[start], trip)
    return paths[n]


def split_reassign(model: CostModel, initial: Solution, deadline: float) -> Solution:
    """60% of the remaining budget for Split, rest for allocation; keep a valid incumbent."""
    now = perf_counter()
    split = split_order(
        model, tuple(t for trip in initial for t in trip.visits), now + max(0, deadline - now) * 0.6
    )
    best = initial
    if split:
        assigned = assign_trips(model, split, deadline)
        best = min((initial, split, assigned), key=model.score)
    return best


def local_search(model: CostModel, initial: Solution, deadline: float) -> Solution:
    current = initial
    while perf_counter() < deadline:
        improved = False
        for source, source_trip in enumerate(current):
            for position, token in enumerate(source_trip.visits):
                remaining = (*source_trip.visits[:position], *source_trip.visits[position + 1 :])
                rest = tuple(
                    Trip(trip.aircraft, remaining) if i == source else trip
                    for i, trip in enumerate(current)
                    if i != source or remaining
                )
                for target, target_trip in enumerate(rest):
                    for slot in range(len(target_trip.visits) + 1):
                        for moved in (token, token ^ 1):
                            replacement = Trip(
                                target_trip.aircraft,
                                (*target_trip.visits[:slot], moved, *target_trip.visits[slot:]),
                            )
                            if model.trip(replacement) is None:
                                continue
                            candidate = (*rest[:target], replacement, *rest[target + 1 :])
                            if model.score(candidate) < model.score(current):
                                current, improved = candidate, True
                                break
                        if improved:
                            break
                    if improved or perf_counter() >= deadline:
                        break
                if improved or perf_counter() >= deadline:
                    break
            if improved or perf_counter() >= deadline:
                break
        if not improved:
            break
    return current


def alns(
    model: CostModel,
    initial: Solution,
    deadline: float,
    seed: int,
    *,
    repair: Callable[[CostModel, set[int], Solution, float, int], Solution] = insertion,
) -> Solution:
    """Bounded random/worst-connector repair, adaptive operator selection, feasible incumbent."""
    random = Random(seed)
    current = best = initial
    weights = [1.0, 1.0]
    while perf_counter() < deadline:
        operator = random.choices(range(2), weights)[0]
        tasks = list(range(len(model.problem.tracks)))
        count = min(len(tasks), max(1, min(6, len(tasks) // 4)))
        if operator == 0:
            removed = set(random.sample(tasks, count))
        else:
            expensive = []
            for trip in current:
                previous = model.problem.aircraft[trip.aircraft].home
                for token in trip.visits:
                    start, end = model.endpoints(token)
                    expensive.append((model.leg(trip.aircraft, previous, start)[0], token // 2))
                    previous = end
            removed = {task for _, task in sorted(expensive, reverse=True)[:count]}
        partial = tuple(
            Trip(trip.aircraft, tuple(t for t in trip.visits if t // 2 not in removed))
            for trip in current
            if any(t // 2 not in removed for t in trip.visits)
        )
        model.repair_attempts += 1
        candidate = repair(model, removed, partial, deadline, random.choice((2, 3)))
        reward = 0.1
        if candidate and model.score(candidate) < model.score(current):
            current, reward = candidate, 1.0
            if model.score(current) < model.score(best):
                best, reward = current, 3.0
        weights[operator] = weights[operator] * 0.8 + reward * 0.2
    return best


def exact_small(model: CostModel, deadline: float) -> tuple[Solution, bool]:
    """Enumerate every oriented trip, then all task partitions/aircraft assignments."""
    n = len(model.problem.tracks)
    if n > 6:
        raise ValueError("Full enumeration is restricted to six tracks.")
    # At fixed subset and aircraft, a shorter feasible trip dominates every longer
    # one in this additive model (no time windows or inter-trip collision constraints).
    cheapest: dict[tuple[int, int], tuple[tuple[float, float], Trip]] = {}
    for size in range(1, n + 1):
        for order in permutations(range(n), size):
            mask = sum(1 << task for task in order)
            for orientation in product((0, 1), repeat=size):
                visits = tuple(
                    2 * task + flag for task, flag in zip(order, orientation, strict=True)
                )
                for plane in range(len(model.problem.aircraft)):
                    trip = Trip(plane, visits)
                    cost = model.trip(trip)
                    if cost is not None:
                        key = mask, plane
                        value = cost.total, cost.deadhead
                        if key not in cheapest or value < cheapest[key][0]:
                            cheapest[key] = value, trip
                if perf_counter() >= deadline:
                    return model.problem.baseline, False
    by_task: dict[int, list[tuple[int, Trip]]] = {task: [] for task in range(n)}
    for (mask, _), (_, trip) in cheapest.items():
        by_task[(mask & -mask).bit_length() - 1].append((mask, trip))
    best = model.problem.baseline
    best_score = model.score(best)
    complete = True

    def search(mask: int, solution: Solution) -> None:
        nonlocal best, best_score, complete
        if perf_counter() >= deadline:
            complete = False
            return
        if not mask:
            score = model.score(solution)
            if score < best_score:
                best, best_score = solution, score
            return
        task = (mask & -mask).bit_length() - 1
        for subset, trip in by_task[task]:
            if subset & mask == subset:
                search(mask ^ subset, (*solution, trip))
                if not complete:
                    return

    search((1 << n) - 1, ())
    return best, complete
