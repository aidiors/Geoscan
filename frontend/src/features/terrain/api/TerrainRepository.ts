import { requestJson } from "@/shared/api/http";
import { maximumTerrainFileBytes, type TerrainAsset, type TerrainInspection } from "../model/types";

export class TerrainRepository {
  async load(file: File, band: number): Promise<TerrainAsset> {
    if (file.size === 0 || file.size > maximumTerrainFileBytes) throw new Error("DEM должен быть непустым GeoTIFF до 8 МиБ. Обрежьте растр до района работ и всех перелётов.");
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Не удалось прочитать DEM"));
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.readAsDataURL(file);
    });
    const inspection = await requestJson<TerrainInspection>("/v1/terrain/inspect", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_type: "geotiff", content, band }),
    });
    return { name: file.name, bytes: file.size, content, band, inspection, metresConfirmed: false, mslConfirmed: false };
  }
}
