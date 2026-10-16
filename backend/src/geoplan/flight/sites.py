from __future__ import annotations

from geoplan.domain.fleet import FlightSite
from geoplan.planning.models import PlanRequest


def _eligible_departure_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite | None]:
    candidates = [
        site
        for site in _configured_departure_sites(request)
        if _site_supports_drone(site, drone_id)
    ]
    eligible: list[FlightSite | None] = []
    eligible.extend(candidates)
    return eligible if eligible else [None]


def _eligible_landing_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite | None]:
    candidates = [
        site
        for site in _configured_primary_landing_sites(request)
        if _site_supports_drone(site, drone_id)
    ]
    eligible: list[FlightSite | None] = []
    eligible.extend(candidates)
    return eligible if eligible else [None]


def _eligible_alternate_sites(
    request: PlanRequest,
    drone_id: str,
) -> list[FlightSite]:
    return [
        site for site in request.alternate_landing_sites if _site_supports_drone(site, drone_id)
    ]


def _site_supports_drone(site: FlightSite, drone_id: str) -> bool:
    return not site.assigned_drone_ids or drone_id in site.assigned_drone_ids


def _configured_departure_sites(request: PlanRequest) -> list[FlightSite]:
    return [site for site in [request.departure_site, *request.departure_sites] if site is not None]


def _configured_primary_landing_sites(request: PlanRequest) -> list[FlightSite]:
    return [site for site in [request.landing_site, *request.landing_sites] if site is not None]
