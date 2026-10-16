from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geoplan.coverage.models import CoverageArea
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import DubinsPathType, OptimalityStatus
from geoplan.domain.geo import Coordinate


class DubinsPose(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinate: Coordinate
    heading_degrees: float = Field(ge=0, lt=360)


class DubinsPathRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: DubinsPose
    end: DubinsPose
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)

    @model_validator(mode="after")
    def poses_do_not_describe_zero_length_path(self) -> DubinsPathRequest:
        if self.start == self.end:
            raise ValueError("Начальная и конечная позы Dubins-маршрута должны различаться.")
        return self


class DubinsPathResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path_type: DubinsPathType
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)


class HybridAStarPathRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: DubinsPose
    end: DubinsPose
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    primitive_length_m: float = Field(default=20, ge=2, le=5_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)
    grid_resolution_m: float = Field(default=10, ge=1, le=1_000)
    heading_bin_degrees: float = Field(default=15, ge=1, le=90)
    analytic_expansion_distance_m: float = Field(default=200, gt=0, le=100_000)
    maximum_expansions: int = Field(default=50_000, ge=1, le=2_000_000)

    @model_validator(mode="after")
    def poses_do_not_describe_zero_length_path(self) -> HybridAStarPathRequest:
        if self.start == self.end:
            raise ValueError("Начальная и конечная позы Hybrid A* должны различаться.")
        return self


class HybridAStarPathResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)
    expanded_states: int = Field(ge=0)
    optimality: OptimalityStatus = OptimalityStatus.HEURISTIC


class SweepRouteCurvatureRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    coverage_area: CoverageArea
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)

    @model_validator(mode="after")
    def route_contains_complete_survey_legs(self) -> SweepRouteCurvatureRequest:
        if len(self.route) % 2 != 0:
            raise ValueError("Маршрут галсов должен содержать чётное число точек.")
        return self


class SweepRouteCurvatureResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(min_length=1, max_length=5_000)
    transition_path_types: list[DubinsPathType] = Field(default_factory=list, max_length=5_000)
    distance_km: float = Field(gt=0)
    transition_distance_km: float = Field(ge=0)


class TurnAwareSweepRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=10_000)
    coverage_area: CoverageArea
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)
    minimum_turn_radius_m: float = Field(gt=0, le=10_000)
    sampling_step_m: float = Field(default=5, ge=1, le=1_000)
    strip_width_m: float = Field(gt=0, le=5_000)
    footprint_height_m: float = Field(gt=0, le=5_000)

    @model_validator(mode="after")
    def route_contains_complete_survey_legs(self) -> TurnAwareSweepRequest:
        if len(self.route) % 2 != 0:
            raise ValueError("Маршрут галсов должен содержать чётное число точек.")
        return self


class TurnAwareSweepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(min_length=1, max_length=5_000)
    distance_km: float = Field(gt=0)
    transition_distance_km: float = Field(ge=0)
    endpoint_retreat_m: float = Field(ge=0)
    uncovered_area_m2: float = Field(ge=0)


class RouteOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    alternative_routes: list[list[Coordinate]] = Field(default_factory=list, max_length=8)
    departure: Coordinate
    landings: list[Coordinate] = Field(default_factory=list, max_length=100)
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)


class RouteOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: list[Coordinate] = Field(min_length=2)
    reversed_for_shorter_transit: bool
    selected_route_index: int = Field(default=0, ge=0)


class SectionRouteVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str = Field(min_length=1, max_length=64)
    variant_id: str = Field(min_length=1, max_length=64)
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    survey_legs: list[list[Coordinate]] = Field(default_factory=list, max_length=5_000)


class SectionRouteOptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant_groups: list[list[SectionRouteVariant]] = Field(min_length=1, max_length=12)
    coverage_area: CoverageArea
    departure_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    landing_candidates: list[Coordinate] = Field(default_factory=list, max_length=100)
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def groups_contain_unique_sections_and_variants(self) -> SectionRouteOptimizationRequest:
        if any(not group for group in self.variant_groups):
            raise ValueError("Каждая секция должна содержать хотя бы один вариант маршрута.")
        section_ids = [group[0].section_id for group in self.variant_groups]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Идентификаторы секций должны быть уникальными.")
        for group in self.variant_groups:
            section_id = group[0].section_id
            if any(variant.section_id != section_id for variant in group):
                raise ValueError("Группа содержит варианты разных секций.")
            variant_ids = [variant.variant_id for variant in group]
            if len(variant_ids) != len(set(variant_ids)):
                raise ValueError("Идентификаторы вариантов внутри секции должны быть уникальными.")
        return self


class SectionRouteSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str = Field(min_length=1, max_length=64)
    variant_id: str = Field(min_length=1, max_length=64)


class SectionRouteOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    selections: list[SectionRouteSelection] = Field(min_length=1, max_length=12)
    route: list[Coordinate] = Field(min_length=2, max_length=100_000)
    distance_km: float = Field(gt=0)
    turn_count: int = Field(ge=0)
    departure: Coordinate | None = None
    landing: Coordinate | None = None
    optimality: OptimalityStatus
