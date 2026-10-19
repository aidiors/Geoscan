import { describe, expect, it } from "vitest";
import { ImportedObstacleMapper } from "@/features/geodata/model/obstacles";
import type { ImportedZone } from "@/shared/api/types";

const mapper = new ImportedObstacleMapper();
const point: ImportedZone = { zone_id: "tower", name: "Tower", zone_type: "obstacle",
  geometry_type: "point", coordinates: [{ longitude: 37, latitude: 55 }], interior_rings: [] };

describe("obstacle elevation", () => {
  it("keeps a missing height unknown rather than replacing it by sea level", () => {
    expect(mapper.toRequest({ ...point, altitude_mode: "absolute" }).height_m).toBeNull();
    expect(mapper.requiresHorizontalAvoidance({ ...point, altitude_mode: "absolute" })).toBe(true);
  });
  it.each([0, -100, 200])("retains a known absolute top of %s metres", (height) => {
    const zone = { ...point, altitude_mode: "absolute",
      coordinates: [{ ...point.coordinates[0], altitude_m: height }] };
    expect(mapper.toRequest(zone).height_m).toBe(height);
    expect(mapper.requiresHorizontalAvoidance(zone)).toBe(false);
  });
  it("does not treat a partially missing top as a complete elevation", () => {
    const zone = { ...point, geometry_type: "line_string" as const, altitude_mode: "absolute",
      coordinates: [{ ...point.coordinates[0], altitude_m: 20 }, point.coordinates[0]] };
    expect(mapper.toRequest(zone).height_m).toBeNull();
  });
  it("retains relative heights but requires horizontal avoidance without terrain conversion", () => {
    const zone = { ...point, altitude_mode: "relativeToGround",
      coordinates: [{ ...point.coordinates[0], altitude_m: 20 }] };
    expect(mapper.toRequest(zone).height_m).toBe(20);
    expect(mapper.requiresHorizontalAvoidance(zone)).toBe(true);
  });
});
