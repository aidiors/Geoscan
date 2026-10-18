import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useMissionInsights } from "@/features/planner/model/useMissionInsights";
import { inspectSurveyQuality, getFeasibilityAdvice } from "@/features/planner/api/client";
import type { AdvisorResponse, PlanRequest, PlanResponse, SurveyQualityReport } from "@/shared/api/types";

vi.mock("@/features/planner/api/client", () => ({ inspectSurveyQuality: vi.fn(), getFeasibilityAdvice: vi.fn() }));

describe("mission insights snapshots", () => {
  it("hides an obsolete quality response after the request changes", async () => {
    const request = {} as PlanRequest;
    const plan = {} as PlanResponse;
    let finish!: (report: SurveyQualityReport) => void;
    vi.mocked(inspectSurveyQuality).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const { result, rerender } = renderHook(({ source }) => useMissionInsights(source, plan, vi.fn()), { initialProps: { source: request as PlanRequest | null } });
    let pending!: Promise<void>;
    act(() => { pending = result.current.inspect(); });
    expect(result.current.busy).toBe(true);
    rerender({ source: null });
    await act(async () => { finish({ status: "checked" } as SurveyQualityReport); await pending; });
    expect(result.current.quality).toBeNull();
    expect(result.current.busy).toBe(false);
  });

  it("retains advice for the exact request and hides it for changed conditions", async () => {
    const request = {} as PlanRequest;
    const report = { baseline: null, baseline_error: "No route", variants: [], message: "Bounded search" } satisfies AdvisorResponse;
    vi.mocked(getFeasibilityAdvice).mockResolvedValueOnce(report);
    const { result, rerender } = renderHook(({ source }) => useMissionInsights(source, null, vi.fn()), { initialProps: { source: request } });
    await act(async () => { await result.current.advise(request); });
    await waitFor(() => expect(result.current.advice).toEqual(report));
    rerender({ source: {} as PlanRequest });
    expect(result.current.advice).toBeNull();
  });
});
