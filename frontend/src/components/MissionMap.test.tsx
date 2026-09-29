import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TurnRadiusAnalysisResult } from "../types";
import { MissionMap } from "./MissionMap";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MissionMap", () => {
  it("shows a separate conditional route overlay", () => {
    vi.stubGlobal("ResizeObserver", class {
      observe(): void {}
      disconnect(): void {}
    });
    const radiusAnalysis: TurnRadiusAnalysisResult = {
      area_id: "field-1",
      scenarios: [
        { radius_m: 15, status: "feasible", message: "Проверено", distance_km: 2, uncovered_area_m2: 0 },
        { radius_m: 30, status: "feasible", message: "Проверено", distance_km: 2.1, uncovered_area_m2: 0 },
      ],
      common_survey_route: [
        { longitude: 37, latitude: 55 },
        { longitude: 37.01, latitude: 55.01 },
      ],
      message: "Маршрут условен.",
    };

    const { container } = render(<MissionMap zones={[]} sites={[]} drones={[]} plan={null} radiusAnalysis={radiusAnalysis} selectedDroneId="201" pendingSiteRole={null} onAddSite={vi.fn()} onClearZones={vi.fn()} />);

    expect(container.querySelector('polyline[stroke="#f97316"]')).not.toBeNull();
    expect(container).toHaveTextContent("Условный маршрут");
  });
});
