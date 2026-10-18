"""A/B test connection guidance at identical configured routing-search budgets.

Both variants still construct physical curved missions, schedule them and audit
them. The straight proxy is guidance only, never a relaxation of flight safety.
Full pipeline execution time is measured separately, not claimed to be equal.
"""

import argparse
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

from benchmarks.final_readiness import _source_fingerprint, _write
from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.routing.model import CostModel
from geoplan.routing.pool import ProductiveRoutePool


class StraightConnectionProxyPool:
    def __init__(self, pool: ProductiveRoutePool):
        self.pool = pool

    def generate(self, model, maximum_active_drones=None):
        return self.pool.generate(CostModel(model.problem), maximum_active_drones)


def run(request_path: Path, output: Path, repeats: int) -> None:
    request = PlanRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    _write(output / "request.json", request.model_dump(mode="json"))
    pool = ProductiveRoutePool()
    dependencies = create_default_dependencies()
    summary = {
        "backend_source_sha256": _source_fingerprint(),
        "budgets": {
            "construction_seconds": pool.total_seconds,
            "cross_objective_seconds": pool.cross_objective_seconds,
            "catalog_seconds": pool.seconds_per_catalog,
            "decoder_seconds": pool.seconds_per_decode,
        },
        "qualification": "Identical routing-search budgets. Full pipeline times may differ; "
        "these are repeated measurements, not a proof of global optimality.",
        "pairs": [],
    }
    with (
        TestClient(create_app(dependencies, serve_frontend=False)) as physical,
        TestClient(
            create_app(
                replace(dependencies, productive_route_pool=StraightConnectionProxyPool(pool)),
                serve_frontend=False,
            )
        ) as straight,
    ):
        clients = {"heading_aware": physical, "straight_proxy": straight}
        for repeat in range(repeats):
            pair: dict[str, Any] = {}
            order = (
                ("heading_aware", "straight_proxy")
                if repeat % 2 == 0
                else ("straight_proxy", "heading_aware")
            )
            for name in order:
                started = perf_counter()
                response = clients[name].post("/v1/plans", json=request.model_dump(mode="json"))
                elapsed = perf_counter() - started
                _write(output / f"{name}-{repeat + 1}.json", response.json())
                plan = (
                    PlanResponse.model_validate(response.json())
                    if response.status_code == 200
                    else None
                )
                pair[name] = {
                    "seconds": elapsed,
                    "http_status": response.status_code,
                    "audited": plan_is_audited(plan) if plan is not None else False,
                    "objective": list(scheduled_plan_objective(request, plan))
                    if plan is not None
                    else None,
                    "metrics": plan.metrics.model_dump() if plan is not None else None,
                }
                print(repeat + 1, name, response.status_code, round(elapsed, 2), flush=True)
            if all(item["audited"] for item in pair.values()):
                a, b = pair["heading_aware"]["objective"], pair["straight_proxy"]["objective"]
                pair["heading_result"] = "better" if a < b else "worse" if a > b else "tie"
            else:
                pair["heading_result"] = "not_comparable"
            summary["pairs"].append(pair)
            _write(output / "comparison.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path, default=Path("../work/curved-ablation"))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    run(args.request, args.output, args.repeats)
