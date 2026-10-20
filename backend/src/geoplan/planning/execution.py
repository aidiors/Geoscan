"""Canonical preparation, scheduling and independent audit of complete assignments."""

from dataclasses import dataclass

from geoplan.airspace.windows import _schedule_time_windows, _vertical_layers_by_segment_id
from geoplan.coverage.models import WorkSegment
from geoplan.domain.mission import Mission
from geoplan.planning.models import PlanRequest
from geoplan.planning.photography import prepare_photography
from geoplan.planning.schedule_portfolio import AuditedSchedulePortfolio, AuditedScheduleSelection
from geoplan.planning.survey_phases import normalize_reference_missions
from geoplan.safety.audit import PlanSafetyAuditor
from geoplan.scheduling.models import MissionScheduleRequest
from geoplan.scheduling.scheduler import MissionScheduler


@dataclass(frozen=True)
class AuditedMissionEvaluator:
    scheduler: MissionScheduler
    auditor: PlanSafetyAuditor

    def evaluate(
        self, request: PlanRequest, segments: list[WorkSegment], missions: list[Mission]
    ) -> AuditedScheduleSelection:
        if request.minimum_turn_radius_m == 0 and not request.segments:
            missions = normalize_reference_missions(segments, missions)
        missions = prepare_photography(request, missions)
        schedule_request = MissionScheduleRequest(
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
        return AuditedSchedulePortfolio(self.scheduler, self.auditor).select(
            request, segments, schedule_request
        )
