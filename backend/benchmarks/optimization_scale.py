"""Ablation of actual scheduled survey plans, including routing cost and safety audit.

Run from backend: python -m benchmarks.optimization_scale --output ../work/scale.json
Aircraft are synthetic. Solver CPU budgets do not bound mission construction or audit.
"""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import AssignmentStrategy, OptimizationCriterion
from geoplan.errors import PlanningError
from geoplan.planning.engine import build_plan
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited, scheduled_plan_objective
from geoplan.routing.model import routing_objective
from geoplan.routing.neighborhood import SortieNeighborhoodSearch
from geoplan.routing.pool import ProductiveRoutePool
from scenarios.optimization_scale import scale_scenarios


class RecordingRoutePool:
    def __init__(self, pool):
        self.pool = pool
        self.statistics = {}

    def generate(self, model, maximum_active_drones=None):
        started = perf_counter()
        candidates = self.pool.generate(model, maximum_active_drones)
        self.statistics = {
            "tracks": len(model.problem.tracks),
            "runtime_seconds": perf_counter() - started,
            "generated_candidates": len(candidates.all),
            "destroy_attempts": model.sortie_destroy_attempts,
            "completed_repairs": model.sortie_repair_completed,
            "geometry_builds": model.geometry_builds,
            "operator_attempts": model.neighborhood_operator_attempts,
            "operator_improvements": model.neighborhood_operator_improvements,
            "candidates": [
                {"name": name, "costs": model.metrics(solution)}
                for name, solution in candidates.all
            ],
        }
        return candidates


def schedule_comparison(request: PlanRequest, plan: PlanResponse) -> dict | None:
    schedule = plan.schedule
    if schedule is None:
        return None
    after = scheduled_plan_objective(request, plan)
    before = None
    if (
        schedule.reference_audited
        and schedule.reference_total_flight_minutes is not None
        and schedule.reference_makespan_minutes is not None
    ):
        # Scheduling changes order and altitude; horizontal transit and fleet stay fixed.
        before = routing_objective(
            request.criterion,
            request.completion_deadline_minutes,
            schedule.reference_total_flight_minutes,
            after[-1],
            schedule.reference_makespan_minutes,
            plan.metrics.active_drone_count,
        )
    verdict = (
        "invalid"
        if not plan_is_audited(plan)
        else "rescued"
        if before is None
        else "improved"
        if after < before
        else "regression"
        if after > before
        else "tie"
    )
    return {"before": before, "after": after, "verdict": verdict}


def summarize(measurements: list[dict]) -> dict:
    groups: dict = {}
    for row in measurements:
        groups.setdefault((row["scenario"], row["criterion"]), {})[row["mode"]] = row
    comparisons = []
    for (scenario, criterion), modes in groups.items():
        independent = None
        if "cross" in modes and modes["cross"].get("cross_objective_comparison"):
            candidate_mode = "cross"
            baseline_mode = "same_run_primary"
            verdict = modes["cross"]["cross_objective_comparison"]["verdict"]
            if "portfolio" in modes:
                old, new = modes["portfolio"], modes["cross"]
                if old["audited"] and new["audited"]:
                    a, b = tuple(old["objective"]), tuple(new["objective"])
                    independent = "improved" if b < a else ("regression" if b > a else "tie")
        else:
            baseline_mode, candidate_mode = (
                ("portfolio", "cross") if "cross" in modes else ("lns", "portfolio")
            )
            if baseline_mode not in modes or candidate_mode not in modes:
                continue
            old, new = modes[baseline_mode], modes[candidate_mode]
            if not old["audited"] or not new["audited"]:
                verdict = "invalid"
            else:
                a, b = tuple(old["objective"]), tuple(new["objective"])
                verdict = "improved" if b < a else ("regression" if b > a else "tie")
        comparisons.append(
            {
                "scenario": scenario,
                "criterion": criterion,
                "verdict": verdict,
                "baseline_mode": baseline_mode,
                "candidate_mode": candidate_mode,
                "independent_run_verdict": independent,
            }
        )
    scheduling = [
        {
            "scenario": row["scenario"],
            "criterion": row["criterion"],
            "mode": row["mode"],
            **row["schedule_comparison"],
            "verdict": row["schedule_comparison"]["verdict"] if row["audited"] else "invalid",
        }
        for row in measurements
        if row.get("schedule_comparison")
    ]
    return {
        "scheduling_pair_counts": {
            verdict: sum(c["verdict"] == verdict for c in scheduling)
            for verdict in ("improved", "tie", "regression", "rescued", "invalid")
        },
        "scheduling_comparisons": scheduling,
        "audit_or_planning_failures": sum(not r["audited"] for r in measurements),
        "balanced_deadline_misses": [
            {
                "scenario": r["scenario"],
                "mode": r["mode"],
                "completion_minutes": r["metrics"]["completion_minutes"],
            }
            for r in measurements
            if r["criterion"] == "balanced"
            and r.get("feasibility")
            and r["feasibility"]["status"] == "infeasible"
        ],
        "pair_counts": {
            verdict: sum(c["verdict"] == verdict for c in comparisons)
            for verdict in ("improved", "tie", "regression", "invalid")
        },
        "comparisons": comparisons,
    }


def run(
    output: Path,
    names: list[str],
    criteria: list[OptimizationCriterion],
    modes: list[str],
    iterations: int = 24,
    seed: int = 0,
):
    if output.exists():
        raise ValueError("Choose a new output path to preserve previous evidence")
    sources = [
        *sorted(Path("src/geoplan").rglob("*.py")),
        Path("scenarios/optimization_scale.py"),
        Path(__file__),
    ]
    report = {
        "scope": "synthetic fleets, actual survey missions, scheduler and independent audit",
        "limits": "zero turn radius; altitude and sensor fixed; no operational forecast",
        "search_configuration": {
            "adaptive_iterations": iterations,
            "random_seed": seed,
            "neighborhood_wall_seconds": 1.5,
            "legacy_iterations": 6,
            "additional_adaptive_wall_seconds": 0.75,
            "completion_seed_wall_seconds": 0.75,
            "completion_deadline_minutes": 170,
            "cross_objective_wall_seconds": 4,
        },
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "measurements": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for case in scale_scenarios():
        if names and case.name not in names:
            continue
        for criterion in criteria:
            for mode in modes:
                request = case.request.model_copy(
                    update={
                        "criterion": criterion,
                        "completion_deadline_minutes": 170,
                        "productive_routing": mode != "reference",
                        "assignment_strategy": AssignmentStrategy.CP_SAT_PORTFOLIO
                        if mode == "cp_sat"
                        else AssignmentStrategy.HYBRID,
                    }
                )
                pool = RecordingRoutePool(
                    ProductiveRoutePool(
                        neighborhood_seconds=0 if mode == "catalog" else 1.5,
                        adaptive_neighborhood_seconds=0.75
                        if mode in {"portfolio", "cp_sat", "cross"}
                        else 0,
                        completion_seed_seconds=0.75
                        if mode in {"portfolio", "cp_sat", "cross"}
                        else 0,
                        cross_objective_seconds=4 if mode in {"cross", "cp_sat"} else 0,
                        adaptive_neighborhood_search=SortieNeighborhoodSearch(
                            adaptive=True,
                            maximum_iterations=iterations,
                            random_seed=seed,
                        ),
                        neighborhood_search=SortieNeighborhoodSearch(
                            adaptive=mode == "adaptive",
                            maximum_iterations=iterations if mode == "adaptive" else 6,
                            random_seed=seed,
                        ),
                    )
                )
                dependencies = replace(create_default_dependencies(), productive_route_pool=pool)
                started = perf_counter()
                row: dict = {
                    "scenario": case.name,
                    "split": case.split,
                    "area_km2": case.area_km2,
                    "fleet": len(request.drones),
                    "criterion": criterion.value,
                    "mode": mode,
                    "request_sha256": hashlib.sha256(
                        request.model_dump_json().encode()
                    ).hexdigest(),
                }
                try:
                    plan = build_plan(
                        request,
                        dependencies.duration_calculator,
                        dependencies.route_optimizer,
                        dependencies.coverage_planner,
                        dependencies.assignment_optimizer,
                        dependencies.mission_scheduler,
                        dependencies.sweep_transition_planner,
                        dependencies.safety_auditor,
                        dependencies.vertical_profile_planner,
                        dependencies.freeform_optimizer,
                        dependencies.assignment_alternative,
                        dependencies.productive_route_pool,
                    )
                    row.update(
                        {
                            "audited": plan_is_audited(plan),
                            "objective": scheduled_plan_objective(request, plan),
                            "metrics": plan.metrics.model_dump(mode="json"),
                            "feasibility": plan.feasibility.model_dump(mode="json")
                            if plan.feasibility
                            else None,
                            "safety_audit": plan.safety_audit.model_dump(mode="json")
                            if plan.safety_audit
                            else None,
                            "optimization": plan.optimization.model_dump(mode="json")
                            if plan.optimization
                            else None,
                            "schedule": plan.schedule.model_dump(mode="json")
                            if plan.schedule
                            else None,
                            "schedule_comparison": schedule_comparison(request, plan),
                        }
                    )
                    if mode == "cross":
                        routing = (
                            plan.optimization.productive_routing if plan.optimization else None
                        )
                        before = routing.pre_cross_objective if routing else None
                        after = list(scheduled_plan_objective(request, plan))
                        if (
                            before is None
                            and routing
                            and routing.cross_objective_variants_evaluated == 0
                        ):
                            before = after
                        row["cross_objective_comparison"] = {
                            "before": before,
                            "after": after,
                            "before_completion_minutes": routing.pre_cross_makespan_minutes
                            if routing
                            else None,
                            "before_flight_minutes": routing.pre_cross_total_flight_minutes
                            if routing
                            else None,
                            "verdict": (
                                "invalid"
                                if not plan_is_audited(plan) or before is None
                                else "improved"
                                if tuple(after) < tuple(before)
                                else "regression"
                                if tuple(after) > tuple(before)
                                else "tie"
                            ),
                        }
                except PlanningError as error:
                    row.update(
                        {"audited": False, "error": error.code.value, "message": error.message}
                    )
                row.update(
                    {"runtime_seconds": perf_counter() - started, "route_search": pool.statistics}
                )
                report["measurements"].append(row)
                report["summary"] = summarize(report["measurements"])
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    case.name,
                    criterion.value,
                    mode,
                    row.get("audited"),
                    row.get("metrics"),
                    round(row["runtime_seconds"], 3),
                    flush=True,
                )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios", nargs="*", default=[])
    parser.add_argument(
        "--criteria",
        nargs="+",
        choices=list(OptimizationCriterion),
        default=[OptimizationCriterion.COMPLETION_TIME],
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["reference", "catalog", "lns", "adaptive", "portfolio", "cross", "cp_sat"],
        default=["reference", "catalog", "lns", "portfolio", "cross", "cp_sat"],
    )
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run(
        args.output,
        args.scenarios,
        [OptimizationCriterion(c) for c in args.criteria],
        args.modes,
        args.iterations,
        args.seed,
    )


if __name__ == "__main__":
    main()
