"""Separate requested coverage settings from an optional frame-pair diagnostic."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from benchmarks.free_coverage_frames import retime
from benchmarks.free_coverage_overlap import analyze
from benchmarks.free_coverage_reference import candidates
from benchmarks.free_coverage_selection import endpoint_protected_choice
from geoplan.routing.coverage_patterns import shell_routes
from geoplan.routing.freeform import GeometryModel, Route, evaluate, evaluate_flights
from scenarios.freeform import dataset, load_inputs


def route_candidates(model: GeometryModel) -> tuple[Route, ...]:
    """Compare reference methods, closed shells and endpoint-aware split shells."""
    pool = list(candidates(model))
    for layers in (1, 3, 5, 8, 9):
        for angle in (0, 90):
            for split in (False, True):
                pool.extend(shell_routes(model, layers, angle, split=split))
    return tuple(pool)


def run(
    inputs: Path | None,
    output: Path,
    profiles: list[tuple[float, float]] | None,
    selected=None,
    *,
    suite: str = "holdout",
    trigger_interval_s: float = 1,
    timing: bool = False,
) -> dict:
    source_cases = load_inputs(inputs) if inputs is not None else dataset(suite, trigger_interval_s)
    if selected:
        unknown = set(selected) - {case.case_id for case in source_cases}
        if unknown:
            raise ValueError(f"Unknown cases: {sorted(unknown)}")
    if inputs is not None and profiles is not None:
        raise ValueError("Frozen inputs already define their quality profiles")
    if inputs is None and not profiles:
        raise ValueError("Built-in datasets require quality profiles")
    configured_profiles = profiles or [
        (
            case.request.parameters.side_overlap_percent,
            case.request.parameters.forward_overlap_percent,
        )
        for case in source_cases
    ]
    if any(not (50 <= side < 95 and 0 <= front < 95) for side, front in configured_profiles):
        raise ValueError("Profiles need side >= 50 and overlap values below 95")
    output.mkdir(parents=True, exist_ok=False)
    source_dir = output / "source"
    source_dir.mkdir()
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for directory in ("benchmarks", "src/geoplan", "scenarios", "tests/benchmarks"):
        for file in (root / directory).rglob("*.py"):
            relative = file.relative_to(root)
            destination = source_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
            hashes[relative.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(root / name, source_dir / name)
        hashes[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    rows: list[dict[str, Any]] = []
    summaries = []
    frozen = []
    timing_rows = []
    for base in source_cases:
        if selected and base.case_id not in selected:
            continue
        case_profiles = (
            profiles
            if profiles is not None
            else [
                (
                    base.request.parameters.side_overlap_percent,
                    base.request.parameters.forward_overlap_percent,
                )
            ]
        )
        for side, front in case_profiles:
            case = replace(
                base,
                case_id=f"{base.case_id}__side{side:g}_front{front:g}"
                if inputs is None
                else base.case_id,
                request=base.request.model_copy(
                    update={
                        "parameters": base.request.parameters.model_copy(
                            update={
                                "side_overlap_percent": side,
                                "forward_overlap_percent": front,
                            }
                        )
                    }
                ),
            )
            model = GeometryModel(case)
            print(f"{case.case_id}: generating", flush=True)
            frozen.append(
                {
                    "case_id": case.case_id,
                    "coverage_request": case.request.model_dump(mode="json"),
                    "home_local_m": case.home,
                    "landing_local_m": case.landing,
                    "cruise_speed_kmh": case.cruise_speed_kmh,
                    "endurance_minutes": case.endurance_minutes,
                    "reserve_percent": case.reserve_percent,
                    "projection": asdict(model.projection),
                }
            )
            started = perf_counter()
            # Outer coverage on departure and return, with a straight interior.
            # Dense inner contours can disappear without keeping old track IDs.
            pool = route_candidates(model)
            generation_seconds = perf_counter() - started
            validation_started = perf_counter()
            group: list[dict[str, Any]] = []
            for route in pool:
                metrics, flights = evaluate(model, route)
                stored = [[asdict(leg) for leg in flight] for flight in flights]
                photo = analyze(model, stored)
                if photo["images_reconstructed"] != metrics["images"]:
                    raise ValueError("Independent photo clock mismatch")
                row: dict[str, Any] = {
                    "case_id": case.case_id,
                    "base_case_id": base.case_id,
                    "side_overlap_percent": side,
                    "forward_overlap_percent": front,
                    **metrics,
                    "optional_pair_overlap_passed": photo["pair_overlap_passed"],
                    "minimum_pair_intersection_percent": photo["minimum_pair_intersection_percent"],
                    "pair_intersection_violations": photo["pairs_below_requested_overlap"],
                    "route": asdict(route),
                    "flights": stored,
                }
                group.append(row)
                rows.append(row)
            baseline = next(row for row in group if row["family"] == "production_reference")
            feasible = [row for row in group if row["screen_passed"]]
            best = (
                min(feasible, key=lambda row: (row["flight_minutes"], row["deadhead_minutes"]))
                if feasible
                else None
            )
            useful = [
                row
                for row in feasible
                if row["flight_minutes"] <= baseline["flight_minutes"] + 1e-8
            ]
            productive = (
                min(useful, key=lambda row: row["nonproductive_time_proxy_minutes"])
                if useful
                else None
            )
            endpoint_best = endpoint_protected_choice(group)
            summaries.append(
                {
                    "case_id": case.case_id,
                    "base_case_id": base.case_id,
                    "side_overlap_percent": side,
                    "forward_overlap_percent": front,
                    "variants": len(group),
                    "geometric_screen_passed": len(feasible),
                    "baseline_screen_passed": baseline["screen_passed"],
                    "best_family": best["family"] if best else None,
                    "baseline_flight_minutes": baseline["flight_minutes"],
                    "best_flight_minutes": best["flight_minutes"] if best else None,
                    "flight_change_percent": 100
                    * (best["flight_minutes"] / baseline["flight_minutes"] - 1)
                    if best
                    else None,
                    "deadhead_change_percent": 100
                    * (best["deadhead_minutes"] / baseline["deadhead_minutes"] - 1)
                    if best
                    else None,
                    "best_nonproductive_proxy_without_longer_flight": productive["family"]
                    if productive
                    else None,
                    "best_endpoint_protected_family": endpoint_best["family"]
                    if endpoint_best
                    else None,
                    "generation_seconds": generation_seconds,
                    "validation_seconds": perf_counter() - validation_started,
                }
            )
            # Keep the stricter, unconfirmed adjacent-area rule as an ablation.
            # It is deliberately excluded from the main acceptance/selection.
            if timing:
                choices = [
                    route
                    for route in pool
                    if route.family
                    in (
                        "production_reference",
                        "contours_inward",
                        "contours_loop",
                    )
                ]
                shells = sorted(
                    (r for r in feasible if r["family"].startswith(("shell_", "split_shell_"))),
                    key=lambda r: r["flight_minutes"],
                )
                if shells:
                    choices.append(next(r for r in pool if r.family == shells[0]["family"]))
                for route in choices:
                    if route.family.startswith("contours_"):
                        route = replace(
                            route, family=route.family + "_transit", scan_connections=False
                        )
                    result = retime(model, route)
                    row = {
                        "case_id": case.case_id,
                        "route": asdict(route),
                        "family": route.family,
                        "frames_planned": result.frames_planned,
                        "slowed_intervals": result.slowed_intervals,
                        "reason": result.reason,
                    }
                    if result.flights is not None:
                        stats = evaluate_flights(model, route.family, result.flights)
                        legs = [[asdict(leg) for leg in flight] for flight in result.flights]
                        check = analyze(model, legs)
                        if check["images_reconstructed"] != result.frames_planned:
                            raise ValueError("Retimed camera clock mismatch")
                        row.update({**stats, **check, "flights": legs})
                        speeds = [
                            ((leg.end[0] - leg.start[0]) ** 2 + (leg.end[1] - leg.start[1]) ** 2)
                            ** 0.5
                            / leg.minutes
                            * 0.06
                            for flight in result.flights
                            for leg in flight
                            if leg.phase == "survey"
                        ]
                        row["minimum_survey_ground_speed_kmh"] = min(speeds)
                    timing_rows.append(row)
            print(
                f"{case.case_id}: {len(group)} candidates, best={best['family'] if best else None}",
                flush=True,
            )
    (output / "inputs.json").write_text(
        json.dumps(frozen, indent=2), encoding="utf-8", newline="\n"
    )
    report = {
        "protocol": "free-geometry-endpoint-shells-v3",
        "parent_inputs_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest()
        if inputs is not None
        else None,
        "dataset": "frozen_inputs" if inputs is not None else suite,
        "timing_diagnostic_enabled": timing,
        "inputs_sha256": hashlib.sha256((output / "inputs.json").read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "acceptance": (
            "Same requested overlap, hardware, target, geometry and battery per comparison. "
            "Image union and interior spacing are geometric screens; "
            "a full photogrammetric audit is not performed. "
            "Adjacent frame area is diagnostic only and does not veto the primary comparison. "
            "Different quality profiles are never mixed to claim an algorithmic improvement."
        ),
        "summary": summaries,
        "results": rows,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "retiming-diagnostic.json").write_text(
        json.dumps(timing_rows, indent=2), encoding="utf-8"
    )
    fields = [key for key, value in summaries[0].items() if not isinstance(value, (list, dict))]
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs", type=Path, help="Replay frozen inputs instead of a built-in suite"
    )
    parser.add_argument("--suite", choices=("reference", "holdout", "all"), default="holdout")
    parser.add_argument("--trigger-interval", type=float, default=1)
    parser.add_argument("--timing-diagnostic", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", action="append", help="side:forward; e.g. 60:75")
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    profiles = []
    for profile in args.profile or ["60:75", "50:75", "60:60", "50:60"]:
        pair = profile.split(":")
        if len(pair) != 2:
            parser.error("A profile must contain side:forward")
        profiles.append((float(pair[0]), float(pair[1])))
    if args.inputs and (args.suite != "holdout" or args.trigger_interval != 1 or args.profile):
        parser.error("Frozen inputs already define the suite, quality profiles and camera interval")
    run(
        args.inputs,
        args.output,
        None if args.inputs else profiles,
        args.case,
        suite=args.suite,
        trigger_interval_s=args.trigger_interval,
        timing=args.timing_diagnostic,
    )
