import type { FlightSiteState } from "./types";

/** An empty list explicitly grants access to the entire fleet. */
export function assignedDroneIds(site: FlightSiteState): string[] {
  return site.assignedDroneIds ?? (site.assignedDroneId ? [site.assignedDroneId] : []);
}
