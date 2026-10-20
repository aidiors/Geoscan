export type Basemap = "streets" | "terrain";

export const basemaps = {
  streets: {
    maximumNativeZoom: 18,
    tileUrl: (zoom: number, x: number, y: number) =>
      `https://tile.openstreetmap.org/${zoom}/${x}/${y}.png`,
  },
  terrain: {
    maximumNativeZoom: 17,
    tileUrl: (zoom: number, x: number, y: number) =>
      `https://a.tile.opentopomap.org/${zoom}/${x}/${y}.png`,
  },
} as const;
