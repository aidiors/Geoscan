import { useState } from "react";
import { getFeasibilityAdvice, inspectSurveyQuality } from "@/features/planner/api/client";
import type { AdvisorResponse, PlanRequest, PlanResponse, SurveyQualityReport } from "@/shared/api/types";
import { errorMessage } from "@/shared/api/errorMessage";

export function useMissionInsights(request: PlanRequest | null, plan: PlanResponse | null, onStatus: (message: string) => void) {
  const [qualityResult, setQuality] = useState<{ request: PlanRequest; plan: PlanResponse; report: SurveyQualityReport } | null>(null);
  const [adviceResult, setAdvice] = useState<{ request: PlanRequest; report: AdvisorResponse } | null>(null);
  const [pending, setPending] = useState<PlanRequest | null>(null);

  async function inspect() {
    if (!request || !plan || pending === request) return;
    setPending(request);
    try {
      const report = await inspectSurveyQuality(request, plan);
      setQuality({ request, plan, report });
    } catch (error) { onStatus(errorMessage(error)); }
    finally { setPending((current) => current === request ? null : current); }
  }

  async function advise(source: PlanRequest, allowAltitudeChange = false) {
    if (pending === source) return;
    setPending(source);
    try { setAdvice({ request: source, report: await getFeasibilityAdvice(source, allowAltitudeChange) }); }
    catch (error) { onStatus(errorMessage(error)); }
    finally { setPending((current) => current === source ? null : current); }
  }

  return {
    quality: qualityResult?.request === request && qualityResult.plan === plan ? qualityResult.report : null,
    advice: adviceResult?.request === request ? adviceResult.report : null,
    busy: request !== null && pending === request,
    inspect, advise,
  };
}
