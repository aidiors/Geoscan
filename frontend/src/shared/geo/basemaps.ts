export type Basemap = "streets" | "terrain" | "satellite";

export const basemaps = {
  streets: {
    maximumNativeZoom: 18,
    tileUrl: (zoom: number, x: number, y: number) =>
      `https://tile.openstreetmap.org/${zoom}/${x}/${y}.png`,
  },
  satellite: {
    maximumNativeZoom: 18,
    tileUrl: (zoom: number, x: number, y: number) =>
      `https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${zoom}/${y}/${x}`,
  },
  terrain: {
    maximumNativeZoom: 17,
    tileUrl: (zoom: number, x: number, y: number) =>
      `https://a.tile.opentopomap.org/${zoom}/${x}/${y}.png`,
  },
} as const;
