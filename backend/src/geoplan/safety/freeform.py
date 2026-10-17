"""Validate finite frames from scheduled freeform missions, independently of their generator."""

from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.diagnostics import SafetyAuditCheck
from geoplan.domain.enums import SafetyAuditStatus, SafetyCheckCode
from geoplan.domain.mission import Mission
from geoplan.planning.constraints import _constraints_for_segment
from geoplan.planning.models import PlanRequest
from geoplan.routing.photography import screen_mission


def freeform_coverage_check(
    request: PlanRequest,
    segments: list[WorkSegment],
    missions: list[Mission],
) -> SafetyAuditCheck:
    sources = {s.segment_id: s for s in segments}
    failed, percentages = [], []
    for mission in missions:
        segment = sources.get(mission.segment_id)
        area = next(
            (
                a
                for a in request.coverage_areas
                if segment and a.area_id == segment.coverage_area_id
            ),
            None,
        )
        if segment is None or area is None or request.survey_parameters is None:
            failed.append(mission.segment_id)
            continue
        try:
            result = screen_mission(
                CoveragePlanRequest(
                    area=area,
                    parameters=request.survey_parameters,
                    mission_altitude_m=mission.altitude_m,
                    wind=request.wind,
                    exclusions=_constraints_for_segment(segment, request),
                ),
                mission,
            )
            passed = result["screen_passed"] and mission.photo_capture_route_indices is not None
            percentages.append(result["image_coverage_percent"])
        except ValueError, IndexError:
            passed = False
        if not passed:
            failed.append(mission.segment_id)
    return SafetyAuditCheck(
        code=SafetyCheckCode.COVERAGE_COMPLETENESS,
        status=SafetyAuditStatus.FAILED if failed else SafetyAuditStatus.PASSED,
        measured_value=round(min(percentages, default=0), 4),
        required_value=99.99,
        unit="percent",
        affected_ids=failed,
        message=(
            "Свободное покрытие проверено по конечным кадрам, шагу и аппаратному интервалу."
            if not failed
            else "Свободное покрытие или план фотосъёмки не прошли проверку."
        ),
    )
