from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from geoplan.planning.models import PlanRequest, PlanResponse


class SurveyQualityRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request: PlanRequest
    plan: PlanResponse


class SurveyQualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["checked", "issues", "partial", "not_evaluated"]
    frame_count: int = Field(default=0, ge=0)
    covered_percent: float | None = None
    maximum_gsd_cm: float | None = None
    minimum_forward_overlap_percent: float | None = None
    minimum_side_overlap_percent: float | None = None
    minimum_capture_interval_s: float | None = None
    cross_sortie_pairs_checked: int = 0
    issues: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    features: list[dict] = Field(default_factory=list)
