"""Physical footprints and timing of explicit point-trigger photographs."""

from dataclasses import dataclass
from itertools import pairwise
from math import hypot

from shapely.geometry import Polygon

from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import Mission
from geoplan.flight.trajectory import mission_route_clock
from geoplan.navigation.geometry import LocalProjection
from geoplan.navigation.routing import coordinate_distance_km
from geoplan.routing.freeform import image_footprint


@dataclass(frozen=True)
class ExplicitFrames:
    footprints: list[Polygon]
    intervals_s: list[float]
    forward_overlap_percent: list[float]
    straight_runs: list[tuple[tuple[float, float], tuple[float, float]]]
    partial: bool


@dataclass(frozen=True)
class ExplicitFrameInspector:
    def inspect(
        self, mission: Mission, projection: LocalProjection, width: float, height: float
    ) -> ExplicitFrames:
        points = [projection.project(p) for p in mission.route]
        scan = [
            stage.stage_type is MissionStageType.SURVEY
            for stage in mission.stages
            for a, b in pairwise(stage.coordinates)
            if a != b
        ]
        indices = mission.photo_capture_route_indices or []
        clock = mission_route_clock(mission).offsets_minutes
        footprints, forward, intervals, runs = [], [], [], []
        partial = len(scan) != len(points) - 1
        if partial:
            return ExplicitFrames([], [], [], [], True)
        headings = []
        for i in indices:
            edge = i - 1 if i > 0 and scan[i - 1] else min(i, len(points) - 2)
            start, end = points[edge], points[edge + 1]
            footprints.append(image_footprint(points[i], start, end, width, height))
            distance = hypot(end[0] - start[0], end[1] - start[1])
            headings.append(((end[0] - start[0]) / distance, (end[1] - start[1]) / distance))
        for position, (a, b) in enumerate(pairwise(indices)):
            intervals.append((clock[b] - clock[a]) * 60)
            ux, uy = headings[position]
            vx, vy = headings[position + 1]
            if ux * vx + uy * vy > 0.99985 and all(scan[a:b]):
                # Camera spacing and stage clocks use physical ground distance;
                # local projection distortion must not manufacture an overlap violation.
                step = coordinate_distance_km(mission.route[a], mission.route[b]) * 1000
                forward.append(max(0, 1 - step / height) * 100)
            else:
                partial = True
        # Merge collinear subdivided edges; capture waypoints must not look like
        # thousands of independent strips in the lateral-neighbor analysis.
        current = None
        for (a, b), survey in zip(pairwise(points), scan, strict=True):
            if not survey:
                if current is not None:
                    runs.append(current)
                current = None
                continue
            if current is not None:
                x, y = current
                u = (y[0] - x[0], y[1] - x[1])
                v = (b[0] - a[0], b[1] - a[1])
                cross = abs(u[0] * v[1] - u[1] * v[0])
                if (
                    a == y
                    and cross <= max(1e-6, hypot(*u) * hypot(*v) * 1e-6)
                    and u[0] * v[0] + u[1] * v[1] > 0
                ):
                    current = (x, b)
                    continue
                runs.append(current)
            current = (a, b)
        if current is not None:
            runs.append(current)
        return ExplicitFrames(footprints, intervals, forward, runs, partial)
