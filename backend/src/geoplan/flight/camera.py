from dataclasses import dataclass
from itertools import pairwise
from math import floor

from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import Mission
from geoplan.flight.trajectory import mission_route_clock
from geoplan.navigation.routing import coordinate_distance_km


@dataclass(frozen=True)
class DistanceCameraPolicy:
    def spacing_m(self, mission: Mission) -> float | None:
        if mission.photo_capture_route_indices is not None:
            return None
        if mission.camera_trigger_spacing_m is not None:
            return mission.camera_trigger_spacing_m
        if (
            mission.survey_distance_km is None
            or mission.image_count is None
            or mission.image_count <= 1
        ):
            return None
        return mission.survey_distance_km * 1000 / (mission.image_count - 1)

    def capture_times_minutes(self, mission: Mission) -> tuple[float, ...]:
        """Replay the commanded photographs, including resets between strips."""
        if mission.photo_capture_route_indices is not None:
            clock = mission_route_clock(mission).offsets_minutes
            return tuple(
                mission.vertical_climb_duration_minutes + clock[i]
                for i in mission.photo_capture_route_indices
            )
        spacing = self.spacing_m(mission)
        if spacing is None:
            return ()
        captures = []
        elapsed = 0.0
        for stage in mission.stages:
            if stage.stage_type is MissionStageType.SURVEY:
                lengths = [
                    coordinate_distance_km(a, b) * 1000 for a, b in pairwise(stage.coordinates)
                ]
                total = sum(lengths)
                if total <= 0:
                    elapsed += stage.duration_minutes
                    continue
                times = stage.leg_durations_minutes
                if times is None:
                    times = [stage.duration_minutes * d / total for d in lengths] if total else []
                next_capture, distance, clock = 0, 0.0, elapsed
                for length, duration in zip(lengths, times, strict=True):
                    if length <= 0:
                        clock += duration
                        continue
                    last = floor((distance + length + 1e-7) / spacing)
                    if len(captures) + max(0, last - next_capture + 1) > 100_000:
                        raise ValueError("Camera plan exceeds 100000 captures")
                    for index in range(next_capture, last + 1):
                        offset = min(length, max(0.0, index * spacing - distance))
                        captures.append(clock + duration * offset / length)
                    next_capture = last + 1
                    distance += length
                    clock += duration
            elapsed += stage.duration_minutes
        return tuple(captures)

    def minimum_interval_s(self, mission: Mission) -> float | None:
        return min(
            ((b - a) * 60 for a, b in pairwise(self.capture_times_minutes(mission))),
            default=None,
        )
