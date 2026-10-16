from __future__ import annotations

from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import ErrorCode
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.navigation.routing import RouteGeometryError, build_safe_route


def _safe_route(
    route: list[Coordinate],
    constraints: list[NoFlyZone],
    segment_id: str,
) -> list[Coordinate]:
    try:
        return build_safe_route(route, constraints)
    except RouteGeometryError as error:
        raise PlanningError(
            code=ErrorCode.ROUTE_BLOCKED,
            message=f"Для участка {segment_id} не найден безопасный транзит.",
        ) from error
