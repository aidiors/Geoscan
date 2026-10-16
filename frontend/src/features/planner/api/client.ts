import type { PlanRequest, PlanResponse, TurnRadiusAnalysisRequest, TurnRadiusAnalysisResult } from "@/shared/api/types";
import { requestJson } from "@/shared/api/http";

export function createPlan(request: PlanRequest): Promise<PlanResponse> {
  return requestJson<PlanResponse>("/v1/plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
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
