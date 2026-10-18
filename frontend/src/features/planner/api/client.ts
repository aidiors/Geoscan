import type { PlanRequest, PlanResponse, TurnRadiusAnalysisRequest, TurnRadiusAnalysisResult } from "@/shared/api/types";
import { requestJson } from "@/shared/api/http";
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

export async function createPlan(request: PlanRequest): Promise<PlanResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 300_000);
  try {
    return await requestJson<PlanResponse>("/v1/plans", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request), signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
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
