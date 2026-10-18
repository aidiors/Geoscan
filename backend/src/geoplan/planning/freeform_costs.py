"""The endpoint-protection policy is separate from search and photography."""

from dataclasses import dataclass

from geoplan.domain.enums import MissionStageType
from geoplan.domain.mission import Mission
from geoplan.planning.models import PlanResponse


@dataclass(frozen=True)
class EndpointCostProtection:
    tolerance_minutes: float = 1e-7

    def allows(self, missions: list[Mission], baseline: PlanResponse) -> bool:
        original = [mission for drone in baseline.drone_plans for mission in drone.missions]
        return all(
            actual <= reference + self.tolerance_minutes
            for actual, reference in zip(self._costs(missions), self._costs(original), strict=True)
        )

    def allows_plan(self, candidate: PlanResponse, baseline: PlanResponse) -> bool:
        return self.allows([m for drone in candidate.drone_plans for m in drone.missions], baseline)

    @staticmethod
    def _costs(missions: list[Mission]) -> tuple[float, float, float, float]:
        total = dead = departure = returning = 0.0
        for mission in missions:
            total += mission.duration_minutes
            dead += mission.transit_duration_minutes or 0
            scan = [
                i
                for i, stage in enumerate(mission.stages)
                if stage.stage_type is MissionStageType.SURVEY
            ]
            if not scan:
                return float("inf"), float("inf"), float("inf"), float("inf")
            departure += sum(stage.duration_minutes for stage in mission.stages[: scan[0]])
            returning += sum(stage.duration_minutes for stage in mission.stages[scan[-1] + 1 :])
        return total, dead, departure, returning
