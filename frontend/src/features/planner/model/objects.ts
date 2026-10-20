import type { ImportedZone } from "@/shared/api/types";
import type { FlightSiteState } from "./types";

export type ZoneLayerPatch = Partial<Pick<ImportedZone, "name" | "visible" | "locked">>;
export type SitePatch = Pick<FlightSiteState, "name" | "role" | "coordinate">;
