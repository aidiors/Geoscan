"""Bounded what-if search. Every successful recommendation carries a real audited plan."""

from dataclasses import dataclass
from math import ceil
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.fleet import FlightSite
from geoplan.errors import PlanningError
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.plan_quality import plan_is_audited


class PlanBuilder(Protocol):
    def __call__(self, request: PlanRequest) -> PlanResponse: ...


class AdvisorRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request: PlanRequest
    maximum_variants: int = Field(default=3, ge=1, le=5)
    allow_altitude_change: bool = False
    candidate_departure_sites: list[FlightSite] = Field(default_factory=list, max_length=3)


class AdvisorVariant(BaseModel):
    model_config = ConfigDict(frozen=True)
    label: str
    status: Literal["verified", "not_found", "error"]
    explanation: str
    changed_parameters: list[str]
    request: PlanRequest
    plan: PlanResponse | None = None


class AdvisorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    baseline: PlanResponse | None
    baseline_error: str | None = None
    variants: list[AdvisorVariant]
    message: str = (
        "Показаны проверенные варианты в пределах бюджета поиска. "
        "Отсутствие варианта не доказывает невыполнимость; минимальный парк не доказан. "
        "Увеличение срока не продлевает рабочее окно и действие разрешений."
    )


@dataclass(frozen=True)
class FeasibilityAdvisor:
    planner: PlanBuilder

    def advise(self, query: AdvisorRequest) -> AdvisorResponse:
        request = query.request
        baseline, error = self._run(request)
        candidates: list[tuple[str, list[str], PlanRequest]] = []
        # Diagnose deadline pressure with the complete available fleet first.
        if request.maximum_active_drones is not None and request.maximum_active_drones < len(
            request.drones
        ):
            limit = request.maximum_active_drones
            for count in sorted({min(limit + 1, len(request.drones)), len(request.drones)}):
                candidates.append(
                    (
                        f"Разрешить до {count} БВС",
                        ["maximum_active_drones"],
                        request.model_copy(update={"maximum_active_drones": count}),
                    )
                )
        deadline = request.completion_deadline_minutes
        if baseline is not None and deadline is not None:
            actual = baseline.metrics.completion_minutes
            if actual > deadline + 1e-6:
                extended = ceil(actual * 1.02)
                candidates.append(
                    (
                        f"Срок {extended} мин",
                        ["completion_deadline_minutes"],
                        request.model_copy(update={"completion_deadline_minutes": extended}),
                    )
                )
            else:
                # A real deadline-constrained resource-saving alternative.
                candidates.append(
                    (
                        "Экономить налёт в текущий срок",
                        ["criterion"],
                        request.model_copy(update={"criterion": OptimizationCriterion.BALANCED}),
                    )
                )
        for site in query.candidate_departure_sites:
            candidates.append(
                (
                    f"Проверить площадку «{site.name}»",
                    ["departure_sites"],
                    request.model_copy(update={"departure_site": None, "departure_sites": [site]}),
                )
            )
        if query.allow_altitude_change and request.survey_parameters is not None:
            sensor = request.survey_parameters.sensor
            cameras = [d.installed_sensor or sensor for d in request.drones]
            max_height = min(
                request.survey_parameters.maximum_gsd_cm
                / 100
                * c.focal_length_mm
                / max(c.sensor_width_mm / c.image_width_px, c.sensor_height_mm / c.image_height_px)
                for c in cameras
            )
            if max_height < request.mission_altitude_m:
                candidates.insert(
                    0,
                    (
                        f"Высота {max_height * 0.99:.1f} м",
                        ["mission_altitude_m"],
                        request.model_copy(update={"mission_altitude_m": max_height * 0.99}),
                    ),
                )
        variants = []
        seen = {request.model_dump_json()}
        for label, changed, candidate in candidates:
            fingerprint = candidate.model_dump_json()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            # Validate copied requests: model_copy deliberately skips validation.
            candidate = PlanRequest.model_validate(candidate.model_dump())
            plan, failure = self._run(candidate)
            verified = (
                plan is not None
                and plan_is_audited(plan)
                and (
                    candidate.completion_deadline_minutes is None
                    or plan.metrics.completion_minutes
                    <= candidate.completion_deadline_minutes + 1e-6
                )
            )
            variants.append(
                AdvisorVariant(
                    label=label,
                    status="verified" if verified else "error" if failure else "not_found",
                    explanation=(
                        "Вариант рассчитан заново, прошёл аудит и укладывается в заданный срок."
                        if verified
                        else failure or "Поиск подходящего плана не дал результата."
                    ),
                    changed_parameters=changed,
                    request=candidate,
                    plan=plan,
                )
            )
            if len(variants) >= query.maximum_variants:
                break
        return AdvisorResponse(baseline=baseline, baseline_error=error, variants=variants)

    def _run(self, request: PlanRequest) -> tuple[PlanResponse | None, str | None]:
        try:
            return self.planner(request), None
        except PlanningError as error:
            return None, error.message
