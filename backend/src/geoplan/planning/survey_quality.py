"""Independent footprint diagnostics using the exported distance-trigger semantics.

This is a flat, nadir pinhole-camera check. Terrain profiles provide conservative
height bounds, not a ray/DEM intersection or a photogrammetric reconstruction.
"""

from dataclasses import dataclass
from itertools import pairwise
from math import ceil, hypot

from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geoplan.domain.enums import MissionStageType
from geoplan.domain.geo import Coordinate
from geoplan.domain.survey import SensorProfile
from geoplan.flight.camera import DistanceCameraPolicy
from geoplan.flight.photography import ExplicitFrameInspector
from geoplan.navigation.geometry import (
    LocalProjection,
    polygon_from_coordinates,
    polygon_parts,
    projection_for_coordinates,
)
from geoplan.navigation.routing import route_distance_km
from geoplan.planning.lateral_overlap import SurveyStrip, lateral_overlaps
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.quality_models import SurveyQualityReport


@dataclass(frozen=True)
class SurveyQualityAnalyzer:
    maximum_frames: int = 100_000
    maximum_displayed_frames: int = 400

    def analyze(self, request: PlanRequest, plan: PlanResponse) -> SurveyQualityReport:
        assumptions = [
            "Надирная камера без крена и тангажа; прямоугольный отпечаток, "
            "локальная плоская проекция.",
            "Проверяются плановые команды камеры; фактические снимки не загружены.",
            "Применены команды экспорта: первый кадр при включении, затем по дистанции.",
            "Оси камеры считаются ориентированными по путевому углу; "
            "нескомпенсированный снос, рельеф внутри кадра и дисторсия не моделируются.",
            "Боковое перекрытие: соседние параллельные проходы, имеющие общую продольную часть; "
            "кратность покрытия не заменяет процент перекрытия.",
            "Стыки вылетов проверяются объединением отпечатков и парами проходов; "
            "произвольные непараллельные стыки требуют отдельной фотограмметрической проверки.",
        ]
        survey = request.survey_parameters
        if survey is None or not request.coverage_areas:
            return SurveyQualityReport(status="not_evaluated", assumptions=assumptions)
        projection = projection_for_coordinates(
            [p for area in request.coverage_areas for p in area.coordinates]
        )
        targets = unary_union(
            [
                polygon_from_coordinates(area.coordinates, area.interior_rings, projection)
                for area in request.coverage_areas
            ]
        )
        # The same spatial exclusions as the coverage audit. Temporal/vertical
        # interpretation remains the flight auditor's responsibility.
        if request.no_fly_zones:
            targets = targets.difference(
                unary_union(
                    [
                        polygon_from_coordinates(
                            z.coordinates, z.interior_rings, projection
                        ).buffer(z.safety_margin_m + 0.5, join_style="mitre")
                        for z in request.no_fly_zones
                    ]
                )
            )
        cameras = {d.drone_id: d.installed_sensor or survey.sensor for d in request.drones}
        regions = [part.buffer(0.01) for part in polygon_parts(targets)]

        def region(a, b):
            midpoint = LineString([a, b]).interpolate(0.5, normalized=True)
            return next((i for i, part in enumerate(regions) if part.covers(midpoint)), None)

        footprints: list[Polygon] = []
        frame_gsd: list[float] = []
        strips: list[SurveyStrip] = []
        forward: list[float] = []
        intervals: list[float] = []
        issues: list[str] = []
        forward_problem_frames: list[Polygon] = []
        incomplete = request.wind is not None and request.wind.speed_mps > 0
        for drone in plan.drone_plans:
            for mission in drone.missions:
                sensor = cameras.get(mission.drone_id)
                if sensor is None:
                    incomplete = True
                    continue
                spacing = DistanceCameraPolicy().spacing_m(mission)
                if mission.photo_capture_route_indices is None and (
                    spacing is None or spacing <= 0
                ):
                    incomplete = True
                    continue
                height_min = min(
                    (p.target_agl_m for p in mission.vertical_profile), default=mission.altitude_m
                )
                height_max = max(
                    (p.target_agl_m for p in mission.vertical_profile), default=mission.altitude_m
                )
                if mission.vertical_profile:
                    incomplete = True
                width = height_min * sensor.sensor_width_mm / sensor.focal_length_mm
                length = height_min * sensor.sensor_height_mm / sensor.focal_length_mm
                gsd = self._gsd(height_max, sensor)
                if mission.photo_capture_route_indices is not None:
                    if (
                        len(footprints) + len(mission.photo_capture_route_indices)
                        > self.maximum_frames
                    ):
                        return SurveyQualityReport(
                            status="not_evaluated",
                            assumptions=assumptions,
                            issues=["Превышен лимит детальной проверки кадров."],
                        )
                    actual = ExplicitFrameInspector().inspect(mission, projection, width, length)
                    footprints.extend(actual.footprints)
                    frame_gsd.extend([gsd] * len(actual.footprints))
                    intervals.extend(actual.intervals_s)
                    forward.extend(actual.forward_overlap_percent)
                    strips.extend(
                        SurveyStrip(
                            (mission.drone_id, mission.segment_id), a, b, width, region(a, b)
                        )
                        for a, b in actual.straight_runs
                    )
                    incomplete |= actual.partial
                    if any(
                        t < sensor.minimum_trigger_interval_s - 1e-4 for t in actual.intervals_s
                    ):
                        issues.append(f"{mission.drone_id}: нарушен интервал явных команд камеры.")
                    continue
                assert spacing is not None
                elapsed = (mission.start_offset_minutes or 0) * 60
                last_capture = None
                for stage in mission.stages:
                    if stage.stage_type is not MissionStageType.SURVEY:
                        elapsed += stage.duration_minutes * 60
                        continue
                    coords = [projection.project(c) for c in stage.coordinates]
                    line = LineString(coords)
                    if line.length <= 1e-6:
                        elapsed += stage.duration_minutes * 60
                        continue
                    physical_distance = route_distance_km(stage.coordinates) * 1000
                    projected_spacing = spacing * line.length / physical_distance
                    count = int((physical_distance + 1e-7) // spacing) + 1
                    if len(footprints) + count > self.maximum_frames:
                        return SurveyQualityReport(
                            status="not_evaluated",
                            frame_count=len(footprints),
                            issues=["Превышен лимит детальной проверки кадров; отчёт не завершён."],
                            assumptions=assumptions,
                        )
                    direct = hypot(coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1])
                    straight = abs(line.length - direct) <= max(0.01, line.length * 1e-6)
                    if straight:
                        strips.append(
                            SurveyStrip(
                                (mission.drone_id, mission.segment_id),
                                coords[0],
                                coords[-1],
                                width,
                                region(coords[0], coords[-1]),
                            )
                        )
                    else:
                        incomplete = True
                    if stage.leg_durations_minutes is None:
                        incomplete = True
                    first_frame = len(footprints)
                    for index in range(count):
                        offset = min(index * projected_spacing, line.length)
                        point = line.interpolate(offset)
                        before = line.interpolate(max(0, offset - 0.1))
                        after = line.interpolate(min(line.length, offset + 0.1))
                        dx, dy = after.x - before.x, after.y - before.y
                        norm = hypot(dx, dy)
                        if norm <= 1e-9:
                            incomplete = True
                            continue
                        footprints.append(
                            self._rectangle(point.x, point.y, dx / norm, dy / norm, width, length)
                        )
                        frame_gsd.append(gsd)
                        time = elapsed + self._time_at_offset(
                            coords, stage.leg_durations_minutes, offset, stage.duration_minutes
                        )
                        if last_capture is not None:
                            interval = time - last_capture
                            intervals.append(interval)
                            if interval < sensor.minimum_trigger_interval_s - 1e-4:
                                issues.append(
                                    f"{mission.drone_id}/{mission.segment_id}: "
                                    "интервал кадров меньше предела камеры."
                                )
                        last_capture = time
                    if count > 1 and straight:
                        overlap = max(0, 1 - spacing / length) * 100
                        forward.append(overlap)
                        if overlap < survey.forward_overlap_percent - 1e-4:
                            forward_problem_frames.extend(footprints[first_frame:])
                    elapsed += stage.duration_minutes * 60
        if not footprints or targets.area <= 0:
            return SurveyQualityReport(
                status="not_evaluated",
                assumptions=assumptions,
                issues=["Недостаточно геометрии или команд камеры."],
            )
        covered = unary_union(footprints)
        gaps = targets.difference(covered)
        percent = max(0, min(100, 100 * (1 - gaps.area / targets.area)))
        side, cross, side_problems = lateral_overlaps(
            strips, survey.side_overlap_percent, survey_target=targets.buffer(0.02)
        )
        incomplete |= any(strip.region_id is None for strip in strips)
        if percent < 99:
            issues.append("Отпечатки кадров покрывают менее 99% доступной целевой области.")
        if max(frame_gsd) > survey.maximum_gsd_cm + 1e-7:
            issues.append("Расчётный GSD превышает требование.")
        if forward and min(forward) < survey.forward_overlap_percent - 1e-4:
            issues.append("Продольное перекрытие ниже требования.")
        if side and min(side) < survey.side_overlap_percent - 0.1:
            issues.append("Боковое перекрытие соседних проходов ниже требования.")
        if not side or not forward:
            incomplete = True
        features = []
        stride = max(1, ceil(len(footprints) / self.maximum_displayed_frames))
        for index in range(0, len(footprints), stride):
            features.append(
                self._feature(
                    footprints[index],
                    projection,
                    "gsd_violation" if frame_gsd[index] > survey.maximum_gsd_cm else "footprint",
                )
            )
        if not gaps.is_empty:
            features.append(
                self._feature(
                    gaps.simplify(0.25, preserve_topology=True), projection, "coverage_gap"
                )
            )
        if forward_problem_frames:
            features.append(
                self._feature(
                    unary_union(forward_problem_frames), projection, "forward_overlap_violation"
                )
            )
        for geometry in side_problems[:100]:
            relevant = geometry.intersection(targets)
            if relevant.area > 1e-4 and relevant.geom_type in ("Polygon", "MultiPolygon"):
                features.append(self._feature(relevant, projection, "side_overlap_violation"))
        assumptions.append(
            f"Карта показывает каждый {stride}-й отпечаток; "
            f"покрытие рассчитано по всем {len(footprints)} кадрам."
        )
        if incomplete:
            assumptions.append(
                "Проверка частичная: профиль рельефа даёт лишь границы высоты, "
                "либо часть стыков/команд не поддержана этой моделью."
            )
        return SurveyQualityReport(
            status="issues" if issues else "partial" if incomplete else "checked",
            frame_count=len(footprints),
            covered_percent=percent,
            maximum_gsd_cm=max(frame_gsd),
            minimum_forward_overlap_percent=min(forward, default=None),
            minimum_side_overlap_percent=min(side, default=None),
            minimum_capture_interval_s=min(intervals, default=None),
            cross_sortie_pairs_checked=cross,
            issues=list(dict.fromkeys(issues)),
            assumptions=assumptions,
            features=features,
        )

    @staticmethod
    def _gsd(height: float, camera: SensorProfile) -> float:
        return (
            height
            / camera.focal_length_mm
            * max(
                camera.sensor_width_mm / camera.image_width_px,
                camera.sensor_height_mm / camera.image_height_px,
            )
            * 100
        )

    @staticmethod
    def _rectangle(
        x: float, y: float, dx: float, dy: float, width: float, length: float
    ) -> Polygon:
        return Polygon(
            [
                (x + along * dx - across * dy, y + along * dy + across * dx)
                for along, across in (
                    (-length / 2, -width / 2),
                    (length / 2, -width / 2),
                    (length / 2, width / 2),
                    (-length / 2, width / 2),
                )
            ]
        )

    @staticmethod
    def _time_at_offset(coords, times, offset: float, duration: float) -> float:
        remaining, elapsed = offset, 0.0
        lengths = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(coords)]
        if times is None:
            return duration * 60 * offset / sum(lengths)
        for length, minutes in zip(lengths, times, strict=True):
            if length > 0 and remaining <= length:
                return elapsed + minutes * 60 * remaining / length
            remaining -= length
            elapsed += minutes * 60
        return elapsed

    @staticmethod
    def _feature(geometry: BaseGeometry, projection: LocalProjection, kind: str) -> dict:
        def coordinates(value):
            if value and isinstance(value[0], (int, float)):
                point: Coordinate = projection.unproject(value[0], value[1])
                return [point.longitude, point.latitude]
            return [coordinates(item) for item in value]

        shape = mapping(geometry)
        return {
            "type": "Feature",
            "properties": {"kind": kind},
            "geometry": {"type": shape["type"], "coordinates": coordinates(shape["coordinates"])},
        }
