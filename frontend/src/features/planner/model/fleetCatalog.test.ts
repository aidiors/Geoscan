import { describe, expect, it } from "vitest";
import { cloneFleet, fleetCatalog } from "./fleetCatalog";

describe("physical fleet", () => {
  it("creates independent physical aircraft from a model without changing presets", () => {
    const original = cloneFleet();
    const added = fleetCatalog.create("201", 5, original, "thermal");
    expect(new Set(added.map((drone) => drone.id)).size).toBe(5);
    expect(added.every((drone) => drone.modelId === "201" && drone.speed === 70)).toBe(true);
    expect(added[0].tasks).not.toContain("thermal");
    added[0].payloads[0].supported_tasks.length = 0;
    expect(added[1].payloads[0].supported_tasks).toEqual(["rgb"]);
    expect(cloneFleet()).toEqual(original);
  });

  it("clones installed equipment and state without sharing mutable data or identity", () => {
    const fleet = cloneFleet();
    fleet[0].battery = 73;
    const copy = fleetCatalog.clone(fleet[0], fleet);
    expect(copy.id).not.toBe(fleet[0].id);
    expect(copy.modelId).toBe("201");
    expect(copy.battery).toBe(73);
    copy.tasks.push("video");
    expect(fleet[0].tasks).not.toContain("video");
  });

  it("validates total fleet size and keeps custom task capabilities consistent", () => {
    const fleet = cloneFleet();
    expect(() => fleetCatalog.create("201", 98, fleet, "rgb")).toThrow("100");
    expect(() => fleetCatalog.create("201", 1.5, fleet, "rgb")).toThrow();
    const [custom] = fleetCatalog.create("custom", 1, fleet, "lidar");
    expect(custom.tasks).toEqual(["lidar"]);
    expect(custom.payloads[0].supported_tasks).toEqual(["lidar"]);
  });
});
