import { describe, expect, it } from "vitest";

import { radiusSamples } from "./radii";

describe("radius samples", () => {
  it("keeps zero as a single unconstrained scenario", () => {
    expect(radiusSamples(0, 0)).toEqual([0]);
  });

  it("includes zero in a comparison range", () => {
    expect(radiusSamples(0, 40)).toEqual([0, 20, 40]);
  });

  it("accepts one explicit positive radius", () => {
    expect(radiusSamples(15, 15)).toEqual([15]);
  });
});
