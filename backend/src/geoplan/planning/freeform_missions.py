"""Build and photograph freeform candidates through explicit planning ports."""

from dataclasses import dataclass
from math import hypot

from geoplan.coverage.models import CoveragePlanRequest, WorkSegment
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.mission import Mission
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.freeform_ports import MissionCandidateBuilder
from geoplan.routing.freeform import GeometryModel
from geoplan.routing.pattern_pool import RankedCoverageCandidate
from geoplan.routing.photography import screen_mission, with_capture_waypoints


@dataclass(frozen=True)
class PreparedFreeformMission:
    segment: WorkSegment
    mission: Mission


@dataclass(frozen=True)
class FreeformMissionFactory:
    def build(
        self,
        model: GeometryModel,
        template: WorkSegment,
        candidate: RankedCoverageCandidate,
        drone: DroneSpec,
        identifier: str,
        builder: MissionCandidateBuilder,
    ) -> PreparedFreeformMission | None:
        service = [leg for leg in candidate.legs if leg.phase == "survey"]
        internal = [leg for leg in candidate.legs if leg.phase in ("survey", "connection")]
        if not internal or not service:
            return None
        path = [
            model.coordinate(internal[0].start),
            *(model.coordinate(leg.end) for leg in internal),
        ]
        tracks = [(model.coordinate(leg.start), model.coordinate(leg.end)) for leg in service]
        ratio = min(
            route_distance_km(list(track))
            * 1000
            / hypot(leg.end[0] - leg.start[0], leg.end[1] - leg.start[1])
            for track, leg in zip(tracks, service, strict=True)
        )
        segment = template.model_copy(
            update={
                "segment_id": identifier,
                "route": path,
                "alternative_routes": [],
                "survey_tracks": tracks,
                "distance_km": route_distance_km(path),
                "image_count": None,
                "survey_speed_limit_kmh": model.survey_cap * min(1, ratio) * (1 - 1e-9),
                "strip_spacing_m": model.spacing,
                "computed_gsd_cm": model.width
                / model.case.request.parameters.sensor.image_width_px
                * 100,
                "coverage_pattern": candidate.pattern.family.value,
                "flight_direction_degrees": None,
            }
        )
        mission = builder(segment, drone)
        return PreparedFreeformMission(segment, mission) if mission is not None else None


@dataclass(frozen=True)
class AuditedMissionPhotography:
    def prepare(self, request: CoveragePlanRequest, mission: Mission) -> Mission | None:
        photographed = with_capture_waypoints(
            mission, request.parameters.sensor.minimum_trigger_interval_s
        )
        return photographed if screen_mission(request, photographed)["screen_passed"] else None
