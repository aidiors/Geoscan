"""Shared result predicates and objectives for already scheduled plans."""

from math import fsum

from geoplan.domain.enums import SafetyAuditStatus
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.routing.model import routing_objective


def plan_is_audited(plan: PlanResponse) -> bool:
    return plan.safety_audit is not None and plan.safety_audit.status is SafetyAuditStatus.PASSED


def scheduled_plan_objective(request: PlanRequest, plan: PlanResponse) -> tuple[float, ...]:
    missions = [mission for drone in plan.drone_plans for mission in drone.missions]
    total = fsum(mission.duration_minutes for mission in missions)
    transit = fsum(mission.transit_duration_minutes or 0 for mission in missions)
    completion = (
        plan.schedule.makespan_minutes if plan.schedule else plan.metrics.completion_minutes
    )
    active = float(plan.metrics.active_drone_count)
    return routing_objective(
        request.criterion,
        request.completion_deadline_minutes,
        total,
        transit,
        completion,
        int(active),
    )
