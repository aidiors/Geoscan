

export interface Coordinate {
  longitude: number;
  latitude: number;
  altitude_m?: number | null;
}

export interface MapView {
  center: Coordinate;
  zoom: number;
}

export interface Point {
  x: number;
  y: number;
}
