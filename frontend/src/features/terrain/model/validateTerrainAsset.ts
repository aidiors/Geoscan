import { maximumTerrainFileBytes, type TerrainAsset } from "./types";

export function validateTerrainAsset(value: unknown): asserts value is TerrainAsset {
  const fail = () => { throw new Error("Повреждены данные DEM в проекте"); };
  if (!value || typeof value !== "object") return fail();
  const asset = value as TerrainAsset;
  if (typeof asset.name !== "string" || typeof asset.content !== "string"
    || !Number.isInteger(asset.bytes) || asset.bytes <= 0 || asset.bytes > maximumTerrainFileBytes
    || !Number.isInteger(asset.band) || asset.band < 1 || asset.band > 256
    || typeof asset.metresConfirmed !== "boolean" || typeof asset.mslConfirmed !== "boolean") return fail();
  // Bound the payload before decoding; preserve bytes exactly through portable JSON archives.
  if (asset.content.length !== Math.ceil(asset.bytes / 3) * 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(asset.content)) return fail();
  const info = asset.inspection;
  if (!info || typeof info !== "object" || info.band !== asset.band || typeof info.crs !== "string"
    || typeof info.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(info.sha256)
    || (info.unit !== null && typeof info.unit !== "string")) return fail();
  for (const key of ["width", "height", "band_count", "scale", "offset", "minimum_elevation_m", "maximum_elevation_m", "valid_fraction"] as const) {
    if (typeof info[key] !== "number" || !Number.isFinite(info[key])) return fail();
  }
  if (!Number.isInteger(info.width) || !Number.isInteger(info.height) || info.width < 1 || info.height < 1
    || info.width * info.height > 25_000_000 || info.band_count < asset.band
    || info.valid_fraction <= 0 || info.valid_fraction > 1 || info.scale === 0
    || info.minimum_elevation_m < -500 || info.maximum_elevation_m > 10000
    || info.minimum_elevation_m > info.maximum_elevation_m
    || !Array.isArray(info.bounds_wgs84) || info.bounds_wgs84.length !== 4
    || !info.bounds_wgs84.every((v) => typeof v === "number" && Number.isFinite(v))) return fail();
}
