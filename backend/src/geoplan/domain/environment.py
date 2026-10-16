from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WindCondition(BaseModel):
    model_config = ConfigDict(frozen=True)

    speed_mps: float = Field(ge=0, le=100)
    direction_degrees: float = Field(ge=0, lt=360)
