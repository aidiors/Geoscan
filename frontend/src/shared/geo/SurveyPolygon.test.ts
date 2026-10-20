import { describe, expect, it } from "vitest";
import { SurveyPolygon } from "./SurveyPolygon";

const p = (longitude: number, latitude: number) => ({ longitude: 37 + longitude / 1000, latitude: 55 + latitude / 1000 });

describe("survey contour validation", () => {
  it("closes a concave contour without changing the input", () => {
    const vertices = [p(0, 0), p(4, 0), p(2, 1), p(4, 4), p(0, 4)];
    const ring = SurveyPolygon.close(vertices);
    expect(ring).toHaveLength(6);
    expect(ring[0]).toEqual(ring.at(-1));
    expect(vertices).toHaveLength(5);
  });
  it("rejects crossings, touching edges and repeated vertices", () => {
    for (const vertices of [
      [p(0, 0), p(4, 4), p(0, 4), p(4, 0)],
      [p(0, 0), p(4, 0), p(2, 0), p(4, 4)],
      [p(0, 0), p(4, 0), p(4, 0), p(0, 4)],
    ]) expect(() => SurveyPolygon.close(vertices)).toThrow();
  });
  it("rejects an empty or negligible area", () => {
    expect(() => SurveyPolygon.close([])).toThrow();
    expect(() => SurveyPolygon.close([p(0, 0), p(.00001, 0), p(0, .00001)])).toThrow();
  });
});
