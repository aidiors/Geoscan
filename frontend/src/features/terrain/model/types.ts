export interface TerrainInspection {
  sha256: string;
  width: number;
  height: number;
  band_count: number;
  band: number;
  crs: string;
  unit: string | null;
  scale: number;
  offset: number;
  minimum_elevation_m: number;
  maximum_elevation_m: number;
  valid_fraction: number;
  bounds_wgs84: [number, number, number, number];
}

export interface TerrainAsset {
  name: string;
  content: string;
  band: number;
  bytes: number;
  inspection: TerrainInspection;
  metresConfirmed: boolean;
  mslConfirmed: boolean;
}

export const maximumTerrainFileBytes = 8 * 1024 * 1024;
