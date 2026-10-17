"""Describe repeated fixed-budget experiments without selecting the best seed."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from math import ceil
from pathlib import Path
from random import Random
from statistics import median


def summarize(paths: list[Path]) -> dict:
    groups = defaultdict(list)
    hashes = {}
    algorithm_hashes = {}
    case_hashes = {}
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for case in report["cases"]:
            problem = case["problem"]
            key = problem["case_id"], problem["criterion"]
            digest = hashlib.sha256(json.dumps(problem, sort_keys=True).encode()).hexdigest()
            if key in case_hashes and case_hashes[key] != digest:
                raise ValueError(f"Compared series use different frozen problems: {key}")
            case_hashes[key] = digest
        for name, expected in report["source_sha256"].items():
            actual = hashlib.sha256((path.parent / "source" / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"Source snapshot checksum mismatch: {path}/{name}")
            if name in (
                "benchmarks/productive_cells.py",
                "benchmarks/productive_adaptive.py",
                "benchmarks/productive_model.py",
                "benchmarks/productive_incremental.py",
                "benchmarks/productive_solvers.py",
                "benchmarks/productive_cases.py",
                "benchmarks/productive_dataset.py",
                "benchmarks/run_productive_routing.py",
            ):
                if name in algorithm_hashes and algorithm_hashes[name] != expected:
                    raise ValueError(f"Compared series use different algorithm code: {name}")
                algorithm_hashes[name] = expected
        if report.get("input_report"):
            actual = hashlib.sha256(Path(report["input_report"]).read_bytes()).hexdigest()
            if actual != report["input_sha256"]:
                raise ValueError("Frozen input checksum mismatch.")
        for row in report["rows"]:
            if row["method"] not in ("baseline", "exact"):
                key = row["case"], row["criterion"], row["method"], report["seconds"]
                groups[key].append(row)
    summaries = []
    for key, rows in sorted(groups.items()):
        if len({r["seed"] for r in rows}) != len(rows):
            raise ValueError(f"Duplicate seed observations: {key}")
        valid = [r for r in rows if r["valid"]]
        entry = dict(zip(("case", "criterion", "method", "budget_seconds"), key, strict=True))
        entry.update(
            runs=len(rows),
            valid=len(valid),
            improved=sum(r["objective_improved"] for r in valid),
            catalog_complete=sum(r["catalog_complete"] for r in valid),
        )
        for field in (
            "total_gain_percent",
            "deadhead_gain_percent",
            "makespan_gain_percent",
            "elapsed_seconds",
            "sortie_repair_completed",
            "sortie_destroy_attempts",
            "group_exchange_attempts",
        ):
            values = sorted(r[field] for r in valid)
            if values:
                entry[field] = {
                    "median": median(values),
                    "min": values[0],
                    "max": values[-1],
                    "p95": values[ceil(0.95 * len(values)) - 1],
                }
        summaries.append(entry)
    paired = []
    for (case, criterion, method, budget), rows in sorted(groups.items()):
        if method != "cells_sorties":
            continue
        controls = {
            r["seed"]: r
            for r in groups.get((case, criterion, "alns_fast", budget), [])
            if r["valid"]
        }
        field = "makespan_gain_percent" if criterion == "completion_time" else "total_gain_percent"
        differences = [
            r[field] - controls[r["seed"]][field]
            for r in rows
            if r["valid"] and r["seed"] in controls
        ]
        if not differences:
            continue
        rng = Random(1729)
        samples = sorted(median(rng.choices(differences, k=len(differences))) for _ in range(2000))
        paired.append(
            {
                "case": case,
                "criterion": criterion,
                "budget_seconds": budget,
                "pairs": len(differences),
                "gain_field": field,
                "median_gain_difference_pp": median(differences),
                "seed_bootstrap_95_interval_pp": [samples[49], samples[1949]],
            }
        )
    return {
        "input_sha256": hashes,
        "algorithm_sha256": algorithm_hashes,
        "bootstrap_scope": (
            "seed variability on these fixtures only; not a geographic population interval"
        ),
        "summaries": summaries,
        "paired_cells_sorties_vs_alns_fast": paired,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a fresh file.")
    result = summarize(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
