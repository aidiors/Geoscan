"""Run with: uv run --group benchmark python -m benchmarks.compare_ortools.

Writes JSON under ignored work/, never installs a production optimizer.
Synthetic matrices isolate search quality; physical cases reuse generated
missions and the real scheduler/auditor. Cold starts are the default;
--warm-start supplies the current result and reports its preparation time separately.
"""

import argparse
import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path
from random import Random
from time import perf_counter

import ortools

from benchmarks.ortools_assignment import (
    CpSatAssignmentBenchmark,
    evaluate_assignment,
    objective_score,
)
from geoplan.airspace.windows import _schedule_time_windows, _vertical_layers_by_segment_id
from geoplan.assignment.models import (
    AssignmentOptimizationRequest,
    AssignmentOptimizationResult,
    MissionAssignmentCandidate,
)
from geoplan.assignment.solver import HybridAssignmentOptimizer
from geoplan.composition import AppDependencies, create_default_dependencies
from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.engine import build_plan
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.segments import _resolve_segments
from geoplan.scheduling.models import MissionScheduleRequest, MissionScheduleResult
from scenarios.planning import productive_survey_request


@dataclass
class AssignmentRecorder:
    optimizer: HybridAssignmentOptimizer = field(default_factory=HybridAssignmentOptimizer)
    requests: list[AssignmentOptimizationRequest] = field(default_factory=list)
    results: list[AssignmentOptimizationResult] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)

    def optimize(self, request: AssignmentOptimizationRequest) -> AssignmentOptimizationResult:
        started = perf_counter()
        result = self.optimizer.optimize(request)
        self.seconds.append(perf_counter() - started)
        self.requests.append(request)
        self.results.append(result)
        return result


def compare_scores(old: tuple[float, ...], new: tuple[float, ...]) -> str:
    for previous, candidate in zip(old, new, strict=True):
        if abs(previous - candidate) > 1e-5:
            return "ortools_better" if candidate < previous else "current_better"
    return "tie"


def synthetic_request(seed: int, tasks: int, drones: int) -> AssignmentOptimizationRequest:
    random = Random(seed)
    ids = [f"drone-{i}" for i in range(drones)]
    groups = []
    for index in range(tasks):
        workload = random.uniform(4, 25)
        group = []
        for drone_index, drone in enumerate(ids):
            if random.random() < 0.15 and group:
                continue
            duration = round(
                workload * (0.65 + drone_index / drones) * random.uniform(0.7, 1.4)
                + random.uniform(0, 8),
                6,
            )
            group.append(
                MissionAssignmentCandidate(
                    segment_id=f"task-{index}",
                    drone_id=drone,
                    mission=Mission(
                        drone_id=drone,
                        segment_id=f"task-{index}",
                        duration_minutes=duration,
                        distance_km=duration,
                        route=[
                            Coordinate(longitude=37, latitude=55),
                            Coordinate(longitude=37.01, latitude=55.01),
                        ],
                        altitude_m=120,
                        cruise_speed_kmh=60,
                        effective_speed_kmh=60,
                        estimated_battery_percent=30,
                    ),
                )
            )
        groups.append(group)
    return AssignmentOptimizationRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drone_ids=ids,
        candidate_groups=groups,
        maximum_active_drones=max(1, drones - 2),
        turnaround_minutes_by_drone={d: float(i % 4) for i, d in enumerate(ids)},
    )


def compare_assignment(
    request: AssignmentOptimizationRequest,
    solver: CpSatAssignmentBenchmark,
    baseline: AssignmentOptimizationResult | None = None,
    baseline_seconds: float | None = None,
    warm_start: bool = False,
) -> dict:
    if baseline is None:
        started = perf_counter()
        baseline = HybridAssignmentOptimizer().optimize(request)
        baseline_seconds = perf_counter() - started
    choices = tuple(
        group.index(candidate)
        for group, candidate in zip(request.candidate_groups, baseline.assignments, strict=True)
    )
    existing = evaluate_assignment(request, choices)
    alternative = solver.solve(request, choices if warm_start else None)
    candidate = alternative.evaluation
    verdict = "no_solution"
    if candidate is not None:
        # Avoid calling floating-point summation noise a win. This tolerance is
        # only for reporting; the CP-SAT integer precision is recorded separately.
        verdict = compare_scores(existing.score, candidate.score)
    primary_change = None
    if candidate is not None:
        if request.criterion is OptimizationCriterion.COMPLETION_TIME:
            old, new = existing.completion_minutes, candidate.completion_minutes
        elif request.criterion is OptimizationCriterion.TOTAL_FLIGHT_TIME:
            old, new = existing.total_minutes, candidate.total_minutes
        elif existing.score[0] == candidate.score[0]:
            old, new = existing.score[1], candidate.score[1]
        else:
            old, new = 0.0, 0.0
        if (
            request.criterion is not OptimizationCriterion.BALANCED
            or existing.score[0] == candidate.score[0]
        ):
            primary_change = (old - new) / old * 100 if old else 0.0
    return {
        "criterion": request.criterion.value,
        "tasks": len(request.candidate_groups),
        "fleet": len(request.drone_ids),
        "maximum_active_drones": request.maximum_active_drones,
        "deadline_minutes": request.completion_deadline_minutes,
        "input_sha256": hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
        "current": {
            **asdict(existing),
            "runtime_seconds": baseline_seconds,
            "algorithm": baseline.algorithm.value,
        },
        "ortools": asdict(alternative),
        "warm_start_total_seconds": (baseline_seconds or 0) + alternative.runtime_seconds
        if warm_start
        else None,
        "verdict": verdict,
        "primary_improvement_percent": primary_change,
    }


def physical_requests() -> list[tuple[str, PlanRequest]]:
    base = productive_survey_request(productive_routing=False)
    drones = [
        base.drones[0].model_copy(
            update={
                "drone_id": f"mixed-{i}",
                "name": f"Synthetic UAV {i}",
                "cruise_speed_kmh": 40 + i * 7,
                "maximum_flight_minutes": 35 + i * 6,
                "minimum_turnaround_minutes": float(i % 3 + 1),
            }
        )
        for i in range(8)
    ]
    assert base.survey_parameters is not None
    sensor = base.survey_parameters.sensor.model_copy(update={"minimum_trigger_interval_s": 0.5})
    survey = base.survey_parameters.model_copy(update={"sensor": sensor})
    request = base.model_copy(
        update={
            "drones": drones,
            "maximum_active_drones": 5,
            "survey_parameters": survey,
        }
    )
    assert request.departure_site is not None
    distant = request.departure_site.model_copy(
        update={
            "coordinate": Coordinate(longitude=36.98, latitude=54.99),
        }
    )
    projection = LocalProjection(37, 55)
    larger = request.coverage_areas[0].model_copy(
        update={
            "coordinates": [
                projection.unproject(x, y) for x, y in ((0, 0), (2400, 0), (2400, 1200), (0, 1200))
            ]
        }
    )
    return [
        ("mixed_near", request),
        (
            "mixed_distant",
            request.model_copy(update={"departure_site": distant, "landing_site": distant}),
        ),
        ("mixed_larger", request.model_copy(update={"coverage_areas": [larger]})),
    ]


def physical_comparison(
    request: PlanRequest,
    solver: CpSatAssignmentBenchmark,
    warm_start: bool = False,
) -> dict:
    dependencies = create_default_dependencies()
    recorder = AssignmentRecorder()
    plan: PlanResponse = build_plan(
        request,
        dependencies.duration_calculator,
        dependencies.route_optimizer,
        dependencies.coverage_planner,
        recorder,
        dependencies.mission_scheduler,
        dependencies.sweep_transition_planner,
        dependencies.safety_auditor,
        dependencies.vertical_profile_planner,
        dependencies.freeform_optimizer,
    )
    assert len(recorder.requests) == 1
    assert plan.safety_audit is not None and plan.schedule is not None
    problem = recorder.requests[0]
    comparison = compare_assignment(
        problem, solver, recorder.results[0], recorder.seconds[0], warm_start
    )
    result = comparison["ortools"]["evaluation"]
    if result is None:
        comparison["scheduled"] = {"current_audit": plan.safety_audit.status.value}
        return comparison
    assignments = [
        group[index]
        for group, index in zip(problem.candidate_groups, result["choices"], strict=True)
    ]
    missions = [candidate.mission for candidate in assignments]
    baseline_schedule = schedule_assignment(
        request, [a.mission for a in recorder.results[0].assignments], dependencies
    )
    if abs(baseline_schedule.makespan_minutes - plan.schedule.makespan_minutes) > 1e-9:
        raise AssertionError("Benchmark scheduling differs from the production baseline")
    schedule = schedule_assignment(request, missions, dependencies)
    segments = _resolve_segments(
        request, dependencies.coverage_planner, dependencies.sweep_transition_planner, None
    )
    audit = dependencies.safety_auditor.audit(request, segments, schedule.missions)
    baseline_audit = dependencies.safety_auditor.audit(
        request, segments, baseline_schedule.missions
    )
    if baseline_audit != plan.safety_audit:
        raise AssertionError("Benchmark audit differs from the production baseline")
    failed = [check for check in audit.checks if check.status.value == "failed"]
    comparison["scheduled"] = {
        "current_audit": plan.safety_audit.status.value,
        "ortools_audit": audit.status.value,
        "current_completion_minutes": plan.schedule.makespan_minutes,
        "ortools_completion_minutes": schedule.makespan_minutes,
        "current_total_minutes": sum(
            m.duration_minutes for d in plan.drone_plans for m in d.missions
        ),
        "ortools_total_minutes": sum(m.duration_minutes for m in schedule.missions),
        "ortools_failed_checks": [check.code.value for check in failed],
        "failure_details": [check.model_dump(mode="json") for check in failed],
        "failed_schedule_timeline": [
            {
                "drone_id": m.drone_id,
                "segment_id": m.segment_id,
                "start": m.start_offset_minutes,
                "end": m.end_offset_minutes,
                "duration": m.duration_minutes,
            }
            for m in schedule.missions
        ]
        if failed
        else [],
    }
    actual_total = sum(m.duration_minutes for m in schedule.missions)
    actual_active = len({m.drone_id for m in schedule.missions})
    comparison["scheduled"]["verdict"] = (
        compare_scores(
            objective_score(
                problem,
                comparison["scheduled"]["current_total_minutes"],
                plan.schedule.makespan_minutes,
                plan.metrics.active_drone_count,
            ),
            objective_score(problem, actual_total, schedule.makespan_minutes, actual_active),
        )
        if plan.safety_audit.status.value == audit.status.value == "passed"
        else "audit_failed"
    )
    return comparison


def schedule_assignment(
    request: PlanRequest, missions: list[Mission], dependencies: AppDependencies
) -> MissionScheduleResult:
    # Same scheduler inputs as planning.engine._finish_plan, without presenting
    # a solver choice as the winner before independently auditing its schedule.
    return dependencies.mission_scheduler.schedule(
        MissionScheduleRequest(
            missions=missions,
            minimum_separation_m=request.minimum_separation_m,
            minimum_vertical_separation_m=request.minimum_vertical_separation_m,
            position_uncertainty_m=request.position_uncertainty_m,
            start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
            speed_uncertainty_percent=request.speed_uncertainty_percent,
            flight_site_occupancy_seconds=request.flight_site_occupancy_seconds,
            turnaround_minutes=request.turnaround_minutes,
            turnaround_minutes_by_drone={
                d.drone_id: d.minimum_turnaround_minutes for d in request.drones
            },
            planning_start_time=request.planning_start_time,
            time_windows_by_segment_id=_schedule_time_windows(request, missions),
            vertical_layers_by_segment_id=_vertical_layers_by_segment_id(missions, request),
        )
    )


def run(seconds: float, seeds: list[int], include_physical: bool, warm_start: bool = False) -> dict:
    solver = CpSatAssignmentBenchmark(time_limit_seconds=seconds)
    measurements = []
    for seed in seeds:
        for size, tasks, drones in (("small", 7, 3), ("medium", 24, 8), ("large", 60, 10)):
            source = synthetic_request(seed, tasks, drones)
            # A shared deadline derived from a lower-bound estimate, not from
            # either solver's result; some cases may legitimately miss it.
            work = sum(min(c.mission.duration_minutes for c in g) for g in source.candidate_groups)
            deadline = work / (source.maximum_active_drones or drones) * 1.3 + 5
            for criterion in OptimizationCriterion:
                request = source.model_copy(
                    update={"criterion": criterion, "completion_deadline_minutes": deadline}
                )
                row = compare_assignment(request, solver, warm_start=warm_start)
                row.update({"name": f"synthetic_{size}_{seed}", "kind": "synthetic"})
                measurements.append(row)
                print(f"{row['name']} {criterion.value}: {row['verdict']}", flush=True)
    if include_physical:
        for name, source in physical_requests():
            for criterion in OptimizationCriterion:
                request = source.model_copy(
                    update={"criterion": criterion, "completion_deadline_minutes": 25}
                )
                row = physical_comparison(request, solver, warm_start)
                row.update({"name": name, "kind": "physical_pipeline"})
                measurements.append(row)
                print(f"{name} {criterion.value}: {row['verdict']} {row['scheduled']}", flush=True)
    return {
        "scope": "complete-sortie assignment; no new ordering, partitioning or online replanning",
        "budget": {
            "ortools_wall_seconds_per_problem": seconds,
            "ortools_workers": 1,
            "current": "existing stopping rules; measured, no imposed timeout",
            "warm_start_from_current": warm_start,
        },
        "python": platform.python_version(),
        "ortools_version": ortools.__version__,
        "seeds": seeds,
        "source_sha256": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in (
                "benchmarks/compare_ortools.py",
                "benchmarks/ortools_assignment.py",
                "src/geoplan/assignment/solver.py",
                "src/geoplan/assignment/cp_sat.py",
                "src/geoplan/assignment/alternatives.py",
                "src/geoplan/planning/assignment_portfolio.py",
                "src/geoplan/assignment/neighborhood.py",
                "src/geoplan/coverage/resolution.py",
                "src/geoplan/airspace/windows.py",
                "src/geoplan/planning/engine.py",
                "src/geoplan/scheduling/scheduler.py",
                "src/geoplan/safety/audit.py",
            )
        },
        "measurements": measurements,
        "summary": {
            verdict: sum(row["verdict"] == verdict for row in measurements)
            for verdict in ("tie", "ortools_better", "current_better", "no_solution")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 42, 109])
    parser.add_argument("--skip-physical", action="store_true")
    parser.add_argument("--warm-start", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("../work/ortools-assignment.json"))
    args = parser.parse_args()
    report = run(args.seconds, args.seeds, not args.skip_physical, args.warm_start)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
