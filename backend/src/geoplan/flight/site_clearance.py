"""Conservative clearance of a vertical column above a multirotor site."""

from dataclasses import dataclass

from shapely.geometry import MultiPoint, Point

from geoplan.domain.airspace import AllowedAirspace, FlightObstacle, NoFlyZone
from geoplan.domain.enums import AltitudeReference, ValidityMode
from geoplan.domain.geo import Coordinate
from geoplan.navigation.geometry import polygon_from_coordinates, projection_for_coordinates


def _covers(intervals: list[tuple[float, float]], lower: float, upper: float) -> bool:
    edge = lower
    for start, end in sorted(intervals):
        if start > edge + 1e-7:
            return False
        edge = max(edge, end)
        if edge >= upper - 1e-7:
            return True
    return False


@dataclass(frozen=True)
class VerticalSiteClearance:
    def allows(
        self,
        site: Coordinate,
        ground_msl_m: float,
        flight_msl_m: float,
        height_agl_m: float,
        uncertainty_m: float,
        airspaces: list[AllowedAirspace],
        zones: list[NoFlyZone],
        obstacles: list[FlightObstacle],
    ) -> bool:
        projection = projection_for_coordinates([site])
        point = Point(projection.project(site))
        bounds = {
            AltitudeReference.MSL: (ground_msl_m - uncertainty_m, flight_msl_m),
            AltitudeReference.AGL: (0.0, height_agl_m),
        }
        if airspaces:
            intervals: dict[AltitudeReference, list[tuple[float, float]]] = {
                AltitudeReference.MSL: [],
                AltitudeReference.AGL: [],
            }
            for space in airspaces:
                # Time-window corridors need a separate launch/arrival legality model.
                if space.validity is not None and space.validity.mode is not ValidityMode.PERMANENT:
                    continue
                if (
                    polygon_from_coordinates(space.coordinates, [], projection)
                    .buffer(0.05)
                    .covers(point)
                ):
                    intervals[space.altitude_reference].append(
                        (space.minimum_altitude_m, space.maximum_altitude_m)
                    )
            if not any(_covers(intervals[ref], *bounds[ref]) for ref in bounds):
                return False
        for zone in zones:
            lower, upper = bounds[zone.altitude_reference]
            if zone.minimum_altitude_m is not None and zone.maximum_altitude_m is not None:
                if zone.minimum_altitude_m > upper or zone.maximum_altitude_m < lower:
                    continue
            # Timed prohibitions are conservatively active throughout the column.
            if (
                polygon_from_coordinates(zone.coordinates, zone.interior_rings, projection)
                .buffer(zone.safety_margin_m + 0.5)
                .covers(point)
            ):
                return False
        for obstacle in obstacles:
            top = obstacle.known_top_msl_m
            if top is not None and top + 30 < ground_msl_m - uncertainty_m:
                continue
            hull = MultiPoint([projection.project(p) for p in obstacle.coordinates]).convex_hull
            if hull.buffer(obstacle.safety_radius_m + 0.5).covers(point):
                return False
        return True
