"""Separate mandatory survey edges from connections without inventing coverage."""

from collections import Counter
from itertools import pairwise

from geoplan.coverage.models import WorkSegment
from geoplan.domain.enums import ErrorCode, MissionStageType
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission, MissionStage
from geoplan.errors import PlanningError
from geoplan.flight.emergency import FlightPhase
from geoplan.navigation.routing import route_distance_km


def survey_edge_mask(segment: WorkSegment, route: list[Coordinate]) -> list[bool]:
    if segment.survey_tracks is None:
        return [True] * (len(route) - 1)
    required = Counter(frozenset(track) for track in segment.survey_tracks)
    mask = [frozenset(edge) in required for edge in pairwise(route)]
    actual = Counter(
        frozenset(edge) for edge, scan in zip(pairwise(route), mask, strict=True) if scan
    )
    if actual != required or any(len(track) != 2 for track in required):
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message=f"Маршрут {segment.segment_id} не сохраняет исходные съёмочные галсы.",
        )
    return mask


def survey_flight_phases(
    segment: WorkSegment, route: list[Coordinate], distance_km: float
) -> list[FlightPhase]:
    if segment.survey_tracks is None:
        return [FlightPhase(route, distance_km, segment.survey_speed_limit_kmh)]
    return [
        FlightPhase(
            [a, b], route_distance_km([a, b]), segment.survey_speed_limit_kmh if scan else None
        )
        for (a, b), scan in zip(pairwise(route), survey_edge_mask(segment, route), strict=True)
        if a != b
    ]


def survey_stages(
    segment: WorkSegment, route: list[Coordinate], durations: list[float] | None, total: float
) -> list[MissionStage]:
    if segment.survey_tracks is None:
        return [
            MissionStage(
                stage_type=MissionStageType.SURVEY,
                coordinates=route,
                duration_minutes=total,
                leg_durations_minutes=durations,
            )
        ]
    if durations is None:
        raise PlanningError(
            code=ErrorCode.COVERAGE_GENERATION_FAILED,
            message="Для раздельных галсов нужны точные времена плеч.",
        )
    return [
        MissionStage(
            stage_type=MissionStageType.SURVEY if scan else MissionStageType.TRANSIT,
            coordinates=[a, b],
            duration_minutes=duration,
            leg_durations_minutes=[duration],
        )
        for (a, b), scan, duration in zip(
            pairwise(route), survey_edge_mask(segment, route), durations, strict=True
        )
        if a != b
    ]


def normalize_reference_missions(
    segments: list[WorkSegment], missions: list[Mission]
) -> list[Mission]:
    """Classify reference connectors using its original clock, keeping flight times unchanged."""
    sources = {s.segment_id: s for s in segments}
    normalized = []
    for mission in missions:
        segment = sources[mission.segment_id]
        if (
            segment.survey_tracks is not None
            or segment.coverage_area_id is None
            or len(segment.route) % 2
        ):
            normalized.append(mission)
            continue
        source = segment.model_copy(
            update={
                "survey_tracks": [
                    (segment.route[i], segment.route[i + 1])
                    for i in range(0, len(segment.route), 2)
                ]
            }
        )
        if any(
            stage.stage_type is MissionStageType.SURVEY and stage.leg_durations_minutes is None
            for stage in mission.stages
        ):
            normalized.append(mission)
            continue
        stages = [
            part
            for stage in mission.stages
            for part in (
                survey_stages(
                    source, stage.coordinates, stage.leg_durations_minutes, stage.duration_minutes
                )
                if stage.stage_type is MissionStageType.SURVEY
                else [stage]
            )
        ]
        distance = sum(
            route_distance_km(s.coordinates)
            for s in stages
            if s.stage_type is MissionStageType.SURVEY
        )
        elapsed = sum(s.duration_minutes for s in stages if s.stage_type is MissionStageType.SURVEY)
        data = mission.model_dump()
        data.update(
            stages=[s.model_dump() for s in stages],
            survey_distance_km=round(distance, 3),
            transit_distance_km=round(max(0, mission.distance_km - distance), 3),
            survey_duration_minutes=elapsed,
            transit_duration_minutes=mission.duration_minutes - elapsed,
            useful_flight_percent=round(min(100, distance / mission.distance_km * 100), 2),
        )
        normalized.append(Mission.model_validate(data))
    return normalized
