import { describe, expect, it } from "vitest";

import { demoCases } from "@/features/demos/data/demoCases";
import { scenarios } from "@/features/demos/data/scenarios";

describe("demoCases", () => {
  it("keeps every case connected to a real scenario and presentation evidence", () => {
    const scenarioIds = new Set(scenarios.map((scenario) => scenario.id));
    const caseIds = new Set(demoCases.map((demoCase) => demoCase.id));

    expect(caseIds.size).toBe(demoCases.length);
    expect(demoCases.length).toBeGreaterThanOrEqual(8);
    for (const demoCase of demoCases) {
      expect(scenarioIds.has(demoCase.scenarioId)).toBe(true);
      expect(demoCase.highlights.length).toBeGreaterThanOrEqual(3);
      expect(demoCase.expectedEvidence.length).toBeGreaterThanOrEqual(3);
    }
  });

  it("contains a deliberately infeasible diagnostic case", () => {
    const diagnostic = demoCases.find((demoCase) => demoCase.id === "infeasible-weather");

    expect(diagnostic?.settings?.windSpeed).toBeGreaterThan(20);
  });
});
