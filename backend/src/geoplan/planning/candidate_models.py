from __future__ import annotations

from dataclasses import dataclass, field

from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.flight.emergency import EmergencyReachability
from geoplan.flight.models import MissionDurationEstimate
from geoplan.flight.takeoff_landing import TakeoffLandingEstimate
from geoplan.flight.terrain import VerticalProfileResult


@dataclass(frozen=True)
class MissionCandidate:
    drone: DroneSpec
    mission: Mission


@dataclass(frozen=True)
class OptimizedMissionRoute:
    survey_route: list[Coordinate]
    transit_route: list[Coordinate]
    landing_route: list[Coordinate]
    departure_site: FlightSite | None
    landing_site: FlightSite | None
    survey_distance_km: float
    transit_distance_km: float
    total_distance_km: float
    duration_estimate: MissionDurationEstimate
    emergency_reachability: EmergencyReachability | None
    vertical_profile: VerticalProfileResult | None
    takeoff_landing: TakeoffLandingEstimate = field(default_factory=TakeoffLandingEstimate)
