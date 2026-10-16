import { describe, expect, it } from "vitest";

import {
  coordinateToMapPoint,
  mapPointToCoordinate,
  project,
  unproject,
  zoomAtPoint,
} from "@/shared/geo/map";

describe("map projection", () => {
  it("round-trips geographic coordinates", () => {
    const coordinate = { longitude: 37.6173, latitude: 55.7558 };

    const restored = unproject(project(coordinate, 14), 14);

    expect(restored.longitude).toBeCloseTo(coordinate.longitude, 8);
    expect(restored.latitude).toBeCloseTo(coordinate.latitude, 8);
  });

  it("keeps the coordinate below cursor while zooming", () => {
    const view = { center: { longitude: 37.61, latitude: 55.75 }, zoom: 11 };
    const cursor = { x: 712, y: 198 };
    const size = { width: 980, height: 620 };
    const coordinateBefore = mapPointToCoordinate(cursor, view, size);

    const zoomed = zoomAtPoint(view, 1, cursor, size);
    const pointAfter = coordinateToMapPoint(coordinateBefore, zoomed, size);

    expect(pointAfter.x).toBeCloseTo(cursor.x, 6);
    expect(pointAfter.y).toBeCloseTo(cursor.y, 6);
  });

  it("keeps geographic scale when the map changes aspect ratio", () => {
    const view = { center: { longitude: 37.61, latitude: 55.75 }, zoom: 13 };
    const coordinate = { longitude: 37.63, latitude: 55.77 };
    const wideSize = { width: 1_100, height: 560 };
    const phoneSize = { width: 360, height: 620 };

    const wideCenter = coordinateToMapPoint(view.center, view, wideSize);
    const widePoint = coordinateToMapPoint(coordinate, view, wideSize);
    const phoneCenter = coordinateToMapPoint(view.center, view, phoneSize);
    const phonePoint = coordinateToMapPoint(coordinate, view, phoneSize);

    expect(phonePoint.x - phoneCenter.x).toBeCloseTo(widePoint.x - wideCenter.x, 8);
    expect(phonePoint.y - phoneCenter.y).toBeCloseTo(widePoint.y - wideCenter.y, 8);
  });
});
