import type { ExportTextResponse, PlanResponse } from "@/shared/api/types";
import { requestBlob, requestJson } from "@/shared/api/http";
import { download } from "@/shared/browser/download";

export async function exportMission(
  plan: PlanResponse,
  droneId: string,
  format: "geojson" | "kml" | "qgroundcontrol" | "mission-planner",
): Promise<void> {
  const drone = plan.drone_plans.find((item) => item.drone_id === droneId);
  if ((format === "qgroundcontrol" || format === "mission-planner") && drone && drone.missions.length > 1) {
    const bundle = await requestBlob("/v1/exports/flight-package", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan, drone_id: droneId, format }),
    });
    download(bundle, "flight-package.zip", "application/zip");
    return;
  }
  const payload = await requestJson<ExportTextResponse | Record<string, unknown>>(
    `/v1/exports/${format}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan, drone_id: droneId }),
    },
  );
  if (format === "geojson") {
    download(JSON.stringify(payload, null, 2), `mission-${droneId}.geojson`, "application/geo+json");
    return;
  }
  const textExport = payload as ExportTextResponse;
  const mimeType =
    format === "kml"
      ? "application/vnd.google-earth.kml+xml"
      : format === "qgroundcontrol"
        ? "application/json"
        : "text/plain";
  download(textExport.content, textExport.file_name, mimeType);
}
