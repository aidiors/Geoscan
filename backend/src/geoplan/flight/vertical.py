from __future__ import annotations

from itertools import pairwise

from geoplan.airspace.windows import _profile_altitude
from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import (
    AltitudeReference,
    ErrorCode,
    RecommendationCode,
    RecommendationConfidence,
)
from geoplan.domain.fleet import DroneSpec
from geoplan.domain.geo import Coordinate
from geoplan.domain.terrain import InlineTerrainSource
from geoplan.errors import PlanningError
from geoplan.flight.terrain import (
    InvalidTerrainSourceError,
    TerrainDataUnavailableError,
    VerticalProfilePlanner,
    VerticalProfileResult,
    VerticalRateExceededError,
)
from geoplan.navigation.routing import route_is_inside_allowed_airspaces
from geoplan.planning.models import PlanRequest


def _validate_vertical_airspace(
    profile: VerticalProfileResult | None,
    request: PlanRequest,
    segment_id: str,
) -> None:
    if profile is None or not request.allowed_airspaces:
        return
    for start, end in pairwise(profile.points):
        compatible = [
            airspace
            for airspace in request.allowed_airspaces
            if airspace.minimum_altitude_m <= _profile_altitude(start, airspace.altitude_reference)
            and _profile_altitude(start, airspace.altitude_reference) <= airspace.maximum_altitude_m
            and airspace.minimum_altitude_m
            <= _profile_altitude(end, airspace.altitude_reference)
            <= airspace.maximum_altitude_m
        ]
        if compatible and route_is_inside_allowed_airspaces(
            [start.coordinate, end.coordinate],
            compatible,
        ):
            continue
        raise PlanningError(
            code=ErrorCode.OUTSIDE_ALLOWED_AIRSPACE,
            message=(
                f"Terrain-following profile for segment {segment_id} leaves allowed 3D airspace."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADJUST_MISSION_ALTITUDE,
                    message="Adjust AGL, terrain corridor, or allowed altitude limits.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="mission_altitude_m",
                    current_value=request.mission_altitude_m,
                    affected_ids=[segment_id],
                )
            ],
        )


def _vertical_profile(
    route: list[Coordinate],
    drone: DroneSpec,
    request: PlanRequest,
    planner: VerticalProfilePlanner,
    segment_id: str,
) -> VerticalProfileResult | None:
    if request.altitude_reference is AltitudeReference.MSL:
        return None
    maximum_climb_rate = drone.maximum_climb_rate_mps
    maximum_descent_rate = drone.maximum_descent_rate_mps
    if maximum_climb_rate is None or maximum_descent_rate is None:
        raise PlanningError(
            code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
            message=f"Для БВС {drone.drone_id} не заданы пределы вертикальной скорости.",
        )
    try:
        maximum_ground_speed_kmh = drone.cruise_speed_kmh + (
            request.wind.speed_mps * 3.6 if request.wind is not None else 0
        )
        terrain_source = request.terrain_source or InlineTerrainSource(
            samples=request.terrain_elevation_samples
        )
        return planner.plan(
            route=route,
            target_agl_m=request.mission_altitude_m,
            maximum_ground_speed_kmh=maximum_ground_speed_kmh,
            source=terrain_source,
            sampling_interval_m=request.terrain_sampling_interval_m,
            maximum_interpolation_distance_m=request.terrain_max_interpolation_distance_m,
            maximum_climb_rate_mps=maximum_climb_rate,
            maximum_descent_rate_mps=maximum_descent_rate,
            terrain_vertical_uncertainty_m=request.terrain_vertical_uncertainty_m,
        )
    except (TerrainDataUnavailableError, InvalidTerrainSourceError) as error:
        raise PlanningError(
            code=ErrorCode.TERRAIN_DATA_UNAVAILABLE,
            message=(
                f"Для маршрута участка {segment_id} нет отметки рельефа ближе "
                f"{request.terrain_max_interpolation_distance_m:g} м."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.REVISE_GEOSPATIAL_CONSTRAINTS,
                    message="Добавьте DEM-отметки вдоль всего маршрута и площадок.",
                    confidence=RecommendationConfidence.EXACT,
                    parameter="terrain_elevation_samples",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        ) from error
    except VerticalRateExceededError as error:
        required_climb = error.required_climb_rate_mps
        required_descent = error.required_descent_rate_mps
        raise PlanningError(
            code=ErrorCode.VERTICAL_PROFILE_UNSATISFIED,
            message=(
                f"Segment {segment_id} requires climb {required_climb:.2f} m/s "
                f"and descent {required_descent:.2f} m/s; aircraft limits are "
                f"{maximum_climb_rate:g}/{maximum_descent_rate:g} m/s."
            ),
            recommendations=[
                FeasibilityRecommendation(
                    code=RecommendationCode.ADD_CAPABLE_DRONE,
                    message=(
                        "Use an aircraft with sufficient vertical performance or "
                        "reroute around the abrupt terrain change."
                    ),
                    confidence=RecommendationConfidence.EXACT,
                    parameter="maximum_climb_rate_mps",
                    current_value=maximum_climb_rate,
                    required_value=round(required_climb, 3),
                    unit="m/s",
                    affected_ids=[segment_id, drone.drone_id],
                )
            ],
        ) from error
    except ValueError as error:
        raise PlanningError(
            code=ErrorCode.TERRAIN_DATA_UNAVAILABLE,
            message=f"Could not build the vertical profile for segment {segment_id}.",
        ) from error
