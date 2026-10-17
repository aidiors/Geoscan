import json
from dataclasses import replace

import pytest

from benchmarks.acceptance_scale import _base_request
from benchmarks.productive_cases import tiny_problems
from benchmarks.productive_model import Track, Trip
from benchmarks.productive_pipeline import (
    adapt,
    check_pipeline,
    compatibility,
    phase_conflicts,
    select_pipeline_result,
    serialized_camera_errors,
    service_coverage_percent,
)
from geoplan.coverage.models import CoverageArea, CoveragePlanRequest
from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import DronePlan
from geoplan.geodata.exporters import build_mission_planner_export, build_qgroundcontrol_export
from geoplan.geodata.models import ExportRequest
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanMetrics, PlanResponse
from geoplan.safety.audit import _coverage_check


def fixture():
    projection = LocalProjection(37, 55)
    area = CoverageArea(
        area_id="gap",
        name="Gap",
        coordinates=[
            projection.unproject(x, y) for x, y in ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        ],
    )
    plan = _base_request(area)
    assert plan.survey_parameters is not None
    coverage = CoveragePlanRequest(
        area=area, parameters=plan.survey_parameters, mission_altitude_m=120
    )
    base = tiny_problems()[0]
    tracks = tuple(
        Track(str(i), projection.unproject(x, y), projection.unproject(x + 100, y), 45)
        for i, (x, y) in enumerate(((100, 100), (800, 800)))
    )
    problem = replace(
        base, tracks=tracks, baseline=(Trip(1, (0, 2)),), maximum_internal_distance_km=None
    )
    return problem, {"coverage_request": coverage.model_dump(mode="json"), "strip_spacing_m": 49.23}


def test_adapter_preserves_exact_service_stages_and_camera_state():
    problem, metadata = fixture()
    _, _, missions = adapt(problem, metadata, problem.baseline)
    survey = [s for m in missions for s in m.stages if s.stage_type is MissionStageType.SURVEY]
    assert [s.coordinates for s in survey] == [[t.start, t.end] for t in problem.tracks]
    assert sum(s.duration_minutes for s in missions[0].stages) == pytest.approx(
        missions[0].duration_minutes
    )
    checks = compatibility(problem, problem.baseline, missions)
    assert checks["camera_state_mismatched_missions"] == 0
    assert checks["export_survey_overspeed_tracks"] == 0
    assert checks["phase_timing_consistent"]


def test_detector_excludes_phantom_bridges_between_separate_service_stages():
    problem, metadata = fixture()
    request, segments, missions = adapt(problem, metadata, problem.baseline)
    measured = service_coverage_percent(request, missions)
    legacy = _coverage_check(request, segments, missions).measured_value
    assert legacy is not None
    assert legacy == pytest.approx(measured, abs=0.01)


def test_full_pipeline_reports_integration_gaps_even_when_export_geometry_matches():
    problem, metadata = fixture()
    report = check_pipeline(problem, metadata, problem.baseline)
    assert report["export_count"] == 4
    # The production scheduler reports minutes rounded to three decimals.
    assert report["scheduled_makespan_minutes"] + 0.001 >= report["load_makespan_minutes"]
    assert not report["integration_ready"]
    assert report["camera_state_mismatched_missions"] == 0
    assert report["serialized_camera_trace_errors"] == 0
    assert report["export_survey_overspeed_tracks"] == 0


def test_pipeline_rejects_a_solution_with_missing_service():
    problem, metadata = fixture()
    with pytest.raises(ValueError, match="invalid fixed-track"):
        adapt(problem, metadata, (Trip(1, (0,)),))


def test_serialized_camera_checker_detects_corrupted_export():
    problem, metadata = fixture()
    _, _, missions = adapt(problem, metadata, problem.baseline)
    mission = missions[0]
    response = PlanResponse(
        criterion=problem.criterion,
        drone_plans=[
            DronePlan(
                drone_id=mission.drone_id,
                mission_count=1,
                total_duration_minutes=mission.duration_minutes,
                missions=missions,
            )
        ],
        metrics=PlanMetrics(
            completion_minutes=mission.duration_minutes,
            total_flight_minutes=mission.duration_minutes,
            total_distance_km=mission.distance_km,
            average_fleet_utilization_percent=0,
        ),
    )
    request = ExportRequest(plan=response, drone_id=mission.drone_id)
    qgc, mp = build_qgroundcontrol_export(request), build_mission_planner_export(request)
    assert serialized_camera_errors(missions, qgc.content, mp.content) == 0
    corrupted = json.loads(qgc.content)
    next(i for i in corrupted["mission"]["items"] if i["command"] == 206 and i["params"][0] > 0)[
        "params"
    ][0] = 0
    assert serialized_camera_errors(missions, json.dumps(corrupted), mp.content) == 1


def test_phase_clock_detects_a_crossing_and_accepts_temporal_separation():
    problem, metadata = fixture()
    projection = LocalProjection(37, 55)
    tracks = tuple(
        Track(str(i), projection.unproject(*a), projection.unproject(*b), 45)
        for i, (a, b) in enumerate((((100, 500), (900, 500)), ((500, 100), (500, 900))))
    )
    fleet = tuple(
        replace(p, speed_kmh=90, home=projection.unproject(100, 100)) for p in problem.aircraft
    )
    problem = replace(
        problem, tracks=tracks, aircraft=fleet, baseline=(Trip(0, (0,)), Trip(1, (2,)))
    )
    _, _, missions = adapt(problem, metadata, problem.baseline)
    missions = [
        m.model_copy(update={"start_offset_minutes": 0, "end_offset_minutes": m.duration_minutes})
        for m in missions
    ]
    assert phase_conflicts(problem, problem.baseline, missions, 50) == 1
    missions[1] = missions[1].model_copy(
        update={"start_offset_minutes": missions[0].duration_minutes + 1}
    )
    assert phase_conflicts(problem, problem.baseline, missions, 50) == 0


def test_final_selection_rejects_better_load_with_worse_scheduled_completion():
    baseline = {
        "integration_ready": True,
        "scheduled_objective": (1610.463, 1537.252, 10, 0, 92.747),
        "load_makespan_minutes": 167.411,
    }
    regression = {
        "integration_ready": True,
        "scheduled_objective": (1615.635, 1541.818, 10, 0, 94.732),
        "load_makespan_minutes": 160.136,
    }
    assert select_pipeline_result(baseline, [regression]) is baseline
    unsafe = {"integration_ready": False, "scheduled_objective": (100, 100, 1, 0, 0)}
    better = {"integration_ready": True, "scheduled_objective": (1500, 1500, 10, 0, 100)}
    assert select_pipeline_result(baseline, [regression, unsafe, better]) is better
    with pytest.raises(ValueError, match="unsafe fallback"):
        select_pipeline_result(unsafe, [better])
