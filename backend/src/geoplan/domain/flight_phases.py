"""Operator-supplied operating rates, distinct from aircraft performance limits."""

from pydantic import BaseModel, ConfigDict, Field


class FlightPhaseOperatingProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    climb_rate_mps: float = Field(gt=0, le=100)
    descent_rate_mps: float = Field(gt=0, le=100)
    climb_energy_multiplier: float = Field(default=1, ge=1, le=10)
    descent_energy_multiplier: float = Field(default=1, ge=1, le=10)


class VerticalTakeoffLandingProfile(FlightPhaseOperatingProfile):
    """Sequential vertical departure and arrival of a multirotor."""


class FixedWingTakeoffLandingProfile(FlightPhaseOperatingProfile):
    """Operating upper bounds for a climb/descent distributed over a transit."""


class ForwardFlightPhases(BaseModel):
    model_config = ConfigDict(frozen=True)

    climb_minutes: float = Field(gt=0)
    descent_minutes: float = Field(gt=0)
    maximum_climb_rate_mps: float = Field(gt=0)
    maximum_descent_rate_mps: float = Field(gt=0)
    energy_surcharge_minutes: float = Field(ge=0)
