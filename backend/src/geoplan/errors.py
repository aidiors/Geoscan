from __future__ import annotations

from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import ErrorCode


class PlanningError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        recommendations: list[FeasibilityRecommendation] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.recommendations = list(recommendations or [])
        super().__init__(message)
