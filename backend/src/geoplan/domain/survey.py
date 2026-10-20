"""Physical camera specification, independent of coverage algorithms."""

from pydantic import BaseModel, ConfigDict, Field


class SensorProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensor_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    sensor_width_mm: float = Field(gt=0, le=200)
    sensor_height_mm: float = Field(gt=0, le=200)
    focal_length_mm: float = Field(gt=0, le=1_000)
    image_width_px: int = Field(gt=0, le=200_000)
    image_height_px: int = Field(gt=0, le=200_000)
    minimum_trigger_interval_s: float = Field(gt=0, le=120)
