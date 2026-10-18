import json
from dataclasses import replace
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SafetyAuditStatus
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.mission import Mission
from geoplan.flight.trajectory import mission_route_clock
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.routing.freeform import GeometryModel
from geoplan.safety.audit import _coverage_check
from scenarios.freeform import dataset


def circle_request() -> PlanRequest:
    case = next(c for c in dataset("holdout", 1) if c.case_id == "holdout_circle64")
    model = GeometryModel(case)
    return PlanRequest(
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
            site_id="base", name="Base", coordinate=model.coordinate(case.home)
        ),
        landing_site=FlightSite(
            site_id="land", name="Landing", coordinate=model.coordinate(case.landing)
        ),
    )


def post(request, dependencies=None):
    with TestClient(
        create_app(dependencies or create_default_dependencies(), serve_frontend=False)
    ) as client:
        response = client.post("/v1/plans", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    return PlanResponse.model_validate(response.json())


@pytest.fixture(scope="module")
def planned():
    request = circle_request()
    return request, post(request)


def test_http_selects_audited_freeform_route_with_an_explicit_camera_clock(planned):
    request, plan = planned
    assert plan.safety_audit.status is SafetyAuditStatus.PASSED
    assert plan.optimization.scope == "freeform_coverage_route_pool"
    assert plan.optimization.optimality == "heuristic"
    summary = plan.optimization.productive_routing
    assert summary.selected_variant.startswith("freeform:")
    assert plan.metrics.total_flight_minutes < summary.baseline_total_flight_minutes
    mission = plan.drone_plans[0].missions[0]
    clock = mission_route_clock(mission).offsets_minutes
    captures = mission.photo_capture_route_indices
    assert len(captures) == mission.image_count > 100
    assert all((clock[b] - clock[a]) * 60 >= 1 - 1e-6 for a, b in pairwise(captures))
    assert mission.route[0] == request.departure_site.coordinate
    assert mission.route[-1] == request.landing_site.coordinate
    assert mission.battery_reserve_margin_percent >= 0


def test_all_exports_preserve_capture_positions_and_single_image_commands(planned):
    _, plan = planned
    drone = next(d for d in plan.drone_plans if d.missions)
    mission = drone.missions[0]
    with TestClient(create_app(create_default_dependencies(), serve_frontend=False)) as client:
        exports = {
            name: client.post(
                f"/v1/exports/{name}",
                json={"plan": plan.model_dump(mode="json"), "drone_id": drone.drone_id},
            )
            for name in ("geojson", "kml", "qgroundcontrol", "mission-planner")
        }
    for response in exports.values():
        assert response.status_code == 200, response.text
    assert (
        exports["geojson"].json()["features"][0]["properties"]["photo_capture_route_indices"]
        == mission.photo_capture_route_indices
    )
    assert "photo_capture_route_indices" in exports["kml"].json()["content"]
    qgc = json.loads(exports["qgroundcontrol"].json()["content"])
    items = qgc["mission"]["items"]
    captures = [i for i in items if i["command"] == 2000]
    assert len(captures) == mission.image_count
    assert [i["params"][3] for i in captures] == list(range(1, mission.image_count + 1))
    assert all(i["params"][2] == 1 for i in captures)
    assert not any(i["command"] == 206 for i in items)
    lines = exports["mission-planner"].json()["content"].splitlines()[1:]
    assert sum(int(line.split()[3]) == 2000 for line in lines) == mission.image_count
    from geoplan.geodata.exporters import _autopilot_commands

    repeated = [c for c in _autopilot_commands([mission, mission]) if c.command == 2000]
    assert [c.params[3] for c in repeated] == list(range(1, 2 * mission.image_count + 1))


def test_freeform_audit_rejects_missing_capture_even_when_swaths_still_cover(planned):
    request, plan = planned
    from geoplan.coverage.models import WorkSegment

    mission = plan.drone_plans[0].missions[0]
    data = mission.model_dump()
    data["photo_capture_route_indices"] = data["photo_capture_route_indices"][1:]
    data["image_count"] -= 1
    broken = Mission.model_validate(data)
    segment = WorkSegment(
        segment_id=mission.segment_id,
        route=request.coverage_areas[0].coordinates[:2],
        distance_km=1,
        coverage_area_id=request.coverage_areas[0].area_id,
        coverage_pattern="contours",
    )
    assert _coverage_check(request, [segment], [broken]).status is SafetyAuditStatus.FAILED


def test_failed_freeform_audit_cannot_replace_the_existing_plan():
    deps = create_default_dependencies()

    class RejectFreeform:
        def audit(self, request, segments, missions):
            audit = deps.safety_auditor.audit(request, segments, missions)
            return (
                audit.model_copy(update={"status": SafetyAuditStatus.FAILED})
                if any(s.coverage_pattern is not None for s in segments)
                else audit
            )

    plan = post(circle_request(), replace(deps, safety_auditor=RejectFreeform()))
    assert plan.safety_audit.status is SafetyAuditStatus.PASSED
    assert plan.optimization.scope != "freeform_coverage_route_pool"


def test_cost_dominated_candidates_are_rejected_before_capture_geometry():
    from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
    from geoplan.planning.freeform_costs import EndpointCostProtection

    class RejectCosts(EndpointCostProtection):
        def allows(self, missions, baseline):
            return False

    class UnexpectedPhotography:
        def prepare(self, request, mission):
            raise AssertionError("A cost-dominated candidate must not expand camera waypoints")

    optimizer = AuditedFreeformRouteOptimizer(
        cost_protection=RejectCosts(), photography=UnexpectedPhotography()
    )
    dependencies = replace(create_default_dependencies(), freeform_optimizer=optimizer)
    plan = post(circle_request(), dependencies)
    assert plan.safety_audit.status is SafetyAuditStatus.PASSED
    assert plan.optimization.scope != "freeform_coverage_route_pool"


def test_http_uses_the_injected_freeform_optimizer():
    class RetainReference:
        def __init__(self):
            self.calls = 0

        def improve(self, request, source, drones, baseline, build, evaluate, reference=None):
            self.calls += 1
            return baseline

    optimizer = RetainReference()
    dependencies = replace(create_default_dependencies(), freeform_optimizer=optimizer)
    plan = post(circle_request(), dependencies)
    assert optimizer.calls == 1
    assert plan.safety_audit.status is SafetyAuditStatus.PASSED
    assert plan.optimization.scope != "freeform_coverage_route_pool"


def test_diverse_shortlist_recovers_when_fixed_track_alternatives_are_rejected():
    from geoplan.planning.freeform import AuditedFreeformRouteOptimizer
    from geoplan.routing.pattern_pool import CoverageCandidatePool, DiverseCoverageCandidateSelector
    from scenarios.planning import productive_survey_request

    dependencies = create_default_dependencies()

    class RejectFixedTracks:
        def audit(self, request, segments, missions):
            audit = dependencies.safety_auditor.audit(request, segments, missions)
            return (
                audit.model_copy(update={"status": SafetyAuditStatus.FAILED})
                if any("-productive-" in mission.segment_id for mission in missions)
                else audit
            )

    plans = []
    for limit in (6, 18):
        optimizer = AuditedFreeformRouteOptimizer(
            pool=CoverageCandidatePool(
                selector=DiverseCoverageCandidateSelector(maximum_candidates=limit)
            )
        )
        plans.append(
            post(
                productive_survey_request(),
                replace(
                    dependencies, safety_auditor=RejectFixedTracks(), freeform_optimizer=optimizer
                ),
            )
        )
    old, expanded = plans
    assert all(plan.safety_audit.status is SafetyAuditStatus.PASSED for plan in plans)
    assert old.optimization.productive_routing.selected_variant == "baseline"
    assert expanded.optimization.scope == "freeform_coverage_route_pool"
    assert expanded.metrics.total_flight_minutes < old.metrics.total_flight_minutes * 0.8
    assert expanded.metrics.completion_minutes < old.metrics.completion_minutes
