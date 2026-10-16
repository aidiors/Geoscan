from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from geoplan.domain.diagnostics import FeasibilityRecommendation
from geoplan.domain.enums import ErrorCode, HealthStatus


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str = Field(min_length=1)
    recommendations: list[FeasibilityRecommendation] = Field(default_factory=list, max_length=32)


class HealthReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: HealthStatus
    message: str = Field(min_length=1)
