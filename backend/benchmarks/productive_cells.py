"""Fixed-track cell orders and whole-sortie neighborhoods; benchmark use only."""

from __future__ import annotations

from random import Random
from time import perf_counter

from benchmarks.productive_adaptive import adaptive
from benchmarks.productive_incremental import insertion_fast
from benchmarks.productive_model import CostModel, Solution, Trip, reverse
from benchmarks.productive_solvers import assign_trips
from geoplan.routing.ordering import cell_orders, order_cost, track_cells


def cells_adaptive(
    model: CostModel, initial: Solution, deadline: float, *, catalog_fraction: float = 0.6
) -> Solution:
    if perf_counter() >= deadline:
        return initial
    order = min(cell_orders(model), key=lambda o: (order_cost(model, o), o))
    return adaptive(model, initial, deadline, order=order, catalog_fraction=catalog_fraction)


def cells_sorties(model: CostModel, initial: Solution, deadline: float, seed: int) -> Solution:
    started = perf_counter()
    construction_deadline = started + min(2.0, max(0, deadline - started) * 0.8)
    candidate = cells_adaptive(model, initial, construction_deadline)
    return sortie_search(model, candidate, deadline, seed)


def destroy_sortie(model: CostModel, solution: Solution, index: int) -> tuple[set[int], Solution]:
    """Remove an entire sortie; every removed service task must be repaired once."""
    return {t // 2 for t in solution[index].visits}, solution[:index] + solution[index + 1 :]


def sortie_search(model: CostModel, initial: Solution, deadline: float, seed: int) -> Solution:
    """Whole-sortie reconstruction plus paired tail/group exchanges, strict acceptance.

    Repair inserts one removed task at a time, bounding the cost of rebuilding a
    large sortie. This is a different neighborhood, not an equivalent faster ALNS.
    """
    random = Random(seed)
    current = initial
    attempts = 0
    while perf_counter() < deadline:
        attempts += 1
        model.repair_attempts += 1
        if attempts % 12 == 0 and model.sortie_destroy_attempts < 2:
            # Short sorties are easiest to eliminate; randomization explores all.
            indices = sorted(range(len(current)), key=lambda i: len(current[i].visits))
            index = indices[random.randrange(max(1, (len(indices) + 1) // 2))]
            removed, candidate = destroy_sortie(model, current, index)
            model.sortie_destroy_attempts += 1
            tasks = sorted(removed)
            random.shuffle(tasks)
            repair_deadline = perf_counter() + max(0, deadline - perf_counter()) * 0.4
            for task in tasks:
                candidate = insertion_fast(model, {task}, candidate, repair_deadline)
                if not candidate:
                    break
            if candidate:
                model.sortie_repair_completed += 1
        elif len(current) >= 2:
            left, right = random.sample(range(len(current)), 2)
            a, b = current[left], current[right]
            cut_a, cut_b = random.randrange(len(a.visits) + 1), random.randrange(len(b.visits) + 1)
            av, bv = a.visits[:cut_a] + b.visits[cut_b:], b.visits[:cut_b] + a.visits[cut_a:]
            if attempts % 3 == 0:
                av = a.visits[:cut_a] + reverse(b.visits[cut_b:])
                bv = b.visits[:cut_b] + reverse(a.visits[cut_a:])
            rest = tuple(t for i, t in enumerate(current) if i not in (left, right))
            candidate = (*rest, *(Trip(p, v) for p, v in ((a.aircraft, av), (b.aircraft, bv)) if v))
            model.group_exchange_attempts += 1
        else:
            break
        if candidate and model.score(candidate) < model.score(current):
            current = candidate
        # Reassign accepted groups periodically without changing the incumbent on timeout.
        if attempts % 12 == 0 and perf_counter() < deadline:
            allocated = assign_trips(model, current, deadline)
            if model.score(allocated) < model.score(current):
                current = allocated
    return current


__all__ = [
    "cell_orders",
    "cells_adaptive",
    "cells_sorties",
    "destroy_sortie",
    "order_cost",
    "sortie_search",
    "track_cells",
]
