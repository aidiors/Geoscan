import { describe, expect, it } from "vitest";
import { mergeZones } from "./mergeZones";
import type { ImportedZone } from "@/shared/api/types";

describe("merge imported objects", () => {
  it("keeps distinct objects and constraints addressable across repeated file imports", () => {
    const original: ImportedZone = { zone_id: "a".repeat(64), name: "Field",
      zone_type: "work_area", geometry_type: "polygon", coordinates: [], interior_rings: [] };
    const constraint = { ...original, name: "Restriction", zone_type: "no_fly_zone" as const };
    const merged = mergeZones([original], [constraint, original]);
    expect(merged).toHaveLength(3);
    expect(new Set(merged.map((zone) => zone.zone_id)).size).toBe(3);
    expect(merged.every((zone) => zone.zone_id.length <= 64)).toBe(true);
    expect(merged[1].zone_type).toBe("no_fly_zone");
    expect(merged[1].metadata?.source_zone_id).toBe(original.zone_id);
    expect(constraint.zone_id).toBe(original.zone_id);
    expect(mergeZones(merged, [original])).toHaveLength(4);
  });
});
