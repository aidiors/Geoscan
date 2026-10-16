from __future__ import annotations

import json
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import MissionTask, OptimizationCriterion, SafetyCheckCode
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.geodata.exporters import CanonicalMissionExportVerifier
from geoplan.planning.models import PlanRequest
from geoplan.safety.audit import IndependentPlanSafetyAuditor
from geoplan.scheduling.models import MissionScheduleRequest
from geoplan.scheduling.scheduler import ConflictAwareMissionScheduler


class RobustDeconflictionMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    priority_makespan_minutes: float
    two_agent_cbs_reference_makespan_minutes: float
    cbs_improvement_minutes: float
    resolved_conflict_types: list[str]
    conflict_marker_count: int
    independent_nominal_route_audit_passed: bool
    elapsed_ms: float


def run_experiment() -> list[RobustDeconflictionMeasurement]:
    return [
        _measure("speed_uncertainty", _speed_uncertainty_missions(), speed_uncertainty=1),
        _measure("shared_flight_site", _shared_site_missions(), site_occupancy_seconds=120),
        _measure("critical_emergency_corridor", _emergency_corridor_missions()),
        _measure("sampled_turn_geometry", _sampled_turn_missions()),
        _measure("priority_vs_two_agent_cbs", _priority_sensitive_missions()),
    ]


def _measure(
    scenario: str,
    missions: list[Mission],
    speed_uncertainty: float = 0,
    site_occupancy_seconds: float = 0,
) -> RobustDeconflictionMeasurement:
    started = perf_counter()
    request = MissionScheduleRequest(
        missions=missions,
        minimum_separation_m=50,
        speed_uncertainty_percent=speed_uncertainty,
        flight_site_occupancy_seconds=site_occupancy_seconds,
        turnaround_minutes=0,
        delay_step_seconds=1,
    )
    priority = ConflictAwareMissionScheduler().schedule(request)
    opposite_priority_missions = [
        missions[0].model_copy(update={"drone_id": "priority-2"}),
        missions[1].model_copy(update={"drone_id": "priority-1"}),
    ]
    opposite_priority = ConflictAwareMissionScheduler().schedule(
        request.model_copy(update={"missions": opposite_priority_missions})
    )
    reference_makespan = min(priority.makespan_minutes, opposite_priority.makespan_minutes)
    audit_request = _audit_request(missions, speed_uncertainty, site_occupancy_seconds)
    audit = IndependentPlanSafetyAuditor(CanonicalMissionExportVerifier()).audit(
        audit_request,
        audit_request.segments,
        priority.missions,
    )
    separation = next(
        check for check in audit.checks if check.code is SafetyCheckCode.DRONE_SEPARATION
    )
    return RobustDeconflictionMeasurement(
        scenario=scenario,
        priority_makespan_minutes=priority.makespan_minutes,
        two_agent_cbs_reference_makespan_minutes=reference_makespan,
        cbs_improvement_minutes=round(
            priority.makespan_minutes - reference_makespan,
            3,
        ),
        resolved_conflict_types=sorted(
            {conflict.conflict_type.value for conflict in priority.resolved_conflicts}
        ),
        conflict_marker_count=len(priority.resolved_conflicts),
        independent_nominal_route_audit_passed=separation.status.value == "passed",
        elapsed_ms=round((perf_counter() - started) * 1_000, 3),
    )


def _speed_uncertainty_missions() -> list[Mission]:
    return [
        _mission("alpha", "north", [(37.39, 55.7), (37.41, 55.7)]),
        _mission("bravo", "south", [(37.39, 55.70055), (37.41, 55.70055)]),
    ]


def _shared_site_missions() -> list[Mission]:
    site = FlightSite(
        site_id="base",
        name="Base",
        coordinate=Coordinate(longitude=37.4, latitude=55.7),
    )
    return [
        _mission("alpha", "west", [(37.38, 55.69), (37.39, 55.69)]).model_copy(
            update={"departure_site": site}
        ),
        _mission("bravo", "east", [(37.41, 55.71), (37.42, 55.71)]).model_copy(
            update={"departure_site": site}
        ),
    ]


def _emergency_corridor_missions() -> list[Mission]:
    protected = _mission("alpha", "north", [(37.39, 55.71), (37.41, 55.71)]).model_copy(
        update={
            "critical_alternate_route": _coordinates([(37.4, 55.706), (37.4, 55.694)]),
            "critical_alternate_start_offset_minutes": 0,
            "critical_alternate_duration_minutes": 10,
        }
    )
    crossing = _mission("bravo", "crossing", [(37.39, 55.7), (37.41, 55.7)])
    return [protected, crossing]


def _sampled_turn_missions() -> list[Mission]:
    return [
        _mission(
            "alpha",
            "curved",
            [(37.39, 55.706), (37.4, 55.7), (37.41, 55.706)],
        ),
        _mission("bravo", "vertical", [(37.4, 55.694), (37.4, 55.706)]),
    ]


def _priority_sensitive_missions() -> list[Mission]:
    return [
        _mission("alpha", "short", [(37.392, 55.697), (37.392, 55.703)], duration=2),
        _mission("bravo", "long", [(37.39, 55.7), (37.41, 55.7)], duration=10),
    ]


def _mission(
    drone_id: str,
    segment_id: str,
    points: list[tuple[float, float]],
    duration: float = 10,
) -> Mission:
    return Mission(
        drone_id=drone_id,
        segment_id=segment_id,
        duration_minutes=duration,
        distance_km=1,
        route=_coordinates(points),
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
    )


def _coordinates(points: list[tuple[float, float]]) -> list[Coordinate]:
    return [Coordinate(longitude=longitude, latitude=latitude) for longitude, latitude in points]


def _audit_request(
    missions: list[Mission],
    speed_uncertainty: float,
    site_occupancy_seconds: float,
) -> PlanRequest:
    return PlanRequest(
        criterion=OptimizationCriterion.COMPLETION_TIME,
        drones=[
            DroneSpec(
                drone_id=mission.drone_id,
                name=mission.drone_id,
                cruise_speed_kmh=60,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                supported_tasks=[MissionTask.RGB],
            )
            for mission in missions
        ],
        segments=[
            WorkSegment(
                segment_id=mission.segment_id,
                distance_km=mission.distance_km,
                route=mission.route,
            )
            for mission in missions
        ],
        mission_altitude_m=120,
        task=MissionTask.RGB,
        minimum_separation_m=50,
        speed_uncertainty_percent=speed_uncertainty,
        flight_site_occupancy_seconds=site_occupancy_seconds,
    )


if __name__ == "__main__":
    print(json.dumps([item.model_dump() for item in run_experiment()], indent=2))
