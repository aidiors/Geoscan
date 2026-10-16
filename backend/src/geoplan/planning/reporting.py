from __future__ import annotations

from math import ceil

from geoplan.domain.diagnostics import FeasibilityRecommendation, FeasibilityReport
from geoplan.domain.enums import FeasibilityStatus, RecommendationCode, RecommendationConfidence
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import DronePlan, MaintenanceReport, Mission
from geoplan.planning.models import PlanMetrics, PlanRequest


def _create_drone_plans(
    drones: list[DroneSpec],
    assignments: dict[str, list[Mission]],
) -> list[DronePlan]:
    return [
        DronePlan(
            drone_id=drone.drone_id,
            mission_count=len(assignments[drone.drone_id]),
            total_duration_minutes=round(
                sum(mission.duration_minutes for mission in assignments[drone.drone_id]),
                2,
            ),
            missions=assignments[drone.drone_id],
            autopilot_profile=drone.autopilot_profile,
            maintenance=_maintenance_report(drone, assignments[drone.drone_id]),
        )
        for drone in drones
    ]


def _maintenance_report(drone: DroneSpec, missions: list[Mission]) -> MaintenanceReport:
    projected_flights = drone.completed_flights_since_maintenance + len(missions)
    projected_flight_hours = (
        drone.flight_hours_since_maintenance
        + sum(mission.duration_minutes for mission in missions) / 60
    )
    remaining_flights = (
        max(0, drone.maintenance_interval_flights - projected_flights)
        if drone.maintenance_interval_flights is not None
        else None
    )
    remaining_hours = (
        max(0.0, drone.maintenance_interval_hours - projected_flight_hours)
        if drone.maintenance_interval_hours is not None
        else None
    )
    due_by_flights = remaining_flights == 0 if remaining_flights is not None else False
    due_by_hours = remaining_hours == 0 if remaining_hours is not None else False
    maintenance_due = due_by_flights or due_by_hours
    if drone.maintenance_interval_flights is None and drone.maintenance_interval_hours is None:
        message = "Норматив технического обслуживания для БВС не задан."
    elif maintenance_due:
        message = "После выполнения плана требуется техническое обслуживание."
    else:
        limits = []
        if remaining_flights is not None:
            limits.append(f"{remaining_flights} полётов")
        if remaining_hours is not None:
            limits.append(f"{remaining_hours:.1f} часа налёта")
        message = "До технического обслуживания осталось: " + ", ".join(limits) + "."
    return MaintenanceReport(
        projected_flights=projected_flights,
        projected_flight_hours=round(projected_flight_hours, 2),
        remaining_flights=remaining_flights,
        remaining_hours=round(remaining_hours, 2) if remaining_hours is not None else None,
        maintenance_due_after_plan=maintenance_due,
        message=message,
    )


def _create_metrics(
    drone_plans: list[DronePlan],
    available_drone_count: int,
) -> PlanMetrics:
    durations = [drone_plan.total_duration_minutes for drone_plan in drone_plans]
    scheduled_ends = [
        mission.end_offset_minutes
        for drone_plan in drone_plans
        for mission in drone_plan.missions
        if mission.end_offset_minutes is not None
    ]
    completion_minutes = max(scheduled_ends, default=max(durations))
    total_distance = sum(
        mission.distance_km for drone_plan in drone_plans for mission in drone_plan.missions
    )
    survey_distance = sum(
        mission.survey_distance_km or 0
        for drone_plan in drone_plans
        for mission in drone_plan.missions
    )
    transit_distance = sum(
        mission.transit_distance_km or 0
        for drone_plan in drone_plans
        for mission in drone_plan.missions
    )
    active_drone_count = sum(plan.mission_count > 0 for plan in drone_plans)
    available_fleet_minutes = completion_minutes * available_drone_count
    utilization = sum(durations) / available_fleet_minutes * 100
    return PlanMetrics(
        completion_minutes=round(completion_minutes, 2),
        total_flight_minutes=round(sum(durations), 2),
        total_distance_km=round(total_distance, 3),
        survey_distance_km=round(survey_distance, 3),
        transit_distance_km=round(transit_distance, 3),
        useful_flight_percent=round(
            survey_distance / total_distance * 100 if total_distance > 0 else 0,
            2,
        ),
        average_fleet_utilization_percent=round(utilization, 2),
        active_drone_count=active_drone_count,
        sortie_count=sum(plan.mission_count for plan in drone_plans),
    )


def _create_feasibility_report(
    metrics: PlanMetrics,
    request: PlanRequest,
    eligible_drone_count: int,
) -> FeasibilityReport:
    deadline = request.completion_deadline_minutes
    if deadline is None:
        return FeasibilityReport(
            status=FeasibilityStatus.FEASIBLE,
            message=(
                f"План выполним: используется {metrics.active_drone_count} БВС, "
                f"расчётный срок {metrics.completion_minutes:.1f} мин."
            ),
            maximum_active_drones=request.maximum_active_drones,
            used_drone_count=metrics.active_drone_count,
        )
    deadline_margin = deadline - metrics.completion_minutes
    if deadline_margin >= 0:
        return FeasibilityReport(
            status=FeasibilityStatus.FEASIBLE,
            message=(
                f"План укладывается в срок; резерв {deadline_margin:.1f} мин, "
                f"используется {metrics.active_drone_count} БВС."
            ),
            requested_deadline_minutes=deadline,
            maximum_active_drones=request.maximum_active_drones,
            used_drone_count=metrics.active_drone_count,
            estimated_required_drone_count=metrics.active_drone_count,
            deadline_margin_minutes=round(deadline_margin, 2),
        )
    estimated_required = max(
        metrics.active_drone_count + 1,
        ceil(metrics.total_flight_minutes / deadline),
    )
    if estimated_required <= eligible_drone_count:
        recommendation = f"Оценочно требуется не менее {estimated_required} БВС."
    else:
        recommendation = (
            f"Оценочно требуется не менее {estimated_required} БВС, "
            f"но доступно только {eligible_drone_count}."
        )
    recommendations = [
        FeasibilityRecommendation(
            code=RecommendationCode.EXTEND_DEADLINE,
            message=(
                f"Увеличьте срок минимум до {metrics.completion_minutes:.1f} мин "
                "для текущего рассчитанного плана."
            ),
            confidence=RecommendationConfidence.EXACT,
            parameter="completion_deadline_minutes",
            current_value=deadline,
            required_value=metrics.completion_minutes,
            unit="minutes",
        )
    ]
    if estimated_required <= eligible_drone_count:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.INCREASE_ACTIVE_DRONE_LIMIT,
                message=f"Разрешите оптимизатору использовать до {estimated_required} БВС.",
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="maximum_active_drones",
                current_value=request.maximum_active_drones or metrics.active_drone_count,
                required_value=estimated_required,
                unit="drones",
            )
        )
    else:
        recommendations.append(
            FeasibilityRecommendation(
                code=RecommendationCode.ADD_CAPABLE_DRONE,
                message=(
                    f"Добавьте ориентировочно {estimated_required - eligible_drone_count} "
                    "подходящих БВС либо разделите работы на смены."
                ),
                confidence=RecommendationConfidence.ESTIMATE,
                parameter="drones",
                current_value=eligible_drone_count,
                required_value=estimated_required,
                unit="drones",
            )
        )
    return FeasibilityReport(
        status=FeasibilityStatus.INFEASIBLE,
        message=(
            f"План не укладывается в срок: превышение {-deadline_margin:.1f} мин. {recommendation}"
        ),
        requested_deadline_minutes=deadline,
        maximum_active_drones=request.maximum_active_drones,
        used_drone_count=metrics.active_drone_count,
        estimated_required_drone_count=estimated_required,
        deadline_margin_minutes=round(deadline_margin, 2),
        recommendations=recommendations,
    )
