"""Fixed-work A/B experiment: shared vs per-aircraft geometry, equal cost semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from random import Random
from statistics import median
from time import perf_counter
from typing import TypedDict

from benchmarks.productive_cases import production_problems
from benchmarks.productive_model import CostModel
from benchmarks.productive_solvers import insertion


class Trial(TypedDict):
    workload: str
    repeat: int
    shared_geometry: bool
    elapsed_seconds: float
    geometry_builds: int
    service_evaluations: int
    result_sha256: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument(
        "--output", type=Path, default=Path("../docs/benchmark-results/productive-cache-ab")
    )
    args = parser.parse_args()
    if args.repeats < 1 or args.samples < 1:
        parser.error("Repeats and samples must be positive.")
    if (args.output / "results.json").exists():
        parser.error("Choose a fresh --output directory.")
    problem = next(
        p
        for p, _ in production_problems()
        if p.case_id == "two_restrictions" and p.criterion.value == "total_flight_time"
    )
    points = tuple(
        dict.fromkeys(
            (problem.aircraft[0].home, *(p for t in problem.tracks for p in (t.start, t.end)))
        )
    )
    pairs = Random(0).sample(
        [(a, b) for a in points for b in points if a != b],
        min(args.samples, len(points) * (len(points) - 1)),
    )
    rows: list[Trial] = []
    expected: dict[str, str] = {}
    for repeat in range(args.repeats):
        for workload in ("legs", "regret_complete"):
            # Alternate order to reduce the effect of thermal/system drift.
            for shared in (False, True) if repeat % 2 == 0 else (True, False):
                model = CostModel(problem, shared_geometry=shared)
                start = perf_counter()
                if workload == "legs":
                    values = [
                        model.leg(plane, a, b)
                        for a, b in pairs
                        for plane in range(len(problem.aircraft))
                    ]
                    elapsed = perf_counter() - start
                    result = repr(values)
                else:
                    solution = insertion(model, set(range(len(problem.tracks))), (), start + 60)
                    elapsed = perf_counter() - start
                    if not solution or not model.validate_geometry(solution):
                        raise RuntimeError(
                            "Fixed-work insertion did not finish with a valid solution."
                        )
                    result = repr((solution, model.metrics(solution)))
                digest = hashlib.sha256(result.encode()).hexdigest()
                if workload in expected and digest != expected[workload]:
                    raise AssertionError("A/B changes numerical costs or the completed solution.")
                expected[workload] = digest
                row: Trial = {
                    "workload": workload,
                    "repeat": repeat,
                    "shared_geometry": shared,
                    "elapsed_seconds": elapsed,
                    "geometry_builds": model.geometry_builds,
                    "service_evaluations": model.service_evaluations,
                    "result_sha256": digest,
                }
                rows.append(row)
                print(row, flush=True)
    speedups = {}
    for workload in expected:
        old = median(
            r["elapsed_seconds"]
            for r in rows
            if r["workload"] == workload and not r["shared_geometry"]
        )
        new = median(
            r["elapsed_seconds"] for r in rows if r["workload"] == workload and r["shared_geometry"]
        )
        speedups[workload] = {
            "unshared_median_seconds": old,
            "shared_median_seconds": new,
            "ratio": old / new,
        }
    args.output.mkdir(parents=True, exist_ok=True)
    source = {}
    for file in (
        Path(__file__),
        Path("benchmarks/productive_model.py"),
        Path("benchmarks/productive_solvers.py"),
        Path("benchmarks/productive_cases.py"),
    ):
        data = file.read_bytes()
        target = args.output / "source" / file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        source[file.name] = hashlib.sha256(data).hexdigest()
    (args.output / "results.json").write_text(
        json.dumps(
            {
                "case": problem.case_id,
                "tracks": len(problem.tracks),
                "pairs": len(pairs),
                "aircraft": len(problem.aircraft),
                "source_sha256": source,
                "rows": rows,
                "speedups": speedups,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(speedups, flush=True)


if __name__ == "__main__":
    main()
