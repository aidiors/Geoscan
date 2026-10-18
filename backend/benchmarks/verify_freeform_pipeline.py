"""Exercise actual HTTP planning and all exports on deterministic holdout areas."""

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SafetyAuditStatus
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.flight.trajectory import mission_route_clock
from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.routing.freeform import GeometryModel
from geoplan.routing.pattern_pool import CoverageCandidatePool, DiverseCoverageCandidateSelector
from scenarios.freeform import dataset


def run(output: Path, selected: list[str] | None = None, candidate_limit: int = 18) -> dict:
    if output.exists():
        raise ValueError("Choose a new output file; previous evidence is never overwritten")
    rows = []
    optimizer = AuditedFreeformRouteOptimizer(
        pool=CoverageCandidatePool(
            selector=DiverseCoverageCandidateSelector(maximum_candidates=candidate_limit)
        )
    )
    dependencies = replace(create_default_dependencies(), freeform_optimizer=optimizer)
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        for case in dataset("holdout", 1):
            if selected and case.case_id not in selected:
                continue
            model = GeometryModel(case)
            request = PlanRequest(
                criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
                productive_routing=True,
                drones=[
                    DroneSpec(
                        drone_id="uav",
                        name="Survey",
                        cruise_speed_kmh=90,
                        maximum_flight_minutes=60,
                        battery_reserve_percent=20,
                        supported_tasks=[MissionTask.RGB],
                    )
                ],
                coverage_areas=[case.request.area],
                survey_parameters=case.request.parameters,
                mission_altitude_m=100,
                task=MissionTask.RGB,
                departure_site=FlightSite(
                    site_id="start", name="Start", coordinate=model.coordinate(case.home)
                ),
                landing_site=FlightSite(
                    site_id="landing", name="Landing", coordinate=model.coordinate(case.landing)
                ),
            )
            plans = []
            elapsed = []
            for enabled in (False, True):
                started = perf_counter()
                response = client.post(
                    "/v1/plans",
                    json=request.model_copy(
                        update={"productive_routing": enabled},
                    ).model_dump(mode="json"),
                )
                if response.status_code != 200:
                    raise ValueError(response.text)
                plan = PlanResponse.model_validate(response.json())
                if (
                    plan.safety_audit is None
                    or plan.safety_audit.status is not SafetyAuditStatus.PASSED
                ):
                    raise ValueError("HTTP plan failed the real safety audit")
                plans.append(plan)
                elapsed.append(perf_counter() - started)
            baseline, best = plans
            if best.metrics.total_flight_minutes > baseline.metrics.total_flight_minutes + 0.02:
                raise ValueError("The optimized HTTP plan regressed")
            exports = 0
            for drone in best.drone_plans:
                if not drone.missions:
                    continue
                for fmt in ("geojson", "kml", "qgroundcontrol", "mission-planner"):
                    response = client.post(
                        f"/v1/exports/{fmt}",
                        json={
                            "plan": best.model_dump(mode="json"),
                            "drone_id": drone.drone_id,
                        },
                    )
                    if response.status_code != 200:
                        raise ValueError(response.text)
                    exports += 1
                for mission in drone.missions:
                    if mission.photo_capture_route_indices is not None:
                        offsets = mission_route_clock(mission).offsets_minutes
                        times = [offsets[i] for i in mission.photo_capture_route_indices]
                        from itertools import pairwise

                        if any((b - a) * 60 < 1 - 1e-6 for a, b in pairwise(times)):
                            raise ValueError("Camera hardware interval violated")
            assert best.optimization is not None
            summary = best.optimization.productive_routing
            rows.append(
                {
                    "case_id": case.case_id,
                    "baseline_flight_minutes": baseline.metrics.total_flight_minutes,
                    "selected_flight_minutes": best.metrics.total_flight_minutes,
                    "selected_scope": best.optimization.scope,
                    "selected_variant": summary.selected_variant if summary else None,
                    "freeform_candidates_checked": summary.freeform_candidates_checked
                    if summary
                    else 0,
                    "baseline_seconds": elapsed[0],
                    "optimized_seconds": elapsed[1],
                    "exports_checked": exports,
                    "safety_audit": "passed",
                    "image_count": sum(
                        m.image_count or 0 for d in best.drone_plans for m in d.missions
                    ),
                }
            )
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    if not rows:
        raise ValueError("No matching cases")
    root = Path(__file__).resolve().parents[1]
    source = [
        "planning/freeform.py",
        "planning/freeform_ports.py",
        "planning/freeform_missions.py",
        "planning/freeform_costs.py",
        "planning/plan_quality.py",
        "routing/pattern_pool.py",
        "routing/freeform.py",
        "routing/coverage_patterns.py",
        "routing/photography.py",
        "safety/freeform.py",
        "safety/audit.py",
        "coverage/resolution.py",
        "routing/pool.py",
        "routing/allocation.py",
        "assignment/neighborhood.py",
        "planning/productive.py",
        "planning/engine.py",
        "geodata/exporters.py",
    ]
    report = {
        "protocol": "http-freeform-acceptance-v1",
        "camera_interval_s": 1,
        "candidate_limit_per_context": candidate_limit,
        "overlap_side_forward_percent": [60, 75],
        "results": rows,
        "source_sha256": {
            name: hashlib.sha256((root / "src/geoplan" / name).read_bytes()).hexdigest()
            for name in source
        },
        "projection": asdict(GeometryModel(dataset("holdout", 1)[0]).projection),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--candidate-limit", type=int, default=18)
    args = parser.parse_args()
    run(args.output, args.case, args.candidate_limit)
