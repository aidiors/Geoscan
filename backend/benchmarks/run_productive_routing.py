"""Run with uv run --directory backend python -m benchmarks.run_productive_routing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from benchmarks.productive_adaptive import adaptive
from benchmarks.productive_cases import (
    adaptive_problems,
    production_problems,
    scale_problems,
    sensitivity_problems,
    tiny_problems,
)
from benchmarks.productive_cells import cells_adaptive, cells_sorties, sortie_search
from benchmarks.productive_dataset import load_cases
from benchmarks.productive_incremental import insertion_fast
from benchmarks.productive_model import CostModel, Problem, Solution, Trip
from benchmarks.productive_solvers import (
    alns,
    endpoint_search,
    exact_small,
    insertion,
    local_search,
    savings_merge,
    split_order,
    split_reassign,
)
from geoplan.domain.enums import OptimizationCriterion

METHODS = (
    "endpoints",
    "regret2",
    "merge",
    "merge_singletons",
    "split",
    "split_reassign",
    "relocate",
    "alns",
    "regret2_fast",
    "alns_fast",
    "adaptive",
    "cells",
    "sorties",
    "cells_sorties",
    "cells_budgeted",
)


def run_method(model: CostModel, method: str, deadline: float, seed: int) -> Solution:
    baseline = model.problem.baseline
    if method == "endpoints":
        return endpoint_search(model, baseline, deadline)
    if method == "regret2":
        return insertion(model, set(range(len(model.problem.tracks))), (), deadline)
    if method == "regret2_fast":
        return insertion_fast(model, set(range(len(model.problem.tracks))), (), deadline)
    if method == "merge":
        return savings_merge(model, baseline, deadline)
    if method == "merge_singletons":
        # Full strip-level reconstruction; its initialization is inside the budget.
        singles = tuple(Trip(trip.aircraft, (token,)) for trip in baseline for token in trip.visits)
        singles = endpoint_search(model, singles, deadline)
        return savings_merge(model, singles, deadline)
    if method == "split":
        return split_order(model, tuple(t for trip in baseline for t in trip.visits), deadline)
    if method == "split_reassign":
        return split_reassign(model, baseline, deadline)
    if method == "relocate":
        return local_search(model, baseline, deadline)
    if method == "alns":
        return alns(model, baseline, deadline, seed)
    if method == "alns_fast":
        return alns(model, baseline, deadline, seed, repair=insertion_fast)
    if method == "adaptive":
        return adaptive(model, baseline, deadline)
    if method == "cells":
        return cells_adaptive(model, baseline, deadline)
    if method == "cells_budgeted":
        return cells_adaptive(model, baseline, deadline, catalog_fraction=0.85)
    if method == "sorties":
        return sortie_search(model, baseline, deadline, seed)
    if method == "cells_sorties":
        return cells_sorties(model, baseline, deadline, seed)
    raise ValueError(method)


def run(
    problem: Problem,
    seconds: float,
    seeds: int,
    oracle_seconds: float,
    methods: tuple[str, ...] = METHODS,
    deterministic_repeats: int = 1,
) -> list[dict]:
    rows = []
    base_model = CostModel(problem)
    base = base_model.metrics(problem.baseline)
    assert base is not None and base_model.validate_geometry(problem.baseline)
    oracle_score = None
    if len(problem.tracks) <= 6:
        start = perf_counter()
        solution, complete = exact_small(base_model, start + oracle_seconds)
        assert base_model.validate_geometry(solution)
        oracle = base_model.metrics(solution)
        assert oracle is not None
        rows.append(
            {
                "method": "exact",
                "seed": 0,
                "valid": True,
                "status": "optimal" if complete else "unproven_timeout",
                "elapsed_seconds": perf_counter() - start,
                **oracle,
                "solution": [asdict(trip) for trip in solution],
            }
        )
        if complete:
            oracle_score = base_model.score(solution)[0]
    rows.append(
        {
            "method": "baseline",
            "seed": 0,
            "valid": True,
            "status": "valid",
            "elapsed_seconds": 0,
            **base,
            "solution": [asdict(trip) for trip in problem.baseline],
        }
    )
    for method in methods:
        for seed in range(
            seeds
            if method in ("alns", "alns_fast", "sorties", "cells_sorties")
            else deterministic_repeats
        ):
            # Cold per-method caches; identical baseline validation is outside timed search.
            setup_start = perf_counter()
            model = CostModel(problem)
            assert model.validate_geometry(problem.baseline)
            setup_seconds = perf_counter() - setup_start
            start = perf_counter()
            solution = run_method(model, method, start + seconds, seed)
            elapsed = perf_counter() - start
            audit_start = perf_counter()
            valid = bool(solution) and model.validate_geometry(solution)
            metrics = model.metrics(solution) if valid else None
            row = {
                "method": method,
                "seed": seed,
                "valid": valid,
                "status": "valid" if valid else "no_complete_solution",
                "elapsed_seconds": elapsed,
                "budget_seconds": seconds,
                "setup_seconds": setup_seconds,
                "audit_seconds": perf_counter() - audit_start,
                "geometry_builds": model.geometry_builds,
                "service_evaluations": model.service_evaluations,
                "insertion_evaluations": model.insertion_evaluations,
                "repair_attempts": model.repair_attempts,
                "sortie_destroy_attempts": model.sortie_destroy_attempts,
                "sortie_repair_completed": model.sortie_repair_completed,
                "group_exchange_attempts": model.group_exchange_attempts,
                "catalog_arcs": model.catalog_arcs,
                "catalog_complete": model.catalog_complete,
                "decoded_candidates": model.decoded_candidates,
                "baseline_kept": solution == problem.baseline,
                "solution": [asdict(trip) for trip in solution],
            }
            if metrics is not None:
                row.update(metrics)
                row["total_gain_percent"] = 100 * (
                    1 - metrics["total_minutes"] / base["total_minutes"]
                )
                row["deadhead_gain_percent"] = (
                    100 * (1 - metrics["deadhead_minutes"] / base["deadhead_minutes"])
                    if base["deadhead_minutes"]
                    else 0
                )
                row["makespan_gain_percent"] = 100 * (
                    1 - metrics["load_makespan_minutes"] / base["load_makespan_minutes"]
                )
                row["objective_improved"] = model.score(solution) < model.score(problem.baseline)
                if oracle_score is not None and oracle_score > 0:
                    row["primary_gap_percent"] = 100 * (model.score(solution)[0] / oracle_score - 1)
            rows.append(row)
    return [
        {
            "case": problem.case_id,
            "criterion": problem.criterion.value,
            "tracks": len(problem.tracks),
            **row,
        }
        for row in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--deterministic-repeats", type=int, default=1)
    parser.add_argument("--oracle-seconds", type=float, default=10)
    parser.add_argument("--tiny-only", action="store_true")
    parser.add_argument(
        "--suite",
        choices=("small", "scale", "all", "tiny", "sensitivity", "adaptive"),
        default="small",
    )
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path, help="Replay cases from a previous results.json")
    parser.add_argument("--cases", nargs="+", help="Only these case IDs, with all their criteria")
    parser.add_argument("--criteria", nargs="+", choices=[c.value for c in OptimizationCriterion])
    args = parser.parse_args()
    if (
        args.seconds <= 0
        or args.seeds < 1
        or args.oracle_seconds <= 0
        or args.deterministic_repeats < 1
    ):
        parser.error("Budgets and seed count must be positive.")
    suite = "replay" if args.input else ("tiny" if args.tiny_only else args.suite)
    output = args.output or Path(f"../docs/benchmark-results/productive-routing-v3-{suite}")
    if (output / "results.json").exists():
        parser.error("Output already has results; choose a fresh --output directory.")
    output.mkdir(parents=True, exist_ok=True)
    generation_start = perf_counter()
    cases = (
        [
            (replace(p, criterion=c, deadline_minutes=12), {})
            for p in tiny_problems()
            for c in OptimizationCriterion
        ]
        if suite in ("small", "all", "tiny")
        else []
    )
    if args.input:
        cases = load_cases(args.input)
    if suite in ("small", "all"):
        cases.extend(production_problems())
    if suite in ("scale", "all"):
        cases.extend(scale_problems())
    if suite == "sensitivity":
        cases.extend(sensitivity_problems())
    if suite == "adaptive":
        cases.extend(adaptive_problems())
    if args.cases:
        found = {p.case_id for p, _ in cases}
        if set(args.cases) - found:
            parser.error("Some selected case IDs are absent from the dataset.")
        cases = [(p, m) for p, m in cases if p.case_id in args.cases]
    if args.criteria:
        cases = [(p, m) for p, m in cases if p.criterion.value in args.criteria]
    if not cases:
        parser.error("No cases match the selected filters.")
    generation_seconds = perf_counter() - generation_start
    hashes = {}
    for file in [
        *Path("benchmarks").glob("productive_*.py"),
        Path(__file__),
        Path("benchmarks/__init__.py"),
        Path("benchmarks/acceptance_scale.py"),
        Path("scenarios/coverage.py"),
        Path("scenarios/__init__.py"),
        *Path("src/geoplan").rglob("*.py"),
    ]:
        relative = Path("benchmarks") / file.name if file.is_absolute() else file
        data = file.read_bytes()
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    report: dict[str, Any] = {
        "created_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "shapely": version("shapely"),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "seconds": args.seconds,
        "seeds": args.seeds,
        "deterministic_repeats": args.deterministic_repeats,
        "oracle_seconds": args.oracle_seconds,
        "suite": suite,
        "input_report": str(args.input.resolve()) if args.input else None,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest() if args.input else None,
        "methods": args.methods,
        "case_generation_seconds": generation_seconds,
        "source_sha256": hashes,
        "scope": "fixed tracks, closed home sorties, load makespan; no 4D scheduler/terrain/export",
        "cases": [],
        "rows": [],
    }
    for problem, metadata in cases:
        print(
            f"{problem.case_id}/{problem.criterion.value}: {len(problem.tracks)} tracks", flush=True
        )
        report["cases"].append({"problem": asdict(problem), "production": metadata})
        report["rows"].extend(
            run(
                problem,
                args.seconds,
                args.seeds,
                args.oracle_seconds,
                tuple(args.methods),
                args.deterministic_repeats,
            )
        )
        (output / "results.json").write_text(
            json.dumps(
                report,
                default=_serialize,
                indent=2,
            ),
            encoding="utf-8",
        )
    rows = [{k: v for k, v in row.items() if k != "solution"} for row in report["rows"]]
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to {output.resolve()}", flush=True)


def _serialize(value: Any) -> Any:
    if isinstance(value, frozenset):
        return sorted(value)
    return value.model_dump(mode="json")


if __name__ == "__main__":
    main()
