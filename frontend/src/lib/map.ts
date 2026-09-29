import type { Coordinate, MapView, Point } from "../types";

export const MAP_WIDTH = 900;
export const MAP_HEIGHT = 560;
export const TILE_SIZE = 256;
export const MIN_ZOOM = 3;
export const MAX_ZOOM = 18;

export interface MapSize {
  width: number;
  height: number;
}

export const DEFAULT_MAP_SIZE: MapSize = {
  width: MAP_WIDTH,
  height: MAP_HEIGHT,
};

export interface Tile {
  key: string;
  href: string;
  x: number;
  y: number;
}

export function project(coordinate: Coordinate, zoom: number): Point {
  const latitude = Math.max(Math.min(coordinate.latitude, 85.05112878), -85.05112878);
  const sine = Math.sin((latitude * Math.PI) / 180);
  const size = worldSize(zoom);
  return {
    x: ((coordinate.longitude + 180) / 360) * size,
    y: (0.5 - Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI)) * size,
  };
}

export function unproject(point: Point, zoom: number): Coordinate {
  const size = worldSize(zoom);
  const longitude = (point.x / size) * 360 - 180;
  const latitudeRadians = Math.atan(Math.sinh(Math.PI * (1 - (2 * point.y) / size)));
  return { longitude, latitude: (latitudeRadians * 180) / Math.PI };
}

export function coordinateToMapPoint(
  coordinate: Coordinate,
  view: MapView,
  size: MapSize = DEFAULT_MAP_SIZE,
): Point {
  const center = project(view.center, view.zoom);
  const point = project(coordinate, view.zoom);
  return {
    x: size.width / 2 + point.x - center.x,
    y: size.height / 2 + point.y - center.y,
  };
}

export function mapPointToCoordinate(
  point: Point,
  view: MapView,
  size: MapSize = DEFAULT_MAP_SIZE,
): Coordinate {
  const center = project(view.center, view.zoom);
  return unproject(
    {
      x: center.x + point.x - size.width / 2,
      y: center.y + point.y - size.height / 2,
    },
    view.zoom,
  );
}

export function zoomAtPoint(
  view: MapView,
  delta: number,
  point: Point,
  size: MapSize = DEFAULT_MAP_SIZE,
): MapView {
  const zoom = clampZoom(view.zoom + delta);
  if (zoom === view.zoom) {
    return view;
  }
  const coordinate = mapPointToCoordinate(point, view, size);
  const projected = project(coordinate, zoom);
  return {
    zoom,
    center: unproject(
      {
        x: projected.x - point.x + size.width / 2,
        y: projected.y - point.y + size.height / 2,
      },
      zoom,
    ),
  };
}

export function panView(view: MapView, startPoint: Point, currentPoint: Point): MapView {
  const center = project(view.center, view.zoom);
  return {
    ...view,
    center: unproject(
      {
        x: center.x + startPoint.x - currentPoint.x,
        y: center.y + startPoint.y - currentPoint.y,
      },
      view.zoom,
    ),
  };
}

export function fitCoordinates(
  coordinates: Coordinate[],
  size: MapSize = DEFAULT_MAP_SIZE,
): MapView {
  if (coordinates.length === 0) {
    return { center: { longitude: 37.409, latitude: 55.704 }, zoom: 12 };
  }
  const longitudes = coordinates.map((coordinate) => coordinate.longitude);
  const latitudes = coordinates.map((coordinate) => coordinate.latitude);
  const minimumLongitude = Math.min(...longitudes);
  const maximumLongitude = Math.max(...longitudes);
  const minimumLatitude = Math.min(...latitudes);
  const maximumLatitude = Math.max(...latitudes);
  const center = {
    longitude: (minimumLongitude + maximumLongitude) / 2,
    latitude: (minimumLatitude + maximumLatitude) / 2,
  };
  for (let zoom = MAX_ZOOM; zoom >= MIN_ZOOM; zoom -= 1) {
    const topLeft = project({ longitude: minimumLongitude, latitude: maximumLatitude }, zoom);
    const bottomRight = project({ longitude: maximumLongitude, latitude: minimumLatitude }, zoom);
    if (
      Math.abs(bottomRight.x - topLeft.x) < size.width * 0.72 &&
      Math.abs(bottomRight.y - topLeft.y) < size.height * 0.72
    ) {
      return { center, zoom };
    }
  }
  return { center, zoom: MIN_ZOOM };
}

export function visibleTiles(view: MapView, size: MapSize = DEFAULT_MAP_SIZE): Tile[] {
  const center = project(view.center, view.zoom);
  const minimumX = Math.floor((center.x - size.width / 2) / TILE_SIZE) - 1;
  const maximumX = Math.floor((center.x + size.width / 2) / TILE_SIZE) + 1;
  const minimumY = Math.max(0, Math.floor((center.y - size.height / 2) / TILE_SIZE) - 1);
  const tileCount = 2 ** view.zoom;
  const maximumY = Math.min(
    tileCount - 1,
    Math.floor((center.y + size.height / 2) / TILE_SIZE) + 1,
  );
  const tiles: Tile[] = [];
  for (let tileY = minimumY; tileY <= maximumY; tileY += 1) {
    for (let tileX = minimumX; tileX <= maximumX; tileX += 1) {
      const wrappedX = ((tileX % tileCount) + tileCount) % tileCount;
      tiles.push({
        key: `${view.zoom}/${tileX}/${tileY}`,
        href: `https://tile.openstreetmap.org/${view.zoom}/${wrappedX}/${tileY}.png`,
        x: tileX * TILE_SIZE - center.x + size.width / 2,
        y: tileY * TILE_SIZE - center.y + size.height / 2,
      });
    }
  }
  return tiles;
}

function worldSize(zoom: number): number {
  return TILE_SIZE * 2 ** zoom;
}

function clampZoom(zoom: number): number {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
}
