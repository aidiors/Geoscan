"""Operator-supplied operating rates, distinct from aircraft performance limits."""

from pydantic import BaseModel, ConfigDict, Field


class VerticalTakeoffLandingProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    climb_rate_mps: float = Field(gt=0, le=100)
    descent_rate_mps: float = Field(gt=0, le=100)
    climb_energy_multiplier: float = Field(default=1, ge=1, le=10)
    descent_energy_multiplier: float = Field(default=1, ge=1, le=10)
