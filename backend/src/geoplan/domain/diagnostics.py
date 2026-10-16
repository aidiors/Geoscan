from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from geoplan.domain.enums import (
    FeasibilityStatus,
    RecommendationCode,
    RecommendationConfidence,
    SafetyAuditStatus,
    SafetyCheckCode,
)


class FeasibilityRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: RecommendationCode
    message: str = Field(min_length=1, max_length=512)
    confidence: RecommendationConfidence
    parameter: str | None = Field(default=None, min_length=1, max_length=64)
    current_value: float | None = None
    required_value: float | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    affected_ids: list[str] = Field(default_factory=list, max_length=10_000)


class FeasibilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: FeasibilityStatus
    message: str = Field(min_length=1, max_length=512)
    requested_deadline_minutes: float | None = Field(default=None, gt=0)
    maximum_active_drones: int | None = Field(default=None, gt=0)
    used_drone_count: int = Field(ge=0)
    estimated_required_drone_count: int | None = Field(default=None, gt=0)
    deadline_margin_minutes: float | None = None
    recommendations: list[FeasibilityRecommendation] = Field(default_factory=list, max_length=32)


class SafetyAuditCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: SafetyCheckCode
    status: SafetyAuditStatus
    measured_value: float | None = None
    required_value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    message: str = Field(min_length=1, max_length=512)
    affected_ids: list[str] = Field(default_factory=list, max_length=10_000)


class SafetyAuditReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: SafetyAuditStatus
    audited_mission_count: int = Field(ge=0)
    checks: list[SafetyAuditCheck] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
