from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from geoplan.domain.enums import ZoneType
from geoplan.geodata.models import GeoJsonMultiPolygonGeometry, GeoJsonPolygonGeometry
from geoplan.planning.models import PlanRequest, PlanResponse, TurnRadiusAnalysisResult
from geoplan.planning.quality_models import SurveyQualityReport


class SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    zone_type: ZoneType
    content: str = Field(max_length=25_000_000)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_content(self) -> SourceFile:
        digest = sha256(self.content.encode()).hexdigest()
        if self.sha256 is not None and self.sha256 != digest:
            raise ValueError("Source file checksum mismatch.")
        object.__setattr__(self, "sha256", digest)
        return self


class ArchivedQualityFeature(BaseModel):
    type: Literal["Feature"]
    properties: dict[str, JsonValue]
    geometry: GeoJsonPolygonGeometry | GeoJsonMultiPolygonGeometry


class ProjectDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    editor_state: dict[str, JsonValue]
    source_files: list[SourceFile] = Field(default_factory=list, max_length=100)
    request: PlanRequest | None = None
    plan: PlanResponse | None = None
    survey_quality: SurveyQualityReport | None = None
    turn_radius_analysis: TurnRadiusAnalysisResult | None = None

    @model_validator(mode="after")
    def bind_result_to_request(self) -> ProjectDocument:
        if self.survey_quality is not None:
            if self.plan is None:
                raise ValueError("An archived quality report requires its plan.")
            for feature in self.survey_quality.features:
                ArchivedQualityFeature.model_validate(feature)
        if self.plan is not None:
            if self.request is None:
                raise ValueError("An archived plan requires its original request.")
            fingerprint = sha256(self.request.model_dump_json().encode()).hexdigest()
            if self.plan.search and self.plan.search.request_fingerprint != fingerprint:
                raise ValueError("Plan and input request fingerprints differ.")
        return self

    def archive(self) -> ProjectDocument:
        # Importing history never authorizes an execution export or reissues a signature.
        plan = self.plan.model_copy(update={"export_authorization": None}) if self.plan else None
        return self.model_copy(update={"plan": plan})


class ProjectSaveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    document: ProjectDocument
    parent_revision: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Project name is empty.")
        return value.strip()


class ProjectSummary(BaseModel):
    project_id: UUID
    name: str
    latest_revision: int
    updated_at: datetime


class ProjectRevision(BaseModel):
    project_id: UUID
    name: str
    revision: int
    created_at: datetime
    document_sha256: str
    document: ProjectDocument


class PortableProject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format: Literal["geoplan-project"] = "geoplan-project"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=128)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document: ProjectDocument
