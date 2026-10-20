"""Replay fixed-track solutions through the real scheduler, audit and four exporters.

This adapter is experimental. It exposes compatibility gaps; a passing legacy
audit alone is deliberately insufficient for approving application integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from itertools import combinations, pairwise
from math import ceil
from pathlib import Path
from time import perf_counter

from shapely.geometry import LineString
from shapely.ops import unary_union

from benchmarks.acceptance_scale import _base_request
from benchmarks.productive_dataset import load_cases
from benchmarks.productive_model import CostModel, Problem, Solution, Trip
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.enums import MissionStageType, MissionTask
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission, MissionStage
from geoplan.flight.performance import (
    ground_speed_kmh,
    route_duration_minutes,
    route_leg_durations_minutes,
)
from geoplan.geodata.exporters import (
    _mission_commands,
    build_geojson_export,
    build_kml_export,
    build_mission_planner_export,
    build_qgroundcontrol_export,
)
from geoplan.geodata.models import ExportRequest
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates
from geoplan.navigation.routing import coordinate_distance_km, route_distance_km
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.reporting import _create_drone_plans, _create_metrics
from geoplan.scheduling.models import MissionScheduleRequest
from geoplan.scheduling.scheduler import TimedLeg, _routes_conflict, _timed_legs


def adapt(
    problem: Problem, metadata: dict, solution: Solution
) -> tuple[PlanRequest, list[WorkSegment], list[Mission]]:
    """Reconstruct known benchmark conditions, with explicit service/transit stages.

    Frozen inputs do not encode arbitrary operational constraints. This adapter
    supports these benchmark fixtures, not arbitrary deployed mission requests.
    Reserve is the fixtures' 20%, endurance is reconstructed from usable minutes.
    """
    coverage = CoveragePlanRequest.model_validate(metadata["coverage_request"])
    template = _base_request(coverage.area)
    scale_case = problem.case_id.startswith("scale_")
    drones = [
        DroneSpec(
            drone_id=p.drone_id,
            name=p.drone_id,
            cruise_speed_kmh=p.speed_kmh,
            maximum_flight_minutes=p.available_minutes / 0.8,
            battery_reserve_percent=20,
            minimum_turnaround_minutes=p.turnaround_minutes,
            supported_tasks=[MissionTask.RGB],
        )
        for p in problem.aircraft
    ]
    if scale_case:
        originals = {d.drone_id: d for d in template.drones}
        drones = [originals[p.drone_id] for p in problem.aircraft]
        for plane, drone in zip(problem.aircraft, drones, strict=True):
            usable = (
                drone.maximum_flight_minutes
                * (drone.initial_charge_percent - drone.battery_reserve_percent)
                / 100
            )
            if (
                abs(usable - plane.available_minutes) > 1e-8
                or drone.cruise_speed_kmh != plane.speed_kmh
            ):
                raise ValueError("Frozen scale fixture no longer matches its operational template.")
    home = FlightSite(site_id="home", name="Home", coordinate=problem.aircraft[0].home)
    if any(p.home != home.coordinate for p in problem.aircraft):
        raise ValueError("Pipeline adapter currently requires the fixtures' common home.")
    request = PlanRequest(
        criterion=problem.criterion,
        drones=drones,
        coverage_areas=[coverage.area],
        survey_parameters=coverage.parameters,
        mission_altitude_m=coverage.mission_altitude_m,
        task=MissionTask.RGB,
        no_fly_zones=list(problem.exclusions),
        wind=problem.wind,
        departure_site=home,
        landing_site=home,
        completion_deadline_minutes=problem.deadline_minutes,
        turnaround_minutes=template.turnaround_minutes if scale_case else 0,
        maximum_active_drones=template.maximum_active_drones if scale_case else None,
        minimum_separation_m=template.minimum_separation_m
        if problem.case_id.startswith("scale_")
        else 50,
        position_uncertainty_m=template.position_uncertainty_m
        if problem.case_id.startswith("scale_")
        else 0,
        start_time_uncertainty_seconds=template.start_time_uncertainty_seconds
        if problem.case_id.startswith("scale_")
        else 0,
        speed_uncertainty_percent=template.speed_uncertainty_percent
        if problem.case_id.startswith("scale_")
        else 0,
    )
    sensor = coverage.parameters.sensor
    trigger = (
        coverage.mission_altitude_m
        * sensor.sensor_height_mm
        / sensor.focal_length_mm
        * (1 - coverage.parameters.forward_overlap_percent / 100)
    )
    model = CostModel(problem)
    if not model.validate_geometry(solution):
        raise ValueError("Cannot adapt an invalid fixed-track solution.")
    segments, missions = [], []
    for index, trip in enumerate(solution):
        plane = problem.aircraft[trip.aircraft]
        cost = model.trip(trip)
        assert cost is not None
        stages = [MissionStage(stage_type=MissionStageType.TAKEOFF, coordinates=[plane.home])]
        previous = plane.home
        for token in trip.visits:
            start, end = model.endpoints(token)
            path = model.path(previous, start)
            assert path is not None
            if path[1] > 1e-10:
                stages.append(
                    MissionStage(
                        stage_type=MissionStageType.TRANSIT,
                        coordinates=path[0],
                        duration_minutes=model.leg(trip.aircraft, previous, start)[0],
                        leg_durations_minutes=route_leg_durations_minutes(
                            path[0], path[1], plane.speed_kmh, problem.wind
                        ),
                    )
                )
            stages.append(
                MissionStage(
                    stage_type=MissionStageType.SURVEY,
                    coordinates=[start, end],
                    duration_minutes=model.service(trip.aircraft, token)[0],
                    leg_durations_minutes=[model.service(trip.aircraft, token)[0]],
                )
            )
            previous = end
        tail = model.path(previous, plane.home)
        assert tail is not None
        stages.append(
            MissionStage(
                stage_type=MissionStageType.LANDING,
                coordinates=tail[0],
                duration_minutes=cost.returning,
                leg_durations_minutes=route_leg_durations_minutes(
                    tail[0], tail[1], plane.speed_kmh, problem.wind
                ),
            )
        )
        route = []
        for stage in stages:
            for point in stage.coordinates:
                if not route or point != route[-1]:
                    route.append(point)
        survey = sum(model.track_distances[t // 2] for t in trip.visits)
        distance = route_distance_km(route)
        segment_id = f"productive-{index}"
        segments.append(
            WorkSegment(
                segment_id=segment_id,
                distance_km=distance,
                route=route,
                coverage_area_id=coverage.area.area_id,
                strip_spacing_m=metadata["strip_spacing_m"],
            )
        )
        battery = cost.total / (plane.available_minutes / 0.8) * 100
        missions.append(
            Mission(
                drone_id=plane.drone_id,
                segment_id=segment_id,
                duration_minutes=cost.total,
                distance_km=distance,
                route=route,
                altitude_m=coverage.mission_altitude_m,
                cruise_speed_kmh=plane.speed_kmh,
                effective_speed_kmh=distance / cost.total * 60,
                estimated_battery_percent=battery,
                remaining_battery_percent=100 - battery,
                battery_reserve_margin_percent=80 - battery,
                survey_distance_km=survey,
                transit_distance_km=distance - survey,
                survey_duration_minutes=cost.survey,
                transit_duration_minutes=cost.deadhead,
                image_count=ceil(survey * 1000 / trigger) + 1,
                departure_site=home,
                landing_site=home,
                stages=stages,
            )
        )
    return request, segments, missions


def service_coverage_percent(request: PlanRequest, missions: list[Mission]) -> float:
    """Union individual service swaths, never bridge separate SURVEY stages."""
    assert request.survey_parameters is not None
    area = request.coverage_areas[0]
    projection = projection_for_coordinates(area.coordinates)
    target = polygon_from_coordinates(area.coordinates, area.interior_rings, projection)
    for zone in request.no_fly_zones:
        target = target.difference(
            polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection).buffer(
                zone.safety_margin_m + 0.5, join_style="mitre"
            )
        )
    parameters = request.survey_parameters
    width = min(
        request.mission_altitude_m
        * parameters.sensor.sensor_width_mm
        / parameters.sensor.focal_length_mm,
        parameters.strip_width_m,
    )
    swaths = [
        LineString([projection.project(p) for p in stage.coordinates]).buffer(
            width / 2 + 0.5, cap_style="square"
        )
        for mission in missions
        for stage in mission.stages
        if stage.stage_type is MissionStageType.SURVEY and len(stage.coordinates) >= 2
    ]
    return 100 * unary_union(swaths).intersection(target).area / target.area if swaths else 0.0


def compatibility(problem: Problem, solution: Solution, missions: list[Mission]) -> dict:
    """Check phase timing and camera state independently of the legacy verifier."""
    maximum_error, overspeed, camera_errors = 0.0, 0, 0
    for trip, mission in zip(solution, missions, strict=True):
        projection = projection_for_coordinates(mission.route)
        legs = _timed_legs(mission, 0, projection)
        true_elapsed, leg_index, previous = 0.0, 0, mission.route[0]
        expected_camera = []
        edge_limits = []
        visits = iter(trip.visits)
        for stage in mission.stages:
            limit = (
                problem.tracks[next(visits) // 2].survey_speed_kmh
                if stage.stage_type is MissionStageType.SURVEY
                else None
            )
            for point in stage.coordinates:
                if point != previous:
                    expected_camera.append(stage.stage_type is MissionStageType.SURVEY)
                    edge_limits.append(limit)
                    leg_index += 1
                    previous = point
            true_elapsed += stage.duration_minutes
            estimated = legs[leg_index - 1].end_minutes if leg_index else 0.0
            maximum_error = max(maximum_error, abs(true_elapsed - estimated))
        camera = False
        speed_type, speed_kmh = 0, mission.cruise_speed_kmh
        actual_camera = []
        previous = mission.route[0]
        for command in _mission_commands(mission):
            if command.command == 206:
                camera = bool(command.params[0])
            elif command.command == 178:
                value = command.params[1]
                assert value is not None
                speed_type, speed_kmh = command.params[0], value * 3.6
            elif command.command in (16, 21):
                latitude, longitude = command.params[4], command.params[5]
                assert latitude is not None and longitude is not None
                point = Coordinate(latitude=latitude, longitude=longitude)
                if point != previous:
                    limit = edge_limits[len(actual_camera)]
                    ground_speed = (
                        speed_kmh
                        if speed_type == 1
                        else ground_speed_kmh(previous, point, speed_kmh, problem.wind)
                    )
                    if limit is not None and ground_speed > limit + 1e-8:
                        overspeed += 1
                    actual_camera.append(camera)
                    previous = point
        camera_errors += int(actual_camera != expected_camera)
    return {
        "phase_timing_max_error_minutes": maximum_error,
        "export_survey_overspeed_tracks": overspeed,
        "camera_state_mismatched_missions": camera_errors,
        "phase_timing_consistent": maximum_error <= 0.05,
    }


def phase_conflicts(
    problem: Problem, solution: Solution, missions: list[Mission], minimum_separation_m: float
) -> int:
    """Additional nominal horizontal check with per-leg wind and service-speed timing.

    Reuses the analytic closest-approach kernel, not the legacy time allocation.
    This excludes uncertainty envelopes, terrain and takeoff/climb dynamics.
    """
    projection = projection_for_coordinates([p for m in missions for p in m.route])
    paths = []
    for trip, mission in zip(solution, missions, strict=True):
        elapsed = mission.start_offset_minutes or 0
        visits = iter(trip.visits)
        legs = []
        for stage in mission.stages:
            limit = (
                problem.tracks[next(visits) // 2].survey_speed_kmh
                if stage.stage_type is MissionStageType.SURVEY
                else None
            )
            for a, b in pairwise(stage.coordinates):
                if a == b:
                    continue
                duration = route_duration_minutes(
                    [a, b],
                    route_distance_km([a, b]),
                    problem.aircraft[trip.aircraft].speed_kmh,
                    problem.wind,
                    limit,
                )
                legs.append(
                    TimedLeg(
                        start_minutes=elapsed,
                        end_minutes=elapsed + duration,
                        start_xy=projection.project(a),
                        end_xy=projection.project(b),
                    )
                )
                elapsed += duration
        paths.append((mission, legs))
    return sum(
        _routes_conflict(a, b, minimum_separation_m, 0, 0, 0, 0, 0) is not None
        for (first, a), (second, b) in combinations(paths, 2)
        if first.drone_id != second.drone_id
    )


def serialized_camera_errors(missions: list[Mission], qgc: str, mission_planner: str) -> int:
    """Decode actual exported payloads, independently of the command builder."""
    expected = []
    for mission in missions:
        previous = mission.route[0]
        for stage in mission.stages:
            for point in stage.coordinates:
                if point != previous:
                    expected.append(stage.stage_type is MissionStageType.SURVEY)
                    previous = point
    qgc_commands = [
        (item["command"], item["params"]) for item in json.loads(qgc)["mission"]["items"]
    ]
    mp_commands = [
        (int(fields[3]), [float(p) for p in fields[4:11]])
        for line in mission_planner.splitlines()[1:]
        if (fields := line.split())
    ]
    errors = 0
    for commands in (qgc_commands, mp_commands):
        actual, camera = [], False
        previous = missions[0].route[0]
        for command, params in commands:
            if command == 206:
                camera = params[0] > 0
            elif command in (16, 21, 22):
                point = Coordinate(latitude=params[4], longitude=params[5])
                if command != 22 and coordinate_distance_km(previous, point) > 0.00002:
                    actual.append(camera)
                previous = point
        errors += int(actual != expected)
    return errors


def check_pipeline(problem: Problem, metadata: dict, solution: Solution) -> dict:
    started = perf_counter()
    request, segments, missions = adapt(problem, metadata, solution)
    deps = create_default_dependencies()
    schedule = deps.mission_scheduler.schedule(
        MissionScheduleRequest(
            missions=missions,
            minimum_separation_m=request.minimum_separation_m,
            position_uncertainty_m=request.position_uncertainty_m,
            start_time_uncertainty_seconds=request.start_time_uncertainty_seconds,
            speed_uncertainty_percent=request.speed_uncertainty_percent,
            turnaround_minutes=request.turnaround_minutes,
            turnaround_minutes_by_drone={
                p.drone_id: p.turnaround_minutes for p in problem.aircraft
            },
        )
    )
    audit = deps.safety_auditor.audit(request, segments, schedule.missions)
    assignments = {p.drone_id: [] for p in problem.aircraft}
    for mission in schedule.missions:
        assignments[mission.drone_id].append(mission)
    plans = _create_drone_plans(request.drones, assignments)
    response = PlanResponse(
        criterion=request.criterion,
        drone_plans=plans,
        metrics=_create_metrics(plans, len(plans)),
        safety_audit=audit,
    )
    exports, serialized_errors = 0, 0
    for plan in plans:
        if plan.missions:
            export = ExportRequest(plan=response, drone_id=plan.drone_id)
            build_geojson_export(export)
            build_kml_export(export)
            qgc = build_qgroundcontrol_export(export)
            mp = build_mission_planner_export(export)
            serialized_errors += serialized_camera_errors(plan.missions, qgc.content, mp.content)
            exports += 4
    checks = compatibility(problem, solution, missions)
    scheduled_by_id = {m.segment_id: m for m in schedule.missions}
    nominal_conflicts = phase_conflicts(
        problem,
        solution,
        [scheduled_by_id[m.segment_id] for m in missions],
        request.minimum_separation_m,
    )
    true_coverage = service_coverage_percent(request, missions)
    metrics = CostModel(problem).metrics(solution)
    assert metrics is not None
    scheduled_objective = CostModel(problem).objective(
        metrics["total_minutes"],
        metrics["deadhead_minutes"],
        schedule.makespan_minutes,
        int(metrics["active_aircraft"]),
    )
    return {
        "legacy_audit": audit.model_dump(mode="json"),
        "service_only_coverage_percent": true_coverage,
        "scheduled_makespan_minutes": schedule.makespan_minutes,
        "scheduled_objective": scheduled_objective,
        "scheduled_deadline_met": (
            problem.deadline_minutes is None
            or schedule.makespan_minutes <= problem.deadline_minutes
        ),
        "load_makespan_minutes": metrics["load_makespan_minutes"],
        "resolved_conflicts": schedule.resolved_conflict_count,
        "export_count": exports,
        "phase_nominal_conflict_pairs": nominal_conflicts,
        "serialized_camera_trace_errors": serialized_errors,
        **checks,
        "integration_ready": audit.status.value == "passed"
        and true_coverage >= 99
        and checks["phase_timing_consistent"]
        and checks["export_survey_overspeed_tracks"] == 0
        and checks["camera_state_mismatched_missions"] == 0
        and nominal_conflicts == 0
        and serialized_errors == 0,
        "elapsed_seconds": perf_counter() - started,
    }


def select_pipeline_result(baseline: dict, candidates: list[dict]) -> dict:
    """Protect the original objective after scheduling and require verified outputs."""
    if not baseline["integration_ready"]:
        raise ValueError("Baseline failed compatibility checks; unsafe fallback is forbidden.")
    return min(
        [baseline, *(c for c in candidates if c["integration_ready"])],
        key=lambda c: tuple(c["scheduled_objective"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--criteria", nargs="+")
    parser.add_argument(
        "--methods", nargs="+", help="Candidate methods; baseline is always checked"
    )
    parser.add_argument("--selection", choices=("scheduled", "load"), default="scheduled")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a fresh file.")
    if args.output.with_suffix(".source.py").exists():
        parser.error("Adapter snapshot already exists; choose a fresh output file.")
    cases, candidates = {}, {}
    for path in args.input:
        report = json.loads(path.read_text(encoding="utf-8"))
        for problem, metadata in load_cases(path):
            key = problem.case_id, problem.criterion.value
            if args.cases and problem.case_id not in args.cases:
                continue
            if args.criteria and problem.criterion.value not in args.criteria:
                continue
            if key in cases and asdict(cases[key][0]) != asdict(problem):
                raise ValueError("Pipeline inputs must use identical frozen problems.")
            cases[key] = problem, metadata
            model = CostModel(problem)
            for row in report["rows"]:
                if args.methods and row["method"] not in ("baseline", *args.methods):
                    continue
                if (row["case"], row["criterion"]) == key and row["valid"]:
                    solution = tuple(
                        Trip(t["aircraft"], tuple(t["visits"])) for t in row["solution"]
                    )
                    value = model.score(solution)
                    method_key = (*key, row["method"])
                    if method_key not in candidates or value < candidates[method_key][0]:
                        candidates[method_key] = value, solution, row["method"], row["seed"]
    output = {
        "filters": {"cases": args.cases, "criteria": args.criteria, "methods": args.methods},
        "scope": "fixed-track adapter; no terrain/permission-window/alternate-site certification",
        "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.input},
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rows": [],
        "selected": [],
        "selection": args.selection,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".source.py").write_bytes(Path(__file__).read_bytes())
    source_hashes = {}
    source_root = Path(__file__).resolve().parents[1] / "src"
    snapshot_root = args.output.with_suffix(".source")
    for source in source_root.rglob("*.py"):
        relative = source.relative_to(source_root)
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        source_hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    output["production_source_sha256"] = source_hashes
    for key, (problem, metadata) in cases.items():
        variants = sorted(
            (
                value
                for method_key, value in candidates.items()
                if method_key[:2] == key and method_key[2] != "baseline"
            ),
            key=lambda value: (value[0], value[2], value[3]),
        )
        if args.selection == "load":
            variants = variants[:1]
        print(f"Pipeline {key}/baseline", flush=True)
        base = {
            "case": key[0],
            "criterion": key[1],
            "method": "baseline",
            "role": "baseline",
            "seed": 0,
            **check_pipeline(problem, metadata, problem.baseline),
        }
        base["scheduled_objective_not_worse"] = True
        output["rows"].append(base)
        checked, seen = [], {problem.baseline}
        for _, solution, method, seed in variants:
            if solution in seen:
                continue
            seen.add(solution)
            print(f"Pipeline {key}/{method}", flush=True)
            result = check_pipeline(problem, metadata, solution)
            result["scheduled_objective_not_worse"] = (
                result["scheduled_objective"] <= base["scheduled_objective"]
            )
            result["integration_ready"] &= result["scheduled_objective_not_worse"]
            row = {
                "case": key[0],
                "criterion": key[1],
                "method": method,
                "role": "candidate",
                "seed": seed,
                **result,
            }
            checked.append(row)
            output["rows"].append(row)
            args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
        chosen = select_pipeline_result(base, checked)
        output["selected"].append(
            {
                "case": key[0],
                "criterion": key[1],
                "method": chosen["method"],
                "seed": chosen["seed"],
                "baseline_kept": chosen is base,
                "scheduled_objective": chosen["scheduled_objective"],
                "scheduled_makespan_minutes": chosen["scheduled_makespan_minutes"],
                "baseline_makespan_minutes": base["scheduled_makespan_minutes"],
            }
        )
        args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
