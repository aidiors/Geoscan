import type { PlanRequest, PlanResponse, TurnRadiusAnalysisRequest, TurnRadiusAnalysisResult } from "@/shared/api/types";
import { requestJson } from "@/shared/api/http";
import { ApiError } from "@/shared/api/http";
import { requestProgressivePlan } from "./progressive";
import type { AdvisorResponse, SurveyQualityReport } from "@/shared/api/types";

export function getFeasibilityAdvice(request: PlanRequest, allowAltitudeChange = false): Promise<AdvisorResponse> {
  return requestJson<AdvisorResponse>("/v1/feasibility-advice", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, maximum_variants: 3, allow_altitude_change: allowAltitudeChange }),
  });
}

export function inspectSurveyQuality(request: PlanRequest, plan: PlanResponse): Promise<SurveyQualityReport> {
  return requestJson<SurveyQualityReport>("/v1/survey-quality", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, plan }),
  });
}

export async function createPlan(request: PlanRequest,
  onPlan?: (plan: PlanResponse) => void, signal?: AbortSignal): Promise<PlanResponse> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = setTimeout(() => controller.abort(), 300_000);
  try {
    if (onPlan) return await requestProgressivePlan(request, onPlan, controller.signal);
    return await requestJson<PlanResponse>("/v1/plans", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request), signal: controller.signal,
    });
  } catch (error) {
    if (signal?.aborted) throw new ApiError("Поиск остановлен пользователем.", "planning_cancelled");
    if (controller.signal.aborted) throw new ApiError("Лимит ожидания истёк.", "planning_time_limit");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export function analyzeTurnRadii(
  request: TurnRadiusAnalysisRequest,
): Promise<TurnRadiusAnalysisResult> {
  return requestJson<TurnRadiusAnalysisResult>("/v1/turn-radius-analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
