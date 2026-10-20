import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MissionWorkflow } from "./MissionWorkflow";
import type { PlanResponse, SurveyQualityReport } from "@/shared/api/types";

// Only readiness fields are relevant to this interaction boundary.
const plan = { safety_audit: { status: "passed" }, feasibility: { status: "feasible" }, export_authorization: "signed" } as PlanResponse;
const props = { plan, quality: null, archived: false, calculating: false, canInspect: true, busy: false, onInspect: vi.fn(), onCalculate: vi.fn() };

describe("mission preparation workflow", () => {
  it("requests a separate quality inspection after a successful safety audit", () => {
    render(<MissionWorkflow {...props} />);
    const nav = screen.getByRole("navigation", { name: "Этапы подготовки задания" });
    expect(within(nav).getByRole("link", { name: "3 Качество" })).toHaveAttribute("aria-current", "step");
    fireEvent.click(within(nav).getByRole("button", { name: "Проверить качество" }));
    expect(props.onInspect).toHaveBeenCalledOnce();
  });

  it("requires recalculation even when an archive contains a signed, checked result", () => {
    render(<MissionWorkflow {...props} archived quality={{ status: "checked", issues: [] } as unknown as SurveyQualityReport} />);
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать план" }));
    expect(props.onCalculate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("link", { name: "К экспорту →" })).not.toBeInTheDocument();
  });

  it("keeps partial quality assumptions visible instead of announcing completed checks", () => {
    render(<MissionWorkflow {...props} quality={{ status: "partial", issues: [] } as unknown as SurveyQualityReport} />);
    expect(screen.getByText(/Проверка качества частичная/)).toBeVisible();
    expect(screen.getByRole("link", { name: "К проверке качества →" })).toHaveAttribute("href", "#mission-quality");
    expect(screen.queryByRole("link", { name: "К экспорту →" })).not.toBeInTheDocument();
  });
});
