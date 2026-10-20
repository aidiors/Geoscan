import type { Coordinate, Point } from "./types";

/** Validates a local survey contour before it enters the planning request. */
export class SurveyPolygon {
  static close(vertices: Coordinate[]): Coordinate[] {
    if (vertices.length < 3) throw new Error("Укажите хотя бы три вершины.");
    if (vertices.length > 500) throw new Error("В одном контуре допускается до 500 вершин.");
    if (vertices.some((p) => !Number.isFinite(p.latitude) || !Number.isFinite(p.longitude)
      || Math.abs(p.latitude) > 85 || Math.abs(p.longitude) > 180)) throw new Error("Контур за пределами поддерживаемой карты.");
    const origin = vertices[0];
    const scale = Math.cos(origin.latitude * Math.PI / 180);
    const points = vertices.map((p) => ({ x: (p.longitude - origin.longitude) * 111_195 * scale, y: (p.latitude - origin.latitude) * 111_195 }));
    const cross = (a: Point, b: Point, c: Point) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    const on = (a: Point, b: Point, p: Point) => Math.abs(cross(a, b, p)) < 1e-7
      && p.x >= Math.min(a.x, b.x) - 1e-7 && p.x <= Math.max(a.x, b.x) + 1e-7
      && p.y >= Math.min(a.y, b.y) - 1e-7 && p.y <= Math.max(a.y, b.y) + 1e-7;
    for (let i = 0; i < points.length; i++) {
      const a = points[i], b = points[(i + 1) % points.length];
      if (Math.hypot(a.x - b.x, a.y - b.y) < 0.01) throw new Error("Соседние вершины совпадают.");
      const c = points[(i + 2) % points.length];
      if (on(a, b, c) || on(b, c, a)) throw new Error("Стороны контура накладываются друг на друга.");
      for (let j = i + 2; j < points.length; j++) {
        if (i === 0 && j === points.length - 1) continue;
        const c = points[j], d = points[(j + 1) % points.length];
        if ((cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0)
          || on(a, b, c) || on(a, b, d) || on(c, d, a) || on(c, d, b)) throw new Error("Контур пересекает сам себя. Исправьте вершины.");
      }
    }
    const area = Math.abs(points.reduce((sum, p, i) => {
      const next = points[(i + 1) % points.length];
      return sum + p.x * next.y - next.x * p.y;
    }, 0)) / 2;
    if (area < 1) throw new Error("Площадь контура должна быть не меньше 1 м².");
    return [...vertices.map((p) => ({ ...p })), { ...origin }];
  }
}
