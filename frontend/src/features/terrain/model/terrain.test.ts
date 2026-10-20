import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { buildPlanRequest } from "@/features/planner/api/buildPlanRequest";
import { cloneFleet } from "@/features/planner/model/fleetCatalog";
import { cloneSites, scenarios } from "@/features/demos/data/scenarios";
import { defaultSurvey, planningSettings } from "@/features/planner/model/defaults";
import { usePlanner } from "@/features/planner/model/usePlanner";
import { TerrainRepository } from "../api/TerrainRepository";
import type { TerrainAsset } from "./types";

const asset: TerrainAsset = {
  name: "test.tif", content: "AQID", band: 1, bytes: 3, metresConfirmed: true, mslConfirmed: true,
  inspection: { sha256: "a".repeat(64), width: 2, height: 2, band_count: 1, band: 1, crs: "EPSG:4326", unit: "m",
    scale: 1, offset: 0, minimum_elevation_m: 100, maximum_elevation_m: 120, valid_fraction: 0.75, bounds_wgs84: [37, 55, 38, 56] },
};

describe("terrain planning workspace", () => {
  it("keeps optical AGL separate from MSL, requires confirmations and vertical limits", () => {
    const scenario = scenarios[0];
    const drone = cloneFleet().find((d) => d.id === "gemini")!;
    const settings = { ...planningSettings(100), altitudeReference: "agl" as const, flatGroundElevation: 500, terrainVerticalUncertainty: 7 };
    const build = (terrain: TerrainAsset | null = asset) => buildPlanRequest("flight", "rgb", scenario, [drone], cloneSites(scenario), [], defaultSurvey, settings, "scenario", terrain);
    expect(build()).toMatchObject({ altitude_reference: "agl", mission_altitude_m: 100, flat_ground_elevation_m: 0,
      terrain_source: { source_type: "geotiff", content: "AQID", band: 1 }, terrain_sampling_interval_m: 25, terrain_vertical_uncertainty_m: 7 });
    expect(() => build(null)).toThrow("подтвердите");
    expect(() => build({ ...asset, mslConfirmed: false })).toThrow("подтвердите");
    drone.maximumClimbRate = null;
    expect(() => build()).toThrow("пределы набора");
    drone.maximumClimbRate = 5;
    settings.terrainSamplingInterval = 0;
    expect(() => build()).toThrow("Шаг профиля");
  });

  it("roundtrips the exact terrain bytes, band, uncertainty and confirmations in a project", () => {
    const { result } = renderHook(usePlanner);
    act(() => result.current.handleTerrainChange(asset));
    act(() => result.current.setSettings((s) => ({ ...s, altitudeReference: "agl", terrainVerticalUncertainty: 7 })));
    const document = JSON.parse(JSON.stringify(result.current.captureProject()));
    act(() => result.current.handleScenarioChange(result.current.scenarioId));
    expect(result.current.terrain).toBeNull();
    act(() => result.current.restoreProject(document));
    expect(result.current.terrain).toEqual(asset);
    expect(result.current.settings.altitudeReference).toBe("agl");
    expect(result.current.settings.terrainVerticalUncertainty).toBe(7);
    document.editor_state.terrain.inspection.bounds_wgs84 = [0];
    expect(() => act(() => result.current.restoreProject(document))).toThrow("DEM");
    expect(result.current.terrain).toEqual(asset);
  });

  it("discards an upload that completes after changing the scenario", async () => {
    let complete!: (value: TerrainAsset) => void;
    const spy = vi.spyOn(TerrainRepository.prototype, "load").mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    try {
      const { result } = renderHook(usePlanner);
      let pending!: Promise<void>;
      act(() => { pending = result.current.handleImportTerrain(new File(["test"], "test.tif"), 1); });
      act(() => result.current.handleScenarioChange(scenarios[1].id));
      await act(async () => { complete(asset); await pending; });
      expect(result.current.terrain).toBeNull();
      expect(result.current.terrainImporting).toBe(false);
    } finally { spy.mockRestore(); }
  });
});
