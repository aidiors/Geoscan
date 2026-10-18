"""Isolate cross-area regrouping on identical inputs; preserve complete local evidence."""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import OptimizationCriterion
from geoplan.planning.cross_area import CrossAreaResult
from geoplan.planning.models import PlanResponse
from geoplan.planning.plan_quality import scheduled_plan_objective
from scenarios.customer import customer_polygons, customer_request, local_batch, stratified_polygons
from scenarios.neighbor_fields import neighbor_fields_request


class SeparateAreaControl:
    def improve(self, request, segments, drones, baseline, build, evaluate) -> CrossAreaResult:
        return CrossAreaResult(baseline, segments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--customer-source", type=Path)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--anchor", type=int, default=1)
    parser.add_argument("--budget", type=float, default=60)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new evidence file")
    request = neighbor_fields_request()
    if args.customer_source is not None:
        polygons = customer_polygons(args.customer_source)
        anchors = stratified_polygons(polygons, 7)
        request = customer_request(local_batch(polygons, anchors[args.anchor], args.count))
    request = request.model_copy(
        update={"productive_routing": True, "planning_time_limit_seconds": args.budget}
    )
    code_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path("src/geoplan").rglob("*.py"))
    }
    report = {
        "limitations": [
            "Customer geometry uses synthetic aircraft, base and camera; "
            "other customer restriction layers are not connected.",
            "Fixed-time heuristic runs do not prove optimality or universal improvement.",
            "Alternative coverage is disabled in both arms to isolate cross-area regrouping.",
        ],
        "code_sha256": code_hashes,
        "rows": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifacts = args.output.parent / args.output.stem
    artifacts.mkdir(exist_ok=True)
    for criterion in OptimizationCriterion:
        query = request.model_copy(
            update={
                "criterion": criterion,
                "completion_deadline_minutes": request.completion_deadline_minutes or 30,
            }
        )
        scores = []
        for label in ("separate", "joint"):
            dependencies = replace(create_default_dependencies(), alternative_coverage_planner=None)
            if label == "separate":
                dependencies = replace(dependencies, cross_area_optimizer=SeparateAreaControl())
            with TestClient(create_app(dependencies, serve_frontend=False)) as client:
                started = perf_counter()
                response = client.post("/v1/plans", json=query.model_dump(mode="json"))
                row = {
                    "criterion": criterion.value,
                    "variant": label,
                    "planning_seconds": perf_counter() - started,
                    "http_status": response.status_code,
                }
                body = response.json()
                name = f"{criterion.value}-{label}"
                (artifacts / f"{name}-request.json").write_text(
                    query.model_dump_json(), encoding="utf-8"
                )
                (artifacts / f"{name}-response.json").write_text(
                    json.dumps(body, ensure_ascii=False), encoding="utf-8"
                )
                if response.status_code == 200:
                    plan = PlanResponse.model_validate(body)
                    row.update(
                        metrics=body["metrics"],
                        audit=body["safety_audit"]["status"],
                        objective=list(scheduled_plan_objective(query, plan)),
                        search=body["search"],
                    )
                    scores.append(scheduled_plan_objective(query, plan))
                    quality = client.post(
                        "/v1/survey-quality",
                        json={"request": query.model_dump(mode="json"), "plan": body},
                    ).json()
                    row["photography"] = {k: v for k, v in quality.items() if k != "features"}
                    exports = []
                    for drone in plan.drone_plans:
                        if not drone.missions:
                            continue
                        for kind in ("geojson", "kml", "qgroundcontrol", "mission-planner"):
                            exported = client.post(
                                f"/v1/exports/{kind}",
                                json={"plan": body, "drone_id": drone.drone_id},
                            )
                            exports.append(
                                {
                                    "drone": drone.drone_id,
                                    "format": kind,
                                    "status": exported.status_code,
                                }
                            )
                    row["exports"] = exports
                else:
                    row["error"] = body
                report["rows"].append(row)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(json.dumps(row, ensure_ascii=False), flush=True)
        if len(scores) == 2:
            print(
                json.dumps(
                    {"criterion": criterion.value, "joint_not_worse": scores[1] <= scores[0]}
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
