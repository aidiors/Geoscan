import type { ImportedZone, PlanRequest } from "@/shared/api/types";

type Obstacle = PlanRequest["obstacles"][number];

export class ImportedObstacleMapper {
  toRequest(zone: ImportedZone): Obstacle {
    const elevations = zone.coordinates.map((point) => point.altitude_m).filter(
      (value): value is number => typeof value === "number" && Number.isFinite(value),
    );
    const top = elevations.length > 0 && elevations.length === zone.coordinates.length
      ? elevations.reduce((maximum, value) => Math.max(maximum, value), -Infinity)
      : null;
    const mode = zone.altitude_mode ?? "unknown";
    return {
      obstacle_id: zone.zone_id,
      geometry_type: zone.geometry_type,
      coordinates: zone.coordinates,
      height_m: top !== null && (top >= 0 || mode === "absolute") ? top : null,
      altitude_mode: mode,
      safety_radius_m: 30,
    };
  }

  requiresHorizontalAvoidance(zone: ImportedZone): boolean {
    return zone.altitude_mode !== "absolute" || zone.coordinates.length === 0 || zone.coordinates.some(
      (point) => typeof point.altitude_m !== "number" || !Number.isFinite(point.altitude_m),
    );
  }
}
