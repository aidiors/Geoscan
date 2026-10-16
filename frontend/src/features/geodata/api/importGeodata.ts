import type { ImportResponse, ZoneType } from "@/shared/api/types";
import { requestJson } from "@/shared/api/http";

export function importGeodata(
  fileName: string,
  zoneType: ZoneType,
  content: string,
): Promise<ImportResponse> {
  const format = fileName.toLowerCase().endsWith(".kml") ? "kml" : "geojson";
  return requestJson<ImportResponse>(`/v1/imports/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_file_name: fileName, zone_type: zoneType, content }),
  });
}
