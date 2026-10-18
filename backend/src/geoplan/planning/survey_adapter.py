"""Translate shared survey strips into an individual aircraft's physical route."""

from dataclasses import dataclass
from math import ceil

from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import ErrorCode
from geoplan.domain.fleet import DroneSpec
from geoplan.errors import PlanningError
from geoplan.navigation.curvature import SweepTransitionPlanner
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.fleet_survey import FleetSurveyPolicy
from geoplan.planning.models import PlanRequest
from geoplan.planning.segments import _curvature_constrained_segment


@dataclass(frozen=True)
class MissionSurveyAdapter:
    transitions: SweepTransitionPlanner

    def prepare(
        self, segment: WorkSegment, drone: DroneSpec, request: PlanRequest
    ) -> tuple[WorkSegment, PlanRequest]:
        physical = FleetSurveyPolicy().for_drone(request, drone)
        survey = physical.survey_parameters
        if survey is None or segment.coverage_area_id is None or segment.coverage_pattern:
            return segment, physical
        if segment.survey_tracks is None and len(segment.route) % 2:
            raise PlanningError(
                code=ErrorCode.COVERAGE_GENERATION_FAILED,
                message="Геометрия не содержит полных съёмочных галсов.",
            )
        trigger = (
            physical.mission_altitude_m
            * survey.sensor.sensor_height_mm
            / survey.sensor.focal_length_mm
            * (1 - survey.forward_overlap_percent / 100)
        )
        tracks = segment.survey_tracks or [
            (segment.route[i], segment.route[i + 1]) for i in range(0, len(segment.route), 2)
        ]
        updates = {
            "trigger_spacing_m": trigger,
            "survey_speed_limit_kmh": trigger / survey.sensor.minimum_trigger_interval_s * 3.6,
            "image_count": sum(
                ceil(route_distance_km(list(t)) * 1000 / trigger) + 1 for t in tracks
            ),
        }
        if physical.minimum_turn_radius_m > 0 or drone.installed_sensor is not None:
            updates["survey_tracks"] = tracks
        prepared = segment.model_copy(update=updates)
        if physical.minimum_turn_radius_m > 0:
            area = next(a for a in request.coverage_areas if a.area_id == segment.coverage_area_id)
            # Rebuild from mandatory straight strips, never pair up sampled curve points.
            if segment.survey_tracks is not None:
                prepared = prepared.model_copy(
                    update={"route": [p for t in tracks for p in t], "alternative_routes": []}
                )
            prepared = _curvature_constrained_segment(prepared, area, physical, self.transitions)
        return prepared, physical
