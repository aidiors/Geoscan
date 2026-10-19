from dataclasses import dataclass

import pytest

from geoplan.coverage.models import WorkSegment
from geoplan.domain.airspace import FlightObstacle, NoFlyZone
from geoplan.domain.diagnostics import SafetyAuditCheck, SafetyAuditReport
from geoplan.domain.enums import (
    AltitudeMode,
    ImportedGeometryType,
    MissionTask,
    OptimizationCriterion,
    SafetyAuditStatus,
    SafetyCheckCode,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.planning.constraints import _obstacle_requires_avoidance
from geoplan.planning.models import PlanRequest
from geoplan.safety.audit import ExportVerificationResult, IndependentPlanSafetyAuditor


@dataclass(frozen=True)
class FixedExportVerifier:
    result: ExportVerificationResult

    def verify(self, missions: list[Mission]) -> ExportVerificationResult:
        return self.result


def test_audit_reports_all_independent_checks_for_safe_plan() -> None:
    report = _auditor().audit(_request(), [_segment()], [_mission()])

    assert report.status is SafetyAuditStatus.PASSED
    assert {check.code for check in report.checks} == set(SafetyCheckCode)
    assert report.audited_mission_count == 1
    assert report.assumptions


@pytest.mark.parametrize(
    "mode,height,required",
    [
        (AltitudeMode.ABSOLUTE, 0, False),
        (AltitudeMode.ABSOLUTE, -100, False),
        (AltitudeMode.ABSOLUTE, 90, True),
        (AltitudeMode.ABSOLUTE, None, True),
        (AltitudeMode.RELATIVE_TO_GROUND, 5, True),
        (AltitudeMode.CLAMP_TO_GROUND, 0, True),
        (AltitudeMode.RELATIVE_TO_SEA_FLOOR, 5, True),
        (AltitudeMode.UNKNOWN, 0, True),
    ],
)
def test_obstacle_avoidance_respects_altitude_reference_in_both_planner_and_audit(
    mode, height, required
):
    obstacle = FlightObstacle(
        obstacle_id="tower",
        geometry_type=ImportedGeometryType.POINT,
        coordinates=[Coordinate(longitude=37.005, latitude=55)],
        height_m=height,
        altitude_mode=mode,
    )
    assert _obstacle_requires_avoidance(obstacle, 100) is required
    request = _request().model_copy(update={"obstacles": [obstacle]})
    report = _auditor().audit(request, [_segment()], [_mission()])
    check = _check(report, SafetyCheckCode.OBSTACLE_CLEARANCE)
    assert check.status is (
        SafetyAuditStatus.FAILED if required else SafetyAuditStatus.NOT_APPLICABLE
    )
    if obstacle.known_top_msl_m is None:
        assert any("горизонтальный" in assumption for assumption in report.assumptions)


def test_audit_detects_route_inside_no_fly_zone() -> None:
    zone = NoFlyZone(
        zone_id="restricted",
        safety_margin_m=30,
        coordinates=[
            Coordinate(longitude=37.004, latitude=54.999),
            Coordinate(longitude=37.006, latitude=54.999),
            Coordinate(longitude=37.006, latitude=55.001),
            Coordinate(longitude=37.004, latitude=55.001),
        ],
    )

    report = _auditor().audit(
        _request().model_copy(update={"no_fly_zones": [zone]}),
        [_segment()],
        [_mission()],
    )

    check = _check(report, SafetyCheckCode.NO_FLY_CLEARANCE)
    assert report.status is SafetyAuditStatus.FAILED
    assert check.status is SafetyAuditStatus.FAILED
    assert check.measured_value is not None and check.measured_value < 0


def test_audit_detects_battery_reserve_violation() -> None:
    mission = _mission().model_copy(update={"remaining_battery_percent": 10})

    report = _auditor().audit(_request(), [_segment()], [mission])

    check = _check(report, SafetyCheckCode.BATTERY_RESERVE)
    assert check.status is SafetyAuditStatus.FAILED
    assert check.measured_value == -10
    assert check.unit == "percentage_points"
    assert "п.п." in check.message


def test_audit_detects_simultaneous_crossing_routes() -> None:
    first = _mission()
    second = _mission(
        drone_id="drone-2",
        segment_id="reverse",
        route=list(reversed(first.route)),
    )

    report = _auditor().audit(_request(include_second_drone=True), [_segment()], [first, second])

    check = _check(report, SafetyCheckCode.DRONE_SEPARATION)
    assert check.status is SafetyAuditStatus.FAILED
    assert check.measured_value is not None and check.measured_value < 1
    assert len(check.affected_ids) == 2


def test_audit_accepts_crossing_routes_on_certified_vertical_layers() -> None:
    first = _mission()
    second = _mission(
        drone_id="drone-2",
        segment_id="reverse",
        route=list(reversed(first.route)),
    ).model_copy(
        update={
            "altitude_m": 130,
            "vertical_deconfliction_offset_m": 30,
        }
    )

    report = _auditor().audit(
        _request(include_second_drone=True),
        [_segment()],
        [first, second],
    )

    assert report.status is SafetyAuditStatus.PASSED
    assert _check(report, SafetyCheckCode.DRONE_SEPARATION).status is SafetyAuditStatus.PASSED
    vertical = _check(report, SafetyCheckCode.VERTICAL_SEPARATION)
    assert vertical.status is SafetyAuditStatus.PASSED
    assert vertical.measured_value == 30


def test_audit_rejects_unseparated_vertical_transition_at_shared_departure() -> None:
    first = _mission()
    second = _mission(
        drone_id="drone-2",
        segment_id="same-departure",
        route=first.route,
    ).model_copy(
        update={
            "altitude_m": 130,
            "duration_minutes": 10.2,
            "end_offset_minutes": 10.2,
            "vertical_climb_duration_minutes": 0.1,
            "vertical_descent_duration_minutes": 0.1,
            "vertical_deconfliction_offset_m": 30,
        }
    )

    report = _auditor().audit(
        _request(include_second_drone=True),
        [_segment()],
        [first, second],
    )

    separation = _check(report, SafetyCheckCode.DRONE_SEPARATION)
    assert separation.status is SafetyAuditStatus.FAILED
    assert separation.measured_value is not None
    assert separation.required_value is not None
    assert separation.measured_value < separation.required_value


def test_audit_certifies_bounded_position_uncertainty() -> None:
    first = _mission()
    second = _mission(
        drone_id="drone-2",
        segment_id="parallel",
        route=[
            Coordinate(longitude=37.0, latitude=55.00055),
            Coordinate(longitude=37.01, latitude=55.00055),
        ],
    )
    nominal_request = _request(include_second_drone=True)
    robust_request = nominal_request.model_copy(update={"position_uncertainty_m": 10})

    nominal = _auditor().audit(nominal_request, [_segment()], [first, second])
    robust = _auditor().audit(robust_request, [_segment()], [first, second])

    assert _check(nominal, SafetyCheckCode.DRONE_SEPARATION).status is SafetyAuditStatus.PASSED
    robust_check = _check(robust, SafetyCheckCode.DRONE_SEPARATION)
    assert robust_check.status is SafetyAuditStatus.FAILED
    assert robust_check.measured_value is not None
    assert robust_check.required_value is not None
    assert robust_check.measured_value < robust_check.required_value


def test_audit_surfaces_export_mismatch() -> None:
    verifier = FixedExportVerifier(
        ExportVerificationResult(
            consistent=False,
            message="Injected mismatch.",
            affected_ids=["drone-1:segment"],
        )
    )

    report = IndependentPlanSafetyAuditor(verifier).audit(
        _request(),
        [_segment()],
        [_mission()],
    )

    check = _check(report, SafetyCheckCode.EXPORT_CONSISTENCY)
    assert report.status is SafetyAuditStatus.FAILED
    assert check.status is SafetyAuditStatus.FAILED


def _auditor() -> IndependentPlanSafetyAuditor:
    return IndependentPlanSafetyAuditor(
        FixedExportVerifier(
            ExportVerificationResult(
                consistent=True,
                message="Exports match.",
                affected_ids=[],
            )
        )
    )


def _request(include_second_drone: bool = False) -> PlanRequest:
    drones = [_drone("drone-1")]
    if include_second_drone:
        drones.append(_drone("drone-2"))
    return PlanRequest(
        criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
        drones=drones,
        segments=[_segment()],
        mission_altitude_m=100,
        task=MissionTask.RGB,
        minimum_separation_m=50,
    )


def _drone(drone_id: str) -> DroneSpec:
    return DroneSpec(
        drone_id=drone_id,
        name=drone_id,
        cruise_speed_kmh=60,
        maximum_flight_minutes=60,
        battery_reserve_percent=20,
        supported_tasks=[MissionTask.RGB],
    )


def _segment() -> WorkSegment:
    route = _route()
    return WorkSegment(segment_id="segment", distance_km=0.64, route=route)


def _route() -> list[Coordinate]:
    return [
        Coordinate(longitude=37.0, latitude=55.0),
        Coordinate(longitude=37.01, latitude=55.0),
    ]


def _mission(
    drone_id: str = "drone-1",
    segment_id: str = "segment",
    route: list[Coordinate] | None = None,
) -> Mission:
    return Mission(
        drone_id=drone_id,
        segment_id=segment_id,
        duration_minutes=10,
        distance_km=0.64,
        route=route or _route(),
        altitude_m=100,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=30,
        remaining_battery_percent=70,
        battery_reserve_margin_percent=50,
        start_offset_minutes=0,
        end_offset_minutes=10,
    )


def _check(report: SafetyAuditReport, code: SafetyCheckCode) -> SafetyAuditCheck:
    checks = report.checks
    return next(check for check in checks if check.code is code)
