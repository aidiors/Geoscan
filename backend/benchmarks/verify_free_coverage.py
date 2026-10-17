"""Verify a saved run using its archived source and independently replayed routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path


def verify_manifest(directory: Path) -> dict:
    report = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256((directory / "inputs.json").read_bytes()).hexdigest()
        != report["inputs_sha256"]
    ):
        raise ValueError("Frozen inputs hash mismatch")
    for name, expected in report["source_sha256"].items():
        file = directory / "source" / name
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Archived source hash mismatch: {name}")
    return report


def restore_route(route_type, stored):
    return route_type(
        stored["family"],
        tuple(
            tuple(tuple(tuple(point) for point in stroke) for stroke in sortie)
            for sortie in stored["sorties"]
        ),
        stored["scan_connections"],
    )


def compare(stored, actual):
    for key, value in actual.items():
        equal = (
            math.isclose(value, stored[key], rel_tol=1e-9, abs_tol=1e-7)
            if isinstance(value, float)
            else value == stored[key]
        )
        if not equal:
            raise ValueError(f"Replay mismatch: {stored.get('case_id')}/{stored['family']}/{key}")


def verify_timing(directory, models):
    from shapely.geometry import LineString

    from benchmarks.free_coverage_frames import retime
    from benchmarks.free_coverage_overlap import analyze
    from geoplan.routing.freeform import Route, evaluate_flights, route_legs

    diagnostic = json.loads((directory / "retiming-diagnostic.json").read_text(encoding="utf-8"))
    checked = rejected = 0
    for row in diagnostic:
        model = models[row["case_id"]]
        route = restore_route(Route, row["route"])
        replay = retime(model, route)
        compare(
            row,
            {
                "frames_planned": replay.frames_planned,
                "slowed_intervals": replay.slowed_intervals,
                "reason": replay.reason,
            },
        )
        if replay.flights is None:
            if "flights" in row or replay.reason is None:
                raise ValueError("Invalid timing rejection")
            rejected += 1
            continue
        flights = replay.flights
        stored = json.loads(json.dumps([[asdict(leg) for leg in f] for f in flights]))
        if stored != row["flights"]:
            raise ValueError("Replayed timing differs")
        metrics = evaluate_flights(model, route.family, flights)
        photo = analyze(model, stored)
        compare(row, {**metrics, **photo})
        if (
            metrics["images"] != photo["images_reconstructed"]
            or metrics["images"] != replay.frames_planned
        ):
            raise ValueError("Retimed camera reconstruction differs")
        for before, after in zip(route_legs(model, route), flights, strict=True):
            a = LineString([before[0].start, *(leg.end for leg in before)])
            b = LineString([after[0].start, *(leg.end for leg in after)])
            if a.hausdorff_distance(b) > 1e-5 or abs(a.length - b.length) > 1e-3:
                raise ValueError("Retiming changed geometry")
            if [asdict(leg) for leg in before if leg.phase != "survey"] != [
                asdict(leg) for leg in after if leg.phase != "survey"
            ]:
                raise ValueError("Retiming changed camera-off legs")
        minimum = min(
            math.dist(leg.start, leg.end) / leg.minutes * 0.06
            for f in flights
            for leg in f
            if leg.phase == "survey"
        )
        compare(row, {"minimum_survey_ground_speed_kmh": minimum})
        checked += 1
    return {"timing_routes_replayed": checked, "timing_rejections_replayed": rejected}


def verify(directory: Path) -> dict:
    report = verify_manifest(directory)
    # Run this verifier in a fresh process: imported planning/benchmark modules
    # must come from the archived tree, not the current checkout.
    if any(name.startswith(("geoplan.", "benchmarks.free_coverage")) for name in sys.modules):
        raise RuntimeError("Replay requires a fresh Python process")
    sys.path[:0] = [str(directory / "source"), str(directory / "source" / "src")]
    from benchmarks.free_coverage_overlap import analyze
    from geoplan.routing.freeform import GeometryModel, Route, evaluate
    from scenarios.freeform import load_inputs

    frozen = load_inputs(directory / "inputs.json")
    models = {case.case_id: GeometryModel(case) for case in frozen}
    checked = 0
    for row in report["results"]:
        route = restore_route(Route, row["route"])
        metrics, flights = evaluate(models[row["case_id"]], route)
        for key, actual in metrics.items():
            expected = row[key]
            if isinstance(actual, float):
                equal = math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-7)
            else:
                equal = actual == expected
            if not equal:
                raise ValueError(f"Replay mismatch: {row['case_id']}/{route.family}/{key}")
        geometry = json.loads(json.dumps([[asdict(leg) for leg in flight] for flight in flights]))
        if geometry != row["flights"]:
            raise ValueError("Replayed stage geometry or timing differs")
        photo = analyze(models[row["case_id"]], row["flights"])
        compare(
            row,
            {
                "optional_pair_overlap_passed": photo["pair_overlap_passed"],
                "minimum_pair_intersection_percent": photo["minimum_pair_intersection_percent"],
                "pair_intersection_violations": photo["pairs_below_requested_overlap"],
            },
        )
        if photo["images_reconstructed"] != metrics["images"]:
            raise ValueError("Independent camera clock differs")
        checked += 1
    for summary in report["summary"]:
        rows = [r for r in report["results"] if r["case_id"] == summary["case_id"]]
        feasible = [r for r in rows if r["screen_passed"]]
        best = (
            min(feasible, key=lambda r: (r["flight_minutes"], r["deadhead_minutes"]))
            if feasible
            else None
        )
        if (best["family"] if best else None) != summary["best_family"]:
            raise ValueError("Winner differs from the original flight-time criterion")
        baseline = next(r for r in rows if r["family"] == "production_reference")
        candidates = [
            r for r in feasible if r["flight_minutes"] <= baseline["flight_minutes"] + 1e-8
        ]
        productive = (
            min(candidates, key=lambda r: r["nonproductive_time_proxy_minutes"])
            if candidates
            else None
        )
        if (productive["family"] if productive else None) != summary[
            "best_nonproductive_proxy_without_longer_flight"
        ]:
            raise ValueError("Productivity diagnostic winner differs")
        protected = (
            [
                row
                for row in feasible
                if all(
                    row[key] <= baseline[key] + 1e-8
                    for key in (
                        "flight_minutes",
                        "deadhead_minutes",
                        "departure_minutes",
                        "return_minutes",
                    )
                )
            ]
            if baseline["screen_passed"]
            else []
        )
        endpoint_best = (
            min(
                protected,
                key=lambda row: (
                    row["flight_minutes"],
                    row["deadhead_minutes"],
                ),
            )
            if protected
            else None
        )
        if (endpoint_best["family"] if endpoint_best else None) != summary[
            "best_endpoint_protected_family"
        ]:
            raise ValueError("Endpoint-protected selection differs")
    result = {
        "inputs_and_source_hashes": "passed",
        "routes_replayed": checked,
        "selection": "passed",
        **verify_timing(directory, models),
    }
    (directory / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.directory.resolve()), indent=2))
