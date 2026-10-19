"""Customer-data ablation. JSONL preserves failures and completed rows on interruption."""

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from benchmarks.process_memory import peak_rss_mib
from geoplan.api.app import create_app
from geoplan.budget import planning_budget
from geoplan.composition import create_default_dependencies
from geoplan.coverage.adaptive import AdaptiveCoveragePlanner
from geoplan.coverage.models import CoveragePlanRequest
from geoplan.coverage.scoring import _scoring_exclusions
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner, CoverageRouteSet, _route_sets_score
from geoplan.domain.enums import OptimizationCriterion, SweepAngleAlgorithm
from geoplan.navigation.routing import PreparedSafeRoutes
from scenarios.customer import customer_polygons, customer_request, local_batch, stratified_polygons


def write_row(output: Path, row: dict) -> None:
    with output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if row["phase"] != "provenance":
        print(
            json.dumps(
                {k: v for k, v in row.items() if k not in ("optimization", "schedule")},
                ensure_ascii=False,
            ),
            flush=True,
        )


def coverage_rows(source: Path, output: Path, count: int, algorithms: list[str]) -> None:
    polygons = customer_polygons(source)
    selected = stratified_polygons(polygons, count) if count else polygons
    for polygon in selected:
        request = customer_request([polygon])
        assert request.survey_parameters is not None and request.departure_site is not None
        coverage = CoveragePlanRequest(
            area=polygon.area,
            parameters=request.survey_parameters,
            mission_altitude_m=request.mission_altitude_m,
            departure_candidates=[request.departure_site.coordinate],
            landing_candidates=[request.departure_site.coordinate],
        )
        for algorithm in algorithms:
            started = perf_counter()
            row = {
                "phase": "coverage",
                "id": polygon.area.area_id,
                "area_km2": polygon.area_km2,
                "vertices": len(polygon.area.coordinates),
                "holes": len(polygon.area.interior_rings),
                "algorithm": algorithm,
            }
            try:
                planner = (
                    AdaptiveCoveragePlanner()
                    if algorithm == "adaptive"
                    else BoustrophedonCoveragePlanner(SweepAngleAlgorithm(algorithm))
                )
                with planning_budget(10):
                    plan = planner.plan(coverage)
                exclusions = _scoring_exclusions(coverage)
                routes = PreparedSafeRoutes.create(
                    [*polygon.area.coordinates, request.departure_site.coordinate], exclusions
                )
                scores = [
                    _route_sets_score(
                        [CoverageRouteSet(s.route, s.alternative_routes)],
                        coverage,
                        exclusions,
                        s.survey_speed_limit_kmh or 1,
                        routes,
                    )
                    for s in plan.segments
                ]
                row.update(
                    segments=len(plan.segments),
                    tracks=sum(len(s.survey_tracks or s.route[::2]) for s in plan.segments),
                    distance_km=sum(s.distance_km for s in plan.segments),
                    site_inclusive_km=sum(s.distance_km for s in scores if s),
                    valid=all(s is not None for s in scores),
                )
            except Exception as error:
                row.update(valid=False, error=f"{type(error).__name__}: {error}")
            row["seconds"] = perf_counter() - started
            write_row(output, row)


def pipeline_rows(
    source: Path,
    output: Path,
    count: int,
    algorithms: list[str],
    anchor_indices: list[int],
    criteria: list[str],
    deadline_minutes: float,
    verify_exports: bool,
) -> None:
    polygons = customer_polygons(source)
    # Quantile anchors spread batches across size and location before measuring results.
    anchors = stratified_polygons(polygons, 7)
    for anchor_index in anchor_indices:
        batch = local_batch(polygons, anchors[anchor_index], count)
        for criterion in map(OptimizationCriterion, criteria):
            for algorithm in algorithms:
                request = customer_request(
                    batch,
                    criterion=criterion,
                    completion_deadline_minutes=deadline_minutes,
                    productive_routing="productive" in algorithm or algorithm == "production",
                    assignment_strategy=(
                        "automatic"
                        if algorithm == "production"
                        else "cp_sat_portfolio"
                        if "cp" in algorithm
                        else "hybrid"
                    ),
                )
                dependencies = create_default_dependencies()
                if algorithm != "production":
                    dependencies = replace(dependencies, alternative_coverage_planner=None)
                if algorithm.startswith("rectangle"):
                    dependencies = replace(
                        dependencies,
                        coverage_planner=BoustrophedonCoveragePlanner(
                            SweepAngleAlgorithm.MINIMUM_ROTATED_RECTANGLE
                        ),
                    )
                elif algorithm.startswith("bounded"):
                    dependencies = replace(
                        dependencies,
                        coverage_planner=BoustrophedonCoveragePlanner(
                            SweepAngleAlgorithm.BOUNDED_PORTFOLIO
                        ),
                    )
                elif algorithm.startswith("adaptive"):
                    dependencies = replace(dependencies, coverage_planner=AdaptiveCoveragePlanner())
                started = perf_counter()
                row: dict = {
                    "phase": "pipeline",
                    "anchor": anchor_index,
                    "ids": [p.area.area_id for p in batch],
                    "algorithm": algorithm,
                    "criterion": criterion.value,
                    "area_km2": sum(p.area_km2 for p in batch),
                }
                with TestClient(create_app(dependencies, serve_frontend=False)) as client:
                    try:
                        http_started = perf_counter()
                        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
                        row["planning_seconds"] = perf_counter() - http_started
                        row["process_peak_rss_mib"] = peak_rss_mib()
                        row["status"] = response.status_code
                        body = response.json()
                        artifacts = output.parent / output.stem
                        artifacts.mkdir(exist_ok=True)
                        name = f"{anchor_index}-{criterion.value}-{algorithm}"
                        (artifacts / f"{name}-request.json").write_text(
                            request.model_dump_json(), encoding="utf-8"
                        )
                        (artifacts / f"{name}-response.json").write_text(
                            json.dumps(body, ensure_ascii=False), encoding="utf-8"
                        )
                        if response.status_code == 200:
                            row.update(
                                metrics=body["metrics"],
                                audit=body["safety_audit"]["status"],
                                optimization=body["optimization"],
                                schedule=body["schedule"],
                                feasibility=body["feasibility"]["status"],
                                transit_minutes=sum(
                                    m["transit_duration_minutes"] or 0
                                    for drone in body["drone_plans"]
                                    for m in drone["missions"]
                                ),
                                return_minutes=sum(
                                    stage["duration_minutes"]
                                    for drone in body["drone_plans"]
                                    for m in drone["missions"]
                                    for stage in m["stages"]
                                    if stage["stage_type"] == "landing"
                                ),
                                failed_checks=[
                                    c
                                    for c in body["safety_audit"]["checks"]
                                    if c["status"] == "failed"
                                ],
                            )
                            if verify_exports and row["audit"] == "passed":
                                exports_started = perf_counter()
                                verified = 0
                                release_blocked = 0
                                for drone in body["drone_plans"]:
                                    if not drone["missions"]:
                                        continue
                                    for kind in (
                                        "geojson",
                                        "kml",
                                        "qgroundcontrol",
                                        "mission-planner",
                                    ):
                                        execution = kind in ("qgroundcontrol", "mission-planner")
                                        for sortie_index in (
                                            range(len(drone["missions"])) if execution else [None]
                                        ):
                                            exported = client.post(
                                                f"/v1/exports/{kind}",
                                                json={
                                                    "plan": body,
                                                    "drone_id": drone["drone_id"],
                                                    "sortie_index": sortie_index,
                                                },
                                            )
                                            expected = (
                                                422
                                                if execution
                                                and not body.get("export_authorization")
                                                else 200
                                            )
                                            if exported.status_code != expected:
                                                raise ValueError(f"Export {kind}: {exported.text}")
                                            if (
                                                expected == 422
                                                and exported.json()["code"]
                                                != "export_release_required"
                                            ):
                                                raise ValueError("Unexpected export refusal")
                                            verified += int(exported.status_code == 200)
                                            release_blocked += int(exported.status_code == 422)
                                row["exports_verified"] = verified
                                row["exports_release_blocked"] = release_blocked
                                row["exports_seconds"] = perf_counter() - exports_started
                        else:
                            row["error"] = body
                    except Exception as error:
                        row["error"] = f"{type(error).__name__}: {error}"
                row["seconds"] = perf_counter() - started
                write_row(output, row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("coverage", "pipeline"))
    parser.add_argument(
        "--source", type=Path, default=Path("../reference/hackathon/Границы полетов.kml")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--algorithms", nargs="+", required=True)
    parser.add_argument("--anchors", type=int, nargs="+", choices=range(7), default=[1, 3, 5])
    parser.add_argument(
        "--criteria",
        nargs="+",
        choices=[c.value for c in OptimizationCriterion],
        default=[c.value for c in OptimizationCriterion],
    )
    parser.add_argument("--deadline", type=float, default=120)
    parser.add_argument("--exports", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new evidence file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sources = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in (Path("src/geoplan"), Path("scenarios"))
        for p in sorted(root.rglob("*.py"))
    }
    sources[str(Path(__file__))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    memory_source = Path("benchmarks/process_memory.py")
    sources[str(memory_source)] = hashlib.sha256(memory_source.read_bytes()).hexdigest()
    write_row(
        args.output,
        {
            "phase": "provenance",
            "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
            "code_sha256": sources,
            "platform": platform.platform(),
            "python": sys.version,
            "arguments": sys.argv,
            "coverage_case_budget_seconds": 10,
            "operational_sample_minimum_area_m2": 1,
            "assumptions": "Real geometry; synthetic central bases, 4 aircraft, camera and weather",
        },
    )
    if args.phase == "coverage":
        coverage_rows(args.source, args.output, args.count, args.algorithms)
    else:
        pipeline_rows(
            args.source,
            args.output,
            args.count,
            args.algorithms,
            args.anchors,
            args.criteria,
            args.deadline,
            args.exports,
        )


if __name__ == "__main__":
    main()
