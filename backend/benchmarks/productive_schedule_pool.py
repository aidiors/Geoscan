"""Compare distinct sortie duration targets through the real scheduler, benchmark only."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

import shapely

from benchmarks.productive_adaptive import decode, route_catalog
from benchmarks.productive_cells import cell_orders, order_cost
from benchmarks.productive_dataset import load_cases
from benchmarks.productive_model import CostModel, Solution
from benchmarks.productive_pipeline import check_pipeline, select_pipeline_result
from benchmarks.productive_solvers import assign_trips


@dataclass(frozen=True)
class PoolMember:
    target_minutes: float | None
    solution: Solution
    allocation: str = "load"


def duration_pool(
    model: CostModel,
    targets: list[float],
    catalog_seconds: float,
    assignment_seconds: float,
    allocations: tuple[str, ...] = ("load",),
) -> tuple[list[PoolMember], dict]:
    """Keep distinct feasible groups even when their load objective is worse.

    Targets constrain decoding on representative aircraft; assignment can change
    the duration. The actual longest sortie is reported separately. Physical
    endurance, eligibility and reserve remain authoritative in CostModel.
    """
    if any(value <= 0 for value in targets) or min(catalog_seconds, assignment_seconds) <= 0:
        raise ValueError("Targets and generation budgets must be positive.")
    if not allocations or set(allocations) - {"load", "catalog"}:
        raise ValueError("Select load and/or catalog allocation.")
    started = perf_counter()
    order = min(cell_orders(model), key=lambda value: (order_cost(model, value), value))
    catalog = route_catalog(model, order, perf_counter() + catalog_seconds)
    candidates, attempts, seen = [], [], set()
    for target in dict.fromkeys(targets):
        attempt = {"target_minutes": target}
        solution = decode(catalog, target, perf_counter() + assignment_seconds)
        if not solution:
            attempts.append({**attempt, "status": "no_complete_decode"})
            continue
        for allocation in dict.fromkeys(allocations):
            allocated = (
                assign_trips(model, solution, perf_counter() + assignment_seconds)
                if allocation == "load"
                else solution
            )
            label = {**attempt, "allocation": allocation}
            if not allocated:
                attempts.append({**label, "status": "assignment_failed_or_timed_out"})
                continue
            # Independently reconstruct safe paths before running the scheduler.
            if not model.validate_geometry(allocated):
                raise ValueError("Generated pool member failed independent feasibility validation.")
            if allocated in seen:
                attempts.append({**label, "status": "duplicate"})
                continue
            seen.add(allocated)
            candidates.append(PoolMember(target, allocated, allocation))
            attempts.append({**label, "status": "retained"})
    return candidates, {
        "generation_seconds": perf_counter() - started,
        "catalog_complete": model.catalog_complete,
        "catalog_arcs": model.catalog_arcs,
        "attempts": attempts,
    }


def _snapshot(output: Path, input_path: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    sources = [root / "pyproject.toml", root / "uv.lock"]
    for folder in ("src", "benchmarks", "scenarios"):
        sources.extend(sorted((root / folder).rglob("*.py")))
    for source in sources:
        relative = source.relative_to(root)
        target = output.with_suffix(".source") / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    return {
        "input": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "python": platform.python_version(),
        "shapely": shapely.__version__,
        "platform": platform.platform(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--criteria", nargs="+", default=["completion_time"])
    parser.add_argument("--targets", type=float, nargs="+", default=[60, 90, 120, 192])
    parser.add_argument("--allocations", nargs="+", choices=("load", "catalog"), default=["load"])
    parser.add_argument("--catalog-seconds", type=float, default=8)
    parser.add_argument("--assignment-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".source").exists():
        parser.error("Output or source snapshot already exists; choose a fresh file.")
    cases = [
        (problem, metadata)
        for problem, metadata in load_cases(args.input)
        if (not args.cases or problem.case_id in args.cases)
        and problem.criterion.value in args.criteria
    ]
    if not cases:
        parser.error("No matching frozen cases.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = {
        **_snapshot(args.output, args.input),
        "parameters": {
            "targets": args.targets,
            "catalog_seconds": args.catalog_seconds,
            "assignment_seconds": args.assignment_seconds,
            "allocations": args.allocations,
        },
        "scope": "fixed tracks; unchanged physical limits; bench only; shared-base fixtures",
        "rows": [],
        "cases": [],
    }

    def save() -> None:
        args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    for problem, metadata in cases:
        started = perf_counter()
        key = {"case": problem.case_id, "criterion": problem.criterion.value}
        model = CostModel(problem)
        print(f"Generate {key}", flush=True)
        pool, generation = duration_pool(
            model,
            args.targets,
            args.catalog_seconds,
            args.assignment_seconds,
            tuple(args.allocations),
        )
        case = {**key, **generation}
        output["cases"].append(case)
        checked = []
        save()
        for member in [PoolMember(None, problem.baseline, "baseline"), *pool]:
            solution = member.solution
            print(f"Schedule {key}/{member.target_minutes}/{member.allocation}", flush=True)
            costs = [model.trip(trip) for trip in solution]
            assert all(cost is not None for cost in costs)
            row = {
                **key,
                "target_minutes": member.target_minutes,
                "allocation": member.allocation,
                "solution": [asdict(trip) for trip in solution],
                "metrics": model.metrics(solution),
                "longest_sortie_minutes": max(cost.total for cost in costs if cost is not None),
                **check_pipeline(problem, metadata, solution),
            }
            checked.append(row)
            output["rows"].append(row)
            print(
                f"  actual={row['scheduled_makespan_minutes']:.3f} "
                f"ready={row['integration_ready']} seconds={row['elapsed_seconds']:.2f}",
                flush=True,
            )
            save()
        chosen = select_pipeline_result(checked[0], checked[1:])
        case.update(
            selected_target_minutes=chosen["target_minutes"],
            selected_allocation=chosen["allocation"],
            baseline_kept=chosen is checked[0],
            scheduled_objective=chosen["scheduled_objective"],
            scheduled_makespan_minutes=chosen["scheduled_makespan_minutes"],
            elapsed_seconds=perf_counter() - started,
        )
        save()


if __name__ == "__main__":
    main()
